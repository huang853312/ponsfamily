import asyncio
import logging
from datetime import datetime, timezone
from web3 import Web3

from analyzers.classifier import classify
from analyzers.cluster import grade, novelty_score, RANK
from analyzers.contracts import ContractInspector
from analysis.code_novelty import fingerprint
from analysis.deployer_intel import assess
from analysis.opportunity_score import calculate,NO_DATA

log=logging.getLogger(__name__)
ERC20_ABI=[{"type":"function","name":"name","stateMutability":"view","inputs":[],"outputs":[{"type":"string"}]},{"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"type":"string"}]}]

class Pipeline:
    def __init__(self,db,telegram,known,tracer,observation_minutes,rpc_limiter=None):
        self.db,self.telegram,self.known,self.tracer=db,telegram,known,tracer
        self.minutes=observation_minutes; self.rpc_limiter=rpc_limiter;self.inspector=ContractInspector(rpc_limiter); self.token_cache={}
    async def _token_meta(self,w3,address):
        if address in self.token_cache:return self.token_cache[address]
        c=w3.eth.contract(address=Web3.to_checksum_address(address),abi=ERC20_ABI)
        try:name=await self.rpc_limiter.call(c.functions.name().call()) if self.rpc_limiter else await c.functions.name().call()
        except Exception:name="Unknown"
        try:symbol=await self.rpc_limiter.call(c.functions.symbol().call()) if self.rpc_limiter else await c.functions.symbol().call()
        except Exception:symbol="Unknown"
        self.token_cache[address]=(name,symbol); return name,symbol
    async def _contract(self,w3,cid,address,creator,block,role_hint,topics,tx_hash,features):
        info=await self.inspector.inspect(w3,address)
        result=classify(info["selectors"],is_minimal_proxy=info["is_minimal_proxy"],implementation=info["implementation"],role_hint=role_hint)
        fresh=await self.db.save_contract(address,creator,info,result,block,topics); features.update({k:features.get(k,False) or v for k,v in fresh.items()})
        code_fingerprint=fingerprint(info["runtime_code"],info["selectors"],topics,info["implementation"],info["is_minimal_proxy"],await self.db.known_fingerprints())
        await self.db.save_fingerprint(address,code_fingerprint);features["unknown_structure"]=code_fingerprint.template_status=="UNKNOWN_STRUCTURE"
        _,new_role=await self.db.add_member(cid,address,result.label,block); features[result.label]=features.get(result.label,False) or new_role
        if info["implementation"]:
            impl=info["implementation"]; impl_info=await self.inspector.inspect(w3,impl)
            impl_result=classify(impl_info["selectors"],role_hint="Implementation")
            impl_fresh=await self.db.save_contract(impl,creator,impl_info,impl_result,block,())
            impl_fingerprint=fingerprint(impl_info["runtime_code"],impl_info["selectors"],(),impl_info["implementation"],impl_info["is_minimal_proxy"],await self.db.known_fingerprints())
            await self.db.save_fingerprint(impl,impl_fingerprint)
            features.update({k:features.get(k,False) or v for k,v in impl_fresh.items()})
            impl_role=impl_result.label if impl_result.label!="Unknown" else "Implementation"
            _,new_impl=await self.db.add_member(cid,impl,impl_role,block); features["Implementation"]=features.get("Implementation",False) or new_impl
            await self.db.relationship(cid,address,impl,"proxy_implementation",tx_hash,"EIP-1967 slot or EIP-1167 runtime target")
        return result
    async def handle(self,p,w3):
        if await self.db.pool_exists(p["address"]):return
        cid,new_deployer=await self.db.get_or_create_cluster("deployer:"+p["creator"],p["creator"],p["block_number"],self.minutes)
        features={"deployer":new_deployer}; results=[]; metadata=[]
        pool_role="Pool"; await self.db.add_member(cid,p["address"],pool_role,p["block_number"])
        factory_role="Infrastructure" if p["dex_version"]=="V4" else "DEX Factory"
        await self.db.add_member(cid,p["factory"],factory_role,p["block_number"])
        await self.db.relationship(cid,p["factory"],p["address"],"created_pool",p["tx_hash"],f"{p['dex_version']} creation event")
        topics=(p.get("event_topic") or "",)
        for address in (p["token0"],p["token1"]):
            result=await self._contract(w3,cid,address,p["creator"],p["block_number"],None,topics,p["tx_hash"],features); results.append(result)
            await self.db.relationship(cid,p["address"],address,"same_transaction",p["tx_hash"],"token address is encoded in this pool creation event")
            name,symbol=await self._token_meta(w3,address); metadata.append((address,name,symbol))
            await self.db.db.execute("INSERT OR IGNORE INTO tokens(address,name,symbol,creator,first_seen) VALUES(?,?,?,?,?)",(address,name,symbol,p["creator"],p["block_number"]))
        for address,role in p.get("related",[]):
            results.append(await self._contract(w3,cid,address,p["creator"],p["block_number"],role,topics,p["tx_hash"],features))
            await self.db.relationship(cid,p["address"],address,"same_transaction",p["tx_hash"],f"{role} address is encoded in this pool creation event")
        trace=await self.tracer.trace(w3,p["tx_hash"])
        await self.db.observation(cid,"trace_status",p["creator"],p["block_number"],{"status":trace["status"],"error":trace.get("error")})
        for creation in trace["creations"]:
            result=await self._contract(w3,cid,creation["address"],creation["creator"] or p["creator"],p["block_number"],None,topics,p["tx_hash"],features); results.append(result)
            await self.db.relationship(cid,creation["creator"] or p["creator"],creation["address"],"created_by",p["tx_hash"],creation["type"])
        await self.db.save_pool(p)
        matches=[self.known.get(a) for a in [p["creator"],p["factory"],p["token0"],p["token1"]]+[x[0] for x in p.get("related",[])] if self.known.get(a)]
        for match in matches: await self.db.known_match(match.address,match)
        snapshot=await self.db.cluster_snapshot(cid); level,reason=grade(snapshot,matches[0] if matches else None,set(self.known.entries))
        old=await self.db.set_level(cid,level); await self.db.db.commit()
        if level=="SILENT":log.info("Silenced mature-system pool %s: %s",p["address"],reason);return
        upgrade=RANK.get(level,0)>RANK.get(old,0) and old not in {level,"SILENT"}
        if old==level and old!="B":return
        score=novelty_score(features)
        opportunity=calculate({"protocol_structure":{"B":5,"A":18,"S":25}[level],"code_novelty":min(15,score//7),"dev_quality":NO_DATA,"capital_growth":NO_DATA,"real_usage":NO_DATA,"smart_money":NO_DATA,"earlyness":NO_DATA})
        await self.db.save_opportunity_score(cid,level,opportunity)
        message=self._message(level,old if upgrade else None,cid,p,metadata,results,features,score,matches,reason,trace["status"],snapshot,opportunity)
        key=f"cluster:{cid}:level:{level}"
        if not await self.db.reserve_alert(key,cid,level,message):return
        try:
            message_id=await self.telegram.send(message)
            if message_id is None:log.warning("Telegram disabled; alert persisted but not delivered")
            else:await self.db.mark_alert_sent(key,message_id)
        except Exception:
            await self.db.release_alert(key);await self.db.release_pool(p["address"]);raise
    async def handle_contract(self,event,w3):
        """Feed a verified top-level/internal creation into the same V1 analysis path."""
        if await self.db.contract_discovery_exists(event["tx_hash"],event["address"]):return
        creator=event["creator"].lower();address=event["address"].lower()
        cid,new_deployer=await self.db.get_or_create_cluster("deployer:"+creator,creator,event["block_number"],self.minutes)
        features={"deployer":new_deployer};results=[]
        result=await self._contract(w3,cid,address,creator,event["block_number"],None,(),event["tx_hash"],features);results.append(result)
        await self.db.relationship(cid,creator,address,"created_by",event["tx_hash"],event["creation_type"])
        await self.db.observation(cid,"trace_status",address,event["block_number"],{"status":event["trace_status"]})
        matches=[m for candidate in (creator,address) if (m:=self.known.get(candidate))]
        for match in matches:await self.db.known_match(match.address,match)
        snapshot=await self.db.cluster_snapshot(cid);level,reason=grade(snapshot,matches[0] if matches else None,set(self.known.entries))
        old=await self.db.set_level(cid,level)
        await self.db.save_deployer_event(creator,address,cid,result.label,level,event["block_number"],event["tx_hash"])
        dev=assess(**await self.db.deployer_stats(creator))
        if level=="SILENT":
            await self.db.save_contract_discovery(event);log.info("Silenced GMGN-confirmed contract %s: %s",address,reason);return
        upgrade=RANK.get(level,0)>RANK.get(old,0) and old not in {level,"SILENT"}
        score=novelty_score(features)
        opportunity=calculate({"protocol_structure":{"B":5,"A":18,"S":25}[level],"code_novelty":min(15,score//7),"dev_quality":dev.score if dev.score is not None else NO_DATA,"capital_growth":NO_DATA,"real_usage":NO_DATA,"smart_money":NO_DATA,"earlyness":NO_DATA},dev.risk_flags,dev.evidence)
        await self.db.save_opportunity_score(cid,level,opportunity)
        message=self._contract_message(level,old if upgrade else None,cid,event,result,features,score,matches,reason,snapshot,opportunity,dev)
        key=f"cluster:{cid}:level:{level}"
        if not await self.db.reserve_alert(key,cid,level,message):
            await self.db.save_contract_discovery(event);return
        try:
            message_id=await self.telegram.send(message)
            if message_id is None:log.warning("Telegram disabled; contract alert persisted but not delivered")
            else:await self.db.mark_alert_sent(key,message_id)
            await self.db.save_contract_discovery(event)
        except Exception:await self.db.release_alert(key);raise
    def _contract_message(self,level,old,cid,event,result,features,score,matches,reason,snapshot,opportunity,dev):
        icon={"S":"🔥","A":"🚨","B":"🟡"}[level];now=datetime.now(timezone.utc).isoformat()
        title=("🔥 项目升级：A → S" if old=="A" and level=="S" else f"⬆️ 项目升级：{old} → {level}" if old else f"{icon} {level}级 Robinhood Radar")
        implementation=next((r["target"] for r in snapshot["relationships"] if r["relation"]=="proxy_implementation"),"Unknown / Not observed")
        first="\n".join(f"- New {k}: {'YES' if v else 'NO'}" for k,v in sorted(features.items()))
        return (f"{title}\n\n发现时间：{now}\n项目 Cluster：{cid}\n初次发现：{snapshot['window']['started_at']}\n当前升级时间：{now if old else 'Not applicable'}\n\n"
          f"Token：Not observed\nPool：Not observed\nDEX：Not observed\n\nDeployer：{event['creator']}\nContract：{event['address']}\nProxy/Implementation：{implementation}\n"
          f"初步类型：{result.label}\n置信度：{result.confidence:.2f}\nNovelty Score：{score}/100\nOpportunity：{opportunity.total}/{opportunity.available_max} available points\nScore Confidence：{opportunity.confidence}\nDEV：{dev.status}; {', '.join(dev.risk_flags) or 'No verified risk flag'}\nCapital/Usage/Smart Money：NO_DATA\n\n首次出现：\n{first}\n\n"
          f"GMGN Known System：{', '.join(sorted({m.system for m in matches})) if matches else 'No'}\n是否突破过滤：{'YES' if matches and level!='SILENT' else 'NO'}\nTrace status：{event['trace_status']}\n\n"
          f"判断理由：{reason}\nEvidence：{'; '.join(result.evidence)}\n\n交易：{event['tx_hash']}\n区块：{event['block_number']}")
    def _message(self,level,old,cid,p,metadata,results,features,score,matches,reason,trace_status,snapshot,opportunity):
        icon={"S":"🔥","A":"🚨","B":"🟡"}[level]; now=datetime.now(timezone.utc).isoformat()
        title=("🔥 项目升级：A → S" if old=="A" and level=="S" else f"⬆️ 项目升级：{old} → {level}" if old else f"{icon} {level}级 Robinhood Radar")
        by_role={m["role"]:m["address"] for m in snapshot["members"]}
        observed=lambda role:by_role.get(role,"Unknown / Not observed")
        first="\n".join(f"- New {k}: {'YES' if v else 'NO'}" for k,v in sorted(features.items()))
        evidence="; ".join(e for r in results for e in r.evidence)
        return (f"{title}\n\n发现时间：{now}\n项目 Cluster：{cid}\n初次发现：{snapshot['window']['started_at']}\n当前升级时间：{now if old else 'Not applicable'}\n\n"
          f"Token：{' / '.join(f'{n} ({s}) {a}' for a,n,s in metadata)}\nPool：{p['address']}\nDEX：{p['dex_version']}\n\nDeployer：{p['creator']}\nFactory：{p['factory']}\nRouter：{observed('Router')}\nHook：{observed('Hook')}\nVault：{observed('Vault')}\nOracle：{observed('Oracle')}\nImplementation：{observed('Implementation')}\n\n"
          f"初步类型：{', '.join(r.label for r in results) or 'Unknown'}\n置信度：{max((r.confidence for r in results),default=0):.2f}\nNovelty Score：{score}/100\nOpportunity：{opportunity.total}/{opportunity.available_max} available points ({opportunity.confidence})\nCapital/Usage/Smart Money：NO_DATA\n\n首次出现：\n{first}\n\n"
          f"GMGN Known System：{', '.join(sorted({m.system for m in matches})) if matches else 'No'}\n是否突破过滤：{'YES' if matches and level!='SILENT' else 'NO'}\nTrace status：{trace_status}\n\n判断理由：{reason}\nEvidence：{evidence or 'Insufficient evidence'}\n\n交易：{p['tx_hash']}\n区块：{p['block_number']}")

class ObservationManager:
    def __init__(self,db,interval=30):self.db,self.interval=db,interval
    async def run(self):
        while True:
            count=await self.db.expire_windows()
            if count:log.info("Finalized %s observation windows",count)
            await asyncio.sleep(self.interval)
