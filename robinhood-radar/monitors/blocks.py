"""Reorg-aware-enough confirmed block polling with bounded batches."""
import asyncio
import logging
from web3 import AsyncWeb3, AsyncHTTPProvider

from monitors import uniswap_v2, uniswap_v3, uniswap_v4

log=logging.getLogger(__name__)

class BlockMonitor:
    def __init__(self, settings, db, handler):
        self.s=settings; self.db=db; self.handler=handler
        self.w3=AsyncWeb3(AsyncHTTPProvider(settings.rpc_url, request_kwargs={"timeout":30}))
    async def run(self):
        delay=1.0
        while True:
            try:
                if await self.w3.eth.chain_id != 4663: raise RuntimeError("RPC chainId is not 4663")
                head=await self.w3.eth.block_number-self.s.confirmations
                cursor=await self.db.cursor()
                if cursor is None:
                    cursor=head-1; await self.db.set_cursor(cursor)
                    log.warning("No cursor: starting at current confirmed head %s",head)
                while cursor < head:
                    end=min(cursor+self.s.block_batch_size,head)
                    await self._range(cursor+1,end)
                    await self.db.set_cursor(end); cursor=end
                delay=1.0; await asyncio.sleep(self.s.poll_interval)
            except asyncio.CancelledError: raise
            except Exception:
                log.exception("RPC loop failed; retrying in %.1fs",delay)
                await asyncio.sleep(delay); delay=min(delay*2,60)
    async def _range(self,start,end):
        specs=[("V2",self.s.v2_factories,uniswap_v2), ("V3",self.s.v3_factories,uniswap_v3), ("V4",self.s.v4_managers,uniswap_v4)]
        for version, addresses, module in specs:
            if not addresses: continue
            logs=await self.w3.eth.get_logs({"fromBlock":start,"toBlock":end,"address":list(addresses),"topics":[module.TOPIC]})
            for event in sorted(logs,key=lambda x:(x["blockNumber"],x["logIndex"])):
                token0,token1,pool,related=module.decode_log(event)
                tx=await self.w3.eth.get_transaction(event["transactionHash"])
                block=await self.w3.eth.get_block(event["blockNumber"])
                await self.handler({"address":pool,"block_number":event["blockNumber"],"timestamp":block["timestamp"],"tx_hash":event["transactionHash"].hex(),"dex_version":version,"token0":token0,"token1":token1,"creator":tx["from"].lower(),"factory":event["address"].lower(),"related":related},self.w3)
