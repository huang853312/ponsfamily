"""Verified activity accounting. Token flows are not TVL."""
import asyncio
import logging
from dataclasses import dataclass,field
from datetime import datetime,timezone
from web3 import AsyncWeb3,AsyncHTTPProvider
from analysis.opportunity_score import calculate,NO_DATA

log=logging.getLogger(__name__)

VERIFIED_ACTIONS={"0x6e553f65":"deposit","0xb6b55f25":"deposit","0x2e1a7d4d":"withdraw","0xa415bcad":"borrow","0x573ade81":"repay","0x022c0d9f":"swap"}

@dataclass
class ActivityAccumulator:
    native_value:int=0
    token_flow_count:int=0
    wallets:set=field(default_factory=set)
    tx_hashes:set=field(default_factory=set)
    verified_actions:dict=field(default_factory=dict)
    unknown_call_count:int=0
    def ingest(self,tx_hash,wallet,native_value,selector=None,token_flow=False):
        self.tx_hashes.add(tx_hash);self.wallets.add(wallet);self.native_value+=native_value
        self.token_flow_count+=int(token_flow)
        action=VERIFIED_ACTIONS.get(selector)
        if action:self.verified_actions[action]=self.verified_actions.get(action,0)+1
        elif selector:self.unknown_call_count+=1
    def snapshot(self):
        return {"native_value":self.native_value,"token_flow_count":self.token_flow_count,"unique_users":len(self.wallets),
            "tx_count":len(self.tx_hashes),"verified_action_counts":self.verified_actions,"unknown_call_count":self.unknown_call_count,
            "tvl":"UNKNOWN"}

TRANSFER_TOPIC="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BANDS=((85,"HOT"),(70,"HIGH"),(50,"WATCH"))

def score_activity_growth(snapshots):
    """Score actual cumulative deltas; elapsed time alone never earns capital points."""
    if len(snapshots)<2:return NO_DATA,NO_DATA,("fewer than two activity snapshots",)
    ordered=sorted(snapshots,key=lambda x:x["window_minutes"]);deltas=[];evidence=[]
    for previous,current in zip(ordered,ordered[1:]):
        delta=int(current["native_value"])-int(previous["native_value"]);deltas.append(delta)
        evidence.append(f"native {previous['window_minutes']}m={previous['native_value']}; native {current['window_minutes']}m={current['native_value']}; delta={delta}")
    positive=sum(delta>0 for delta in deltas);capital=min(20,positive*5+(5 if positive>=2 else 0))
    first,last=ordered[0],ordered[-1]
    user_growth=last["unique_users"]-first["unique_users"];tx_growth=last["tx_count"]-first["tx_count"]
    flow_growth=last["token_flow_count"]-first["token_flow_count"]
    usage=min(10,max(0,user_growth)*2+max(0,tx_growth)//2+min(2,max(0,flow_growth)))
    evidence.append(f"users delta={user_growth}; tx delta={tx_growth}; token-flow delta={flow_growth}; TVL=UNKNOWN")
    return capital,usage,tuple(evidence)

def opportunity_band(total):
    return next((name for threshold,name in BANDS if total>=threshold),None)

class ActivityWorker:
    """Bounded confirmed-block activity ingestion for active infrastructure candidates."""
    def __init__(self,settings,db,rpc_limiter,w3=None,clock=None,telegram=None):
        self.s,self.db,self.rpc_limiter=settings,db,rpc_limiter
        self.w3=w3 or AsyncWeb3(AsyncHTTPProvider(settings.rpc_url,request_kwargs={"timeout":30}))
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.telegram=telegram
    async def _call(self,awaitable):return await self.rpc_limiter.call(awaitable)
    async def run(self):
        delay=1
        while True:
            try:
                await self.sample();delay=1;await asyncio.sleep(self.s.activity_sample_interval)
            except asyncio.CancelledError:raise
            except Exception:
                log.exception("Activity worker failed; discovery monitors remain active; retrying in %ss",delay)
                await asyncio.sleep(delay);delay=min(delay*2,60)
    async def sample(self):
        head=await self._call(self.w3.eth.block_number)-self.s.confirmations
        candidates=await self.db.activity_candidates(self.s.max_activity_clusters)
        for candidate in candidates:await self._sample_cluster(candidate,head)
        return len(candidates)
    async def _sample_cluster(self,candidate,head):
        cid=candidate["id"];tracker=await self.db.activity_tracker(cid,head);addresses=await self.db.activity_addresses(cid)
        for number in range(tracker["last_block"]+1,head+1):
            block=await self._call(self.w3.eth.get_block(number,full_transactions=True))
            for tx in block["transactions"]:
                target=(tx.get("to") or "").lower()
                if target not in addresses:continue
                tx_hash=tx["hash"].hex();raw=tx.get("input",b"");hex_input=raw.hex() if hasattr(raw,"hex") else str(raw)
                selector=(hex_input if hex_input.startswith("0x") else "0x"+hex_input)[:10] if hex_input else None
                receipt=await self._call(self.w3.eth.get_transaction_receipt(tx["hash"]));flows=0
                if receipt.get("status",1)!=1:continue
                for event in receipt.get("logs",[]):
                    topics=event.get("topics",[])
                    if len(topics)>=3 and topics[0].hex().lower()==TRANSFER_TOPIC and ("0x"+topics[2].hex()[-40:]).lower() in addresses:flows+=1
                await self.db.save_activity_event(cid,tx_hash,tx["from"].lower(),number,int(tx.get("value",0)),selector,flows)
            await self.db.advance_activity_tracker(cid,number)
        await self._snapshots(cid,candidate["level"],tracker)
    async def _snapshots(self,cid,level,tracker):
        age=(self.clock()-datetime.fromisoformat(tracker["started_at"])).total_seconds()/60
        saved=await self.db.activity_windows_saved(cid);rows=await self.db.activity_aggregate(cid)
        accumulator=ActivityAccumulator()
        for row in rows:
            for _ in range(row["token_flow_count"]):token_flow=True;break
            else:token_flow=False
            accumulator.ingest(row["tx_hash"],row["wallet"],int(row["native_value"]),row["selector"],token_flow)
            if row["token_flow_count"]>1:accumulator.token_flow_count+=row["token_flow_count"]-1
        snapshot=accumulator.snapshot()
        for minutes in (5,15,30,60):
            if age>=minutes and minutes not in saved:await self.db.save_activity_snapshot(cid,self.clock().isoformat(),minutes,snapshot)
        snapshots=await self.db.activity_snapshots(cid);capital,usage,evidence=score_activity_growth(snapshots)
        if rows:
            previous=await self.db.latest_opportunity_score(cid);components=dict(previous["components"]) if previous else {}
            components.update({"protocol_structure":components.get("protocol_structure",{"B":5,"A":18,"S":25}.get(level,5)),"capital_growth":capital,"real_usage":usage})
            score=calculate(components,previous["risk_flags"] if previous else (),evidence)
            await self._opportunity_alert(cid,level,previous,score,evidence,snapshots)
            await self.db.save_opportunity_score(cid,level,score)
        if age>=60 and 60 in await self.db.activity_windows_saved(cid):await self.db.complete_activity_tracker(cid)
    async def _opportunity_alert(self,cid,level,previous,score,evidence,snapshots):
        if not self.telegram or level not in {"A","S"} or score.components["capital_growth"]==NO_DATA:return
        old_total=previous["total"] if previous else 0;old_band=opportunity_band(old_total);new_band=opportunity_band(score.total)
        if not new_band or new_band==old_band:return
        ranks={None:0,"WATCH":1,"HIGH":2,"HOT":3}
        if ranks[new_band]<=ranks[old_band]:return
        lines="\n".join(f"{x['window_minutes']}m native={x['native_value']} users={x['unique_users']} tx={x['tx_count']} token_flows={x['token_flow_count']}" for x in snapshots)
        message=(f"🔥 Opportunity Upgrade\n\nStructural Level: {level}\nOpportunity: {old_total} -> {score.total}\nConfidence: {score.confidence}\nBand: {new_band}\n\nCapital / Usage:\n{lines}\n\nReason:\n"+"\n".join(evidence)+"\n\nTVL: UNKNOWN\nAction: MANUAL REVIEW")
        key=f"cluster:{cid}:opportunity:{new_band}"
        if not await self.db.reserve_alert(key,cid,f"OPPORTUNITY_{new_band}",message):return
        try:
            message_id=await self.telegram.send(message)
            if message_id is not None:await self.db.mark_alert_sent(key,message_id)
        except Exception:
            await self.db.release_alert(key);raise
