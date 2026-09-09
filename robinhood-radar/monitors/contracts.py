"""Incremental top-level and optional traced internal contract creation discovery."""
import asyncio
import logging
from web3 import AsyncWeb3, AsyncHTTPProvider

log=logging.getLogger(__name__)

class ContractCreationMonitor:
    def __init__(self,settings,db,handler,tracer,rpc_limiter=None):
        self.s,self.db,self.handler,self.tracer=settings,db,handler,tracer
        self.w3=AsyncWeb3(AsyncHTTPProvider(settings.rpc_url,request_kwargs={"timeout":30}))
        self._trace_operational=True
        self.rpc_limiter=rpc_limiter
    async def _call(self,awaitable):
        limiter=getattr(self,"rpc_limiter",None);return await limiter.call(awaitable) if limiter else await awaitable
    async def run(self):
        delay=1.0
        while True:
            try:
                if await self._call(self.w3.eth.chain_id)!=4663:raise RuntimeError("RPC chainId is not 4663")
                head=await self._call(self.w3.eth.block_number)-self.s.confirmations
                cursor=await self.db.monitor_cursor("contracts")
                if cursor is None:
                    cursor=head-1;await self.db.set_monitor_cursor("contracts",cursor)
                    log.warning("No contract cursor: starting at current confirmed head %s",head)
                while cursor<head:
                    await self._block(cursor+1)
                    cursor+=1;await self.db.set_monitor_cursor("contracts",cursor)
                delay=1.0;await asyncio.sleep(self.s.poll_interval)
            except asyncio.CancelledError:raise
            except Exception:
                log.exception("Contract RPC loop failed; retrying in %.1fs",delay)
                await asyncio.sleep(delay);delay=min(delay*2,60)
    async def _block(self,number):
        block=await self._call(self.w3.eth.get_block(number,full_transactions=True))
        for tx in block["transactions"]:
            tx_hash=tx["hash"].hex();events=[]
            trace_status="unavailable" if self.tracer.enabled and not getattr(self,"_trace_operational",True) else "disabled"
            if tx.get("to") is None:
                receipt=await self._call(self.w3.eth.get_transaction_receipt(tx["hash"]))
                if receipt.get("contractAddress"):
                    events.append({"address":receipt["contractAddress"].lower(),"creator":tx["from"].lower(),"creation_type":"TOP_LEVEL_CREATE"})
            if self.tracer.enabled and getattr(self,"_trace_operational",True):
                trace=await self.tracer.trace(self.w3,tx_hash);trace_status=trace["status"]
                if trace_status=="unavailable":
                    self._trace_operational=False
                    log.warning("Trace unavailable for tx %s; disabling trace until process restart",tx_hash)
                events.extend({"address":x["address"],"creator":x.get("creator"),"creation_type":x["type"]} for x in trace["creations"] if x.get("creator"))
            seen=set()
            for item in events:
                identity=(tx_hash,item["address"])
                if identity in seen:continue
                seen.add(identity)
                await self.handler({**item,"tx_hash":tx_hash,"block_number":number,"timestamp":block["timestamp"],"trace_status":trace_status},self.w3)
