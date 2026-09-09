import logging
from datetime import datetime, timezone
from web3 import Web3

from analyzers.contracts import inspect_contract
from analyzers.classifier import classify
from analyzers.cluster import score

log=logging.getLogger(__name__)
ERC20_ABI=[{"type":"function","name":"name","stateMutability":"view","inputs":[],"outputs":[{"type":"string"}]},{"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"type":"string"}]}]

class Pipeline:
    def __init__(self,db,telegram,known): self.db,self.telegram,self.known=db,telegram,known
    async def _token_meta(self,w3,address):
        c=w3.eth.contract(address=Web3.to_checksum_address(address),abi=ERC20_ABI)
        try: name=await c.functions.name().call()
        except Exception: name="?"
        try: symbol=await c.functions.symbol().call()
        except Exception: symbol="?"
        return name,symbol
    async def handle(self,p,w3):
        if await self.db.pool_exists(p["address"]): return
        labels=[]; novel=False; metadata=[]
        for address in (p["token0"],p["token1"]):
            info=await inspect_contract(w3,address); label=classify(info["selectors"]); labels.append(label)
            novel |= await self.db.upsert_contract(address,p["creator"],info["code_hash"],label,p["block_number"],info["selectors"])
            name,symbol=await self._token_meta(w3,address); metadata.append((address,name,symbol,label))
            await self.db.db.execute("INSERT OR IGNORE INTO tokens(address,name,symbol,creator,first_seen) VALUES(?,?,?,?,?)",(address,name,symbol,p["creator"],p["block_number"]))
        for address,label in p.get("related",[]):
            info=await inspect_contract(w3,address); labels.append(label)
            novel |= await self.db.upsert_contract(address,p["creator"],info["code_hash"],label,p["block_number"],info["selectors"])
        await self.db.save_pool(p)
        cid=await self.db.cluster(p["creator"],[(p["factory"],"Factory"),(p["address"],"Pool")]+[(x[0],x[3]) for x in metadata]+p.get("related",[]))
        match=self.known.match(p["creator"],p["factory"],p["token0"],p["token1"])
        level,reason=score(labels,match,novel)
        if level=="SILENT": log.info("Silenced known-system pool %s: %s",p["address"],match); return
        names=" / ".join(f"{name} ({symbol}) {address}" for address,name,symbol,_ in metadata)
        text=(f"{'🔥' if level=='S' else '🚨' if level=='A' else '🟡'} {level}级 Robinhood Radar\n"
              f"发现时间: {datetime.now(timezone.utc).isoformat()}\nToken: {names}\nPool: {p['address']}\nDEX: {p['dex_version']}\n"
              f"Deployer/交易发送者: {p['creator']}\nCreator: {p['creator']} (标准 RPC 可验证范围)\nFactory: {p['factory']}\n"
              f"交易: {p['tx_hash']}\n区块: {p['block_number']}\n初步类型: {', '.join(labels)}\n"
              f"项目集群: Factory + Pool + 2 Tokens"+(f" + {', '.join(x[1] for x in p.get('related',[]))}" if p.get("related") else "")+f"\nGMGN known filter: {match or '否'}\n首次字节码/template: {'是' if novel else '否'}\n判断理由: {reason}")
        key=f"pool:{p['address']}:{level}"
        if not await self.db.reserve_alert(key,cid,level,text): return
        try:
            message_id=await self.telegram.send(text)
            if message_id is None: log.warning("Telegram disabled; alert persisted but not delivered")
            else: await self.db.mark_alert_sent(key,message_id)
        except Exception:
            await self.db.release_alert(key); raise
