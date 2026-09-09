import asyncio
import json
import tempfile
import unittest
import os
from types import SimpleNamespace
from datetime import timedelta
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import patch

from eth_abi import encode
from hexbytes import HexBytes

from analyzers.classifier import classify
from analyzers.cluster import grade
from analyzers.contracts import ContractInspector, minimal_proxy_implementation
from analyzers.known_system_filter import KnownSystemFilter
from analyzers.deployer import DeployerTracer
from monitors import uniswap_v2,uniswap_v3,uniswap_v4
from monitors.contracts import ContractCreationMonitor
from pipeline import Pipeline
from analysis.activity import ActivityAccumulator,ActivityWorker,score_activity_growth
from analysis.code_novelty import fingerprint
from analysis.deployer_intel import assess
from analysis.opportunity_score import calculate,NO_DATA
from analysis.smart_money import evaluate
from config import Settings
from external_reference.dexscreener import DexScreenerReferenceProvider
from external_reference.gmgn import GMGNReferenceProvider
from external_reference.debot import DeBotReferenceProvider
from external_reference.social import SocialReferenceProvider
from rpc import RPCLimiter
from storage.database import Database

def topic_address(address):return HexBytes(bytes(12)+bytes.fromhex(address[2:]))

class CoreTests(unittest.TestCase):
    def setUp(self):self.known=KnownSystemFilter()
    def test_classifier_is_conservative_and_explainable(self):
        self.assertEqual(classify(["0x70a08231"]).label,"Unknown")
        result=classify(["0x18160ddd","0x70a08231","0xa9059cbb","0x23b872dd"])
        self.assertEqual(result.label,"ERC20");self.assertGreater(result.confidence,.9);self.assertTrue(result.evidence)
    def test_pons_and_pair_seed(self):
        self.assertEqual(self.known.get("0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e").system,"PONS")
        self.assertEqual(self.known.get("0x8660A7F019C7943b0b0A91B8E39AFf3b6DB6Ae62").system,"PAIR")
    def test_known_ordinary_silent_but_independent_core_breaks_filter(self):
        pons=self.known.get("0x3711cea4feade896c913c68f01eda97cb06d1a42")
        pair=self.known.get("0x8660A7F019C7943b0b0A91B8E39AFf3b6DB6Ae62")
        empty={"members":[],"relationships":[]}
        self.assertEqual(grade(empty,pons,set(self.known.entries))[0],"SILENT")
        self.assertEqual(grade(empty,pair,set(self.known.entries))[0],"B")
        project={"members":[{"address":"0xnew1","role":"Lending"}],"relationships":[]}
        self.assertEqual(grade(project,pons,set(self.known.entries))[0],"A")
    def test_b_to_a_and_a_to_s(self):
        known=set()
        b={"members":[],"relationships":[]}
        a={"members":[{"address":"a","role":"Oracle"}],"relationships":[]}
        s={"members":[{"address":"a","role":"Factory"},{"address":"b","role":"Router"},{"address":"c","role":"Vault"}],
           "relationships":[{"relation":"created_by"},{"relation":"proxy_implementation"}]}
        self.assertEqual((grade(b,None,known)[0],grade(a,None,known)[0]),("B","A"))
        self.assertEqual((grade(a,None,known)[0],grade(s,None,known)[0]),("A","S"))
        duplicated={"members":s["members"],"relationships":[{"relation":"created_by"},{"relation":"created_by"}]}
        self.assertNotEqual(grade(duplicated,None,known)[0],"S")
    def test_contract_only_core_and_non_core_grading(self):
        rel={"relationships":[{"relation":"created_by"}]}
        self.assertEqual(grade({"members":[{"address":"l","role":"Lending"}],**rel},None,set())[0],"A")
        self.assertEqual(grade({"members":[{"address":"o","role":"Oracle"}],**rel},None,set())[0],"A")
        self.assertEqual(grade({"members":[{"address":"e","role":"ERC20"}],**rel},None,set())[0],"B")
        self.assertEqual(grade({"members":[{"address":"p","role":"Proxy"}],**rel},None,set())[0],"B")
    def test_proxy_to_implementation_detection(self):
        impl=bytes.fromhex("11"*20);code=bytes.fromhex("363d3d373d3d3d363d73")+impl+bytes.fromhex("5af43d82803e903d91602b57fd5bf3")
        self.assertEqual(minimal_proxy_implementation(code),"0x"+"11"*20)
        class Eth:
            get_code=AsyncMock(return_value=code)
            get_storage_at=AsyncMock(return_value=bytes(32))
        class W3:eth=Eth()
        info=asyncio.run(ContractInspector().inspect(W3(),"0x"+"22"*20))
        self.assertTrue(info["is_minimal_proxy"]);self.assertEqual(info["implementation"],"0x"+"11"*20)
    def test_v2_v3_v4_decode(self):
        a="0x"+"11"*20;b="0x"+"22"*20;c="0x"+"33"*20
        log={"topics":[HexBytes(uniswap_v2.TOPIC),topic_address(a),topic_address(b)],"data":HexBytes(encode(["address","uint256"],[c,1]))}
        self.assertEqual(uniswap_v2.decode_log(log),(a,b,c,[]))
        log={"topics":[HexBytes(uniswap_v3.TOPIC),topic_address(a),topic_address(b),HexBytes((3000).to_bytes(32,"big"))],"data":HexBytes(encode(["int24","address"],[60,c]))}
        # Decoder data correctly includes only non-indexed tickSpacing and pool.
        self.assertEqual(uniswap_v3.decode_log(log),(a,b,c,[]))
        pool=HexBytes(bytes.fromhex("44"*32));hook="0x"+"55"*20
        log={"topics":[HexBytes(uniswap_v4.TOPIC),pool,topic_address(a),topic_address(b)],"data":HexBytes(encode(["uint24","int24","address","uint160","int24"],[3000,60,hook,2**96,0]))}
        self.assertEqual(uniswap_v4.decode_log(log),(a,b,"0x"+"44"*32,[(hook,"Hook")]))
    def test_database_init_dedupe_and_observation_recovery(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/"radar.db";db=await Database(path).open()
                tables={r[0] for r in await (await db.db.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
                self.assertTrue({"novelty_features","cluster_relationships","proxy_implementations","observation_windows","classification_evidence",
                    "contract_fingerprints","deployer_events","deployer_relations","cluster_activity_snapshots","wallet_profiles",
                    "wallet_cluster_entries","wallet_performance","external_first_seen","opportunity_scores"}<=tables)
                cid,_=await db.get_or_create_cluster("d:x","x",1,30);self.assertEqual(len(await db.active_windows()),1)
                await db.set_monitor_cursor("contracts",123);await db.set_monitor_cursor("pools",120)
                self.assertTrue(await db.reserve_alert("x",cid,"B","test"));self.assertFalse(await db.reserve_alert("x",cid,"B","test"));await db.close()
                reopened=await Database(path).open();windows=await reopened.active_windows()
                self.assertEqual(len(windows),1);self.assertEqual(windows[0]["cluster_id"],cid)
                self.assertEqual(await reopened.monitor_cursor("contracts"),123);self.assertEqual(await reopened.monitor_cursor("pools"),120);await reopened.close()
        asyncio.run(run())
    def test_pool_contract_dedupe_coexistence_and_window_boundary(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open();cid,_=await db.get_or_create_cluster("d:x","x",1,30)
                self.assertTrue(await db.reserve_alert(f"cluster:{cid}:level:B",cid,"B","pool"))
                self.assertFalse(await db.reserve_alert(f"cluster:{cid}:level:B",cid,"B","contract"))
                self.assertTrue(await db.reserve_alert(f"cluster:{cid}:level:A",cid,"A","upgrade"))
                self.assertFalse(await db.reserve_alert(f"cluster:{cid}:level:A",cid,"A","duplicate upgrade"))
                self.assertTrue(await db.reserve_alert(f"cluster:{cid}:level:S",cid,"S","upgrade"))
                self.assertFalse(await db.reserve_alert(f"cluster:{cid}:level:S",cid,"S","duplicate upgrade"))
                await db.db.execute("UPDATE observation_windows SET status='completed' WHERE cluster_id=?",(cid,));await db.db.commit()
                next_cid,_=await db.get_or_create_cluster("d:x","x",200,30)
                self.assertNotEqual(cid,next_cid);await db.close()
        asyncio.run(run())
    def test_top_level_creation_monitor(self):
        async def run():
            found=[];tx_hash=HexBytes("0x"+"01"*32)
            class Eth:
                async def get_block(self,number,full_transactions=False):
                    return {"timestamp":10,"transactions":[{"hash":tx_hash,"from":"0x"+"11"*20,"to":None}]}
                async def get_transaction_receipt(self,_):return {"contractAddress":"0x"+"22"*20}
            class Trace:enabled=False
            monitor=ContractCreationMonitor.__new__(ContractCreationMonitor);monitor.w3=type("W",(),{"eth":Eth()})();monitor.tracer=Trace()
            async def handler(event,_):found.append(event)
            monitor.handler=handler;await monitor._block(7)
            self.assertEqual(found[0]["creation_type"],"TOP_LEVEL_CREATE");self.assertEqual(found[0]["block_number"],7)
        asyncio.run(run())
    def test_internal_trace_available_and_unavailable(self):
        async def scenario(trace_result):
            found=[];tx_hash=HexBytes("0x"+"02"*32)
            class Eth:
                async def get_block(self,number,full_transactions=False):return {"timestamp":10,"transactions":[{"hash":tx_hash,"from":"0x"+"11"*20,"to":"0x"+"33"*20}]}
            class Trace:
                enabled=True
                async def trace(self,*_):return trace_result
            monitor=ContractCreationMonitor.__new__(ContractCreationMonitor);monitor.w3=type("W",(),{"eth":Eth()})();monitor.tracer=Trace()
            async def handler(event,_):found.append(event)
            monitor.handler=handler;await monitor._block(8);return found
        available={"status":"available","creations":[{"address":"0x"+"44"*20,"creator":"0x"+"33"*20,"type":"CREATE2"}]}
        self.assertEqual(asyncio.run(scenario(available))[0]["creation_type"],"CREATE2")
        self.assertEqual(asyncio.run(scenario({"status":"unavailable","creations":[],"error":"unsupported"})),[])
    def test_creation_relationships_are_not_polluted(self):
        async def run(code,proxy_address):
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open()
                implementation="0x"+"11"*20
                class Eth:
                    async def get_code(self,address):return code if address.lower()==proxy_address else b""
                    async def get_storage_at(self,address,slot):return bytes(32)
                class W3:eth=Eth()
                class Telegram:
                    async def send(self,message):return None
                pipeline=Pipeline(db,Telegram(),self.known,DeployerTracer(False),30)
                event={"tx_hash":"0xabc","address":proxy_address,"block_number":1,"creator":"0x"+"33"*20,
                       "creation_type":"TOP_LEVEL_CREATE","trace_status":"disabled","timestamp":1}
                await pipeline.handle_contract(event,W3())
                cid=(await (await db.db.execute("SELECT id FROM contract_clusters")).fetchone())[0]
                snapshot=await db.cluster_snapshot(cid);await db.close();return snapshot,implementation
        plain,unused=asyncio.run(run(b"\x00","0x"+"22"*20))
        self.assertEqual([r["relation"] for r in plain["relationships"]],["created_by"])
        impl=bytes.fromhex("11"*20);proxy_code=bytes.fromhex("363d3d373d3d3d363d73")+impl+bytes.fromhex("5af43d82803e903d91602b57fd5bf3")
        proxied,_=asyncio.run(run(proxy_code,"0x"+"22"*20))
        self.assertEqual({r["relation"] for r in proxied["relationships"]},{"created_by","proxy_implementation"})
        self.assertNotIn("same_transaction",{r["relation"] for r in proxied["relationships"]})
    def test_pool_event_records_real_same_transaction_relationship(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open()
                class Telegram:
                    async def send(self,message):return None
                pipeline=Pipeline(db,Telegram(),self.known,DeployerTracer(False),30)
                async def contract(w3,cid,address,creator,block,role_hint,topics,tx_hash,features):
                    await db.add_member(cid,address,role_hint or "ERC20",block);return classify(["0x18160ddd","0x70a08231","0xa9059cbb","0x23b872dd"])
                pipeline._contract=contract
                async def metadata(w3,address):return "Token","TKN"
                pipeline._token_meta=metadata
                p={"address":"0x"+"44"*20,"block_number":2,"timestamp":2,"tx_hash":"0xpool","dex_version":"V2",
                   "token0":"0x"+"55"*20,"token1":"0x"+"66"*20,"creator":"0x"+"77"*20,"factory":"0x"+"88"*20,"related":[],"event_topic":"0xtopic"}
                await pipeline.handle(p,None);cid=(await (await db.db.execute("SELECT id FROM contract_clusters")).fetchone())[0]
                snapshot=await db.cluster_snapshot(cid);await db.close();return snapshot
        snapshot=asyncio.run(run())
        relations=[r for r in snapshot["relationships"] if r["relation"]=="same_transaction"]
        self.assertEqual(len(relations),2);self.assertTrue(all("encoded in this pool creation event" in r["evidence"] for r in relations))
    def test_v4_pool_manager_is_not_launchpad_filter(self):
        manager="0x8366a39cc670b4001a1121b8f6a443a643e40951"
        self.assertIsNone(self.known.get(manager))
        self.assertEqual(grade({"members":[],"relationships":[]},None,set(self.known.entries))[0],"B")
    def test_template_fingerprint_exact_and_unknown(self):
        first=fingerprint(b"\x60\x00",("0x12345678",))
        self.assertEqual(first.template_status,"UNKNOWN_STRUCTURE")
        exact=fingerprint(b"\x60\x00",("0x12345678",),known=({"normalized_hash":first.normalized_hash,"selectors":first.selectors},))
        self.assertEqual(exact.template_status,"KNOWN_TEMPLATE");self.assertTrue(exact.evidence)
    def test_deployer_intelligence_is_evidence_based(self):
        new=assess(contracts_created=1,erc20_count=1,silent_count=0,high_grade_clusters=0)
        self.assertEqual(new.status,"UNKNOWN");self.assertFalse(new.risk_flags)
        mass=assess(contracts_created=12,erc20_count=11,silent_count=0,high_grade_clusters=0,max_same_template=8)
        self.assertIn("suspected spam deployer",mass.risk_flags)
        self.assertIn("mass clone",mass.risk_flags)
    def test_activity_metrics_do_not_invent_tvl_or_actions(self):
        activity=ActivityAccumulator();activity.ingest("tx1","wallet1",100,"0xdeadbeef",True)
        activity.ingest("tx2","wallet2",50,"0x6e553f65",False);activity.ingest("tx3","wallet1",0,None,False)
        result=activity.snapshot()
        self.assertEqual(result["unique_users"],2);self.assertEqual(result["tx_count"],3)
        self.assertEqual(result["unknown_call_count"],1);self.assertEqual(result["verified_action_counts"],{"deposit":1})
        self.assertEqual(result["tvl"],"UNKNOWN")
    def test_activity_and_wallet_persistence(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open();snapshot=ActivityAccumulator().snapshot()
                await db.save_activity_snapshot(1,"2026-01-01T00:00:00Z",5,snapshot)
                row=await (await db.db.execute("SELECT * FROM cluster_activity_snapshots")).fetchone()
                self.assertEqual(row["tvl_status"],"UNKNOWN")
                profile=await db.wallet_profile("0xwallet");self.assertEqual(profile["status"],"UNKNOWN");await db.close()
        asyncio.run(run())
        smart=evaluate([]);self.assertEqual(smart.score,"NO_DATA");self.assertFalse(smart.confirmation)
    def test_external_providers_are_explicit_no_data(self):
        async def run():
            providers=[DexScreenerReferenceProvider(),GMGNReferenceProvider(),DeBotReferenceProvider(),SocialReferenceProvider()]
            return await asyncio.gather(*(asyncio.wait_for(p.lookup(tuple()),.1) for p in providers))
        results=asyncio.run(run());self.assertTrue(all(x.status=="NO_DATA" for x in results))
        self.assertEqual(grade({"members":[],"relationships":[]},None,set())[0],"B")
    def test_opportunity_no_data_is_not_zero_or_structural_level(self):
        score=calculate({"protocol_structure":0,"code_novelty":NO_DATA,"dev_quality":NO_DATA,"capital_growth":NO_DATA,
                         "real_usage":NO_DATA,"smart_money":NO_DATA,"earlyness":NO_DATA})
        self.assertEqual(score.components["protocol_structure"],0);self.assertEqual(score.components["code_novelty"],"NO_DATA")
        self.assertEqual(score.total,0);self.assertEqual(score.available_max,25);self.assertEqual(score.confidence,"LOW")
        structural=grade({"members":[{"address":"a","role":"Factory"},{"address":"b","role":"Router"},{"address":"c","role":"Vault"}],
                          "relationships":[{"relation":"created_by"},{"relation":"proxy_implementation"}]},None,set())[0]
        self.assertEqual(structural,"S");self.assertEqual(score.total,0)
    def test_resource_limits_reject_unsafe_or_unenforceable_values(self):
        base={"RPC_URL":"http://localhost","CONTRACT_MONITOR_ENABLED":"true","MAX_RPC_CONCURRENCY":"4","MAX_TRACE_CONCURRENCY":"1","MAX_ACTIVITY_CLUSTERS":"10"}
        with patch.dict(os.environ,base,clear=True):self.assertEqual(Settings.load().max_rpc_concurrency,4)
        with patch.dict(os.environ,{**base,"MAX_TRACE_CONCURRENCY":"3"},clear=True):
            with self.assertRaises(ValueError):Settings.load()
    def test_shared_rpc_limiter_enforces_peak(self):
        async def run():
            limiter=RPCLimiter(2)
            async def request():
                async with limiter.slot():await asyncio.sleep(.01)
            await asyncio.gather(*(request() for _ in range(8)));return limiter.peak
        self.assertEqual(asyncio.run(run()),2)
    def test_activity_worker_candidates_limits_snapshots_restart_and_score(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open()
                for index in range(3):
                    cid,_=await db.get_or_create_cluster(f"d:{index}",f"deployer{index}",index+1,90)
                    await db.add_member(cid,f"0x{index+1:040x}","Lending",index+1);await db.set_level(cid,"A")
                    if index==0:target_cid=cid
                class Eth:
                    head=10
                    @property
                    def block_number(self):
                        async def value():return self.head
                        return value()
                    async def get_block(self,number,full_transactions=False):
                        return {"transactions":[{"hash":HexBytes("0x"+"ab"*32),"from":"0x"+"99"*20,
                            "to":"0x"+f"{1:040x}","value":100,"input":HexBytes("0x6e553f65")}]}
                    async def get_transaction_receipt(self,tx):return {"logs":[]}
                eth=Eth();w3=type("W",(),{"eth":eth})();now=[datetime.now(timezone.utc)]
                settings=SimpleNamespace(rpc_url="",confirmations=0,max_activity_clusters=2,activity_sample_interval=.01)
                worker=ActivityWorker(settings,db,RPCLimiter(1),w3,lambda:now[0])
                self.assertEqual(await worker.sample(),2)
                trackers=await (await db.db.execute("SELECT * FROM activity_trackers")).fetchall();self.assertEqual(len(trackers),2)
                first=dict(await (await db.db.execute("SELECT * FROM activity_trackers WHERE cluster_id=?",(target_cid,))).fetchone());eth.head=11;now[0]=datetime.fromisoformat(first["started_at"])+timedelta(minutes=5)
                await worker.sample()
                for minute in (15,30):now[0]=datetime.fromisoformat(first["started_at"])+timedelta(minutes=minute);await worker.sample()
                await db.db.execute("UPDATE observation_windows SET status='completed' WHERE cluster_id=?",(first["cluster_id"],));await db.db.commit()
                now[0]=datetime.fromisoformat(first["started_at"])+timedelta(minutes=60);await worker.sample()
                windows={r[0] for r in await (await db.db.execute("SELECT window_minutes FROM cluster_activity_snapshots WHERE cluster_id=?",(first["cluster_id"],))).fetchall()}
                self.assertEqual(windows,{5,15,30,60})
                latest=await (await db.db.execute("SELECT components FROM opportunity_scores WHERE cluster_id=? ORDER BY id DESC LIMIT 1",(first["cluster_id"],))).fetchone()
                components=json.loads(latest[0]);self.assertIsInstance(components["capital_growth"],int);self.assertIsInstance(components["real_usage"],int)
                tracker_done=await (await db.db.execute("SELECT * FROM activity_trackers WHERE cluster_id=?",(first["cluster_id"],))).fetchone();self.assertEqual(tracker_done["status"],"completed")
                eth.head=12;await worker.sample();unchanged=await (await db.db.execute("SELECT last_block FROM activity_trackers WHERE cluster_id=?",(first["cluster_id"],))).fetchone();self.assertEqual(unchanged[0],11)
                last_block=first["last_block"];await db.close();reopened=await Database(Path(d)/"radar.db").open()
                restored=await (await reopened.db.execute("SELECT * FROM activity_trackers WHERE cluster_id=?",(first["cluster_id"],))).fetchone()
                self.assertGreaterEqual(restored["last_block"],last_block);await reopened.close()
        asyncio.run(run())
    def test_capital_growth_uses_deltas_not_elapsed_time(self):
        def snapshot(minute,native,users=1,txs=1):return {"window_minutes":minute,"native_value":str(native),"unique_users":users,"tx_count":txs,"token_flow_count":0}
        flat=score_activity_growth([snapshot(5,100),snapshot(15,100),snapshot(30,100)])
        growing=score_activity_growth([snapshot(5,100,1,1),snapshot(15,500,3,4),snapshot(30,2000,7,10)])
        single=score_activity_growth([snapshot(5,100)])
        self.assertEqual(flat[0],0);self.assertGreater(growing[0],flat[0]);self.assertGreater(growing[1],flat[1])
        self.assertEqual(single[0],NO_DATA);self.assertIn("native 5m=100; native 15m=500; delta=400",growing[2])
    def test_opportunity_band_alert_dedupe_upgrade_and_retry(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open();cid,_=await db.get_or_create_cluster("d","dev",1,30);await db.set_level(cid,"A")
                class Telegram:
                    def __init__(self):self.messages=[];self.fail=False
                    async def send(self,message):
                        if self.fail:raise RuntimeError("telegram down")
                        self.messages.append(message);return len(self.messages)
                telegram=Telegram();settings=SimpleNamespace(rpc_url="",confirmations=0,max_activity_clusters=1,activity_sample_interval=60)
                worker=ActivityWorker(settings,db,RPCLimiter(1),telegram=telegram)
                snapshots=[{"window_minutes":5,"native_value":"100","unique_users":1,"tx_count":1,"token_flow_count":0},
                           {"window_minutes":15,"native_value":"500","unique_users":3,"tx_count":4,"token_flow_count":1}]
                evidence=("native 5m=100; native 15m=500; delta=400",)
                def score(total):
                    values={"protocol_structure":25,"code_novelty":15,"dev_quality":15,"capital_growth":min(20,max(0,total-55)),
                            "real_usage":max(0,min(10,total-75)),"smart_money":NO_DATA,"earlyness":max(0,total-85)}
                    return calculate(values)
                await worker._opportunity_alert(cid,"A",{"total":69},score(71),evidence,snapshots)
                await worker._opportunity_alert(cid,"A",{"total":71},score(72),evidence,snapshots)
                await worker._opportunity_alert(cid,"A",{"total":84},score(86),evidence,snapshots)
                self.assertEqual(len(telegram.messages),2);self.assertIn("Band: HIGH",telegram.messages[0]);self.assertIn("Band: HOT",telegram.messages[1])
                self.assertEqual((await (await db.db.execute("SELECT level FROM contract_clusters WHERE id=?",(cid,))).fetchone())[0],"A")
                telegram.fail=True
                with self.assertRaises(RuntimeError):await worker._opportunity_alert(cid,"A",{"total":49},score(50),evidence,snapshots)
                telegram.fail=False;await worker._opportunity_alert(cid,"A",{"total":49},score(50),evidence,snapshots)
                self.assertEqual(len(telegram.messages),3);await db.close()
        asyncio.run(run())
    def test_activity_worker_failure_isolated_and_no_data_without_events(self):
        async def run():
            class BrokenEth:
                @property
                def block_number(self):
                    async def fail():raise RuntimeError("rpc down")
                    return fail()
            settings=SimpleNamespace(rpc_url="",confirmations=0,max_activity_clusters=1,activity_sample_interval=.01)
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open();worker=ActivityWorker(settings,db,RPCLimiter(1),type("W",(),{"eth":BrokenEth()})())
                task=asyncio.create_task(worker.run());await asyncio.sleep(.03)
                self.assertFalse(task.done());task.cancel();await asyncio.gather(task,return_exceptions=True)
                self.assertEqual((await (await db.db.execute("SELECT COUNT(*) FROM opportunity_scores")).fetchone())[0],0);await db.close()
        asyncio.run(run())

if __name__=="__main__":unittest.main()
