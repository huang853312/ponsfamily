import traceback
import asyncio
import sqlite3
import time
from web3 import AsyncWeb3, AsyncHTTPProvider
from rpc_errors import is_contract_execution_failure
from config import RPC_URL, POLL_INTERVAL
from address_book import load_deployers
from database import DB_PATH, save_token_creator, save_relation

class HyperEVMBlockMonitor:
    def __init__(self):
        self.w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))

    async def chain_id(self):
        return await self.w3.eth.chain_id

    async def latest_block(self):
        return await self.w3.eth.block_number

    async def get_contract_creations(self, block_number: int):
        block = await self.w3.eth.get_block(block_number, full_transactions=True)
        events = []

        known_deployers = load_deployers()

        for tx in block.transactions:
            tx_from = (tx.get("from") or "").lower()
            tx_to = tx.get("to")
            tx_value = int(tx.get("value", 0) or 0)

            # Important wallet -> another address native HYPE funding.
            # This alone does NOT trigger Telegram; it is only relationship evidence.
            if (
                tx_to is not None
                and tx_value > 0
                and tx_from in known_deployers
                and tx_to.lower() != tx_from
            ):
                save_relation(
                    parent=tx_from,
                    child=tx_to,
                    relation_type="FUNDING",
                    tx_hash=tx["hash"].hex(),
                    block_number=block_number,
                )

            if tx.get("to") is None:
                receipt = await self.w3.eth.get_transaction_receipt(tx["hash"])
                address = receipt.get("contractAddress")
                if address:
                    save_token_creator(address, tx.get("from", ""), block_number, tx["hash"].hex())
                    events.append({"address": address, "creator": tx.get("from", ""),
                                   "block_number": block_number, "tx_hash": tx["hash"].hex()})

        return events

    async def get_code(self, address: str):
        checksum = self.w3.to_checksum_address(address)
        return bytes(await self.w3.eth.get_code(checksum))

    def scan_position(self, initial_block):
        with sqlite3.connect(DB_PATH) as c:
            c.execute("CREATE TABLE IF NOT EXISTS radar_scan_progress (chain_id INTEGER PRIMARY KEY, next_block INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
            c.execute("INSERT OR IGNORE INTO radar_scan_progress VALUES (999,?,?)", (initial_block, int(time.time())))
            return c.execute("SELECT next_block FROM radar_scan_progress WHERE chain_id=999").fetchone()[0]

    def advance_position(self, current):
        with sqlite3.connect(DB_PATH) as c:
            cursor=c.execute("UPDATE radar_scan_progress SET next_block=?, updated_at=? WHERE chain_id=999 AND next_block=?", (current+1,int(time.time()),current))
            if cursor.rowcount!=1:
                raise RuntimeError("Scanner progress changed unexpectedly")

    async def validate_pool(self, info, block_number):
        # Read failures propagate: retry this block, never treat RPC failure as false.
        address=self.w3.to_checksum_address(info["pool"])
        code=await self.w3.eth.get_code(address,block_identifier=block_number)
        if not code:return False
        async def read_address(signature):
            selector=self.w3.keccak(text=signature)[:4]
            try:
                raw=bytes(await self.w3.eth.call({"to":address,"data":selector},block_identifier=block_number))
            except Exception as exc:
                if is_contract_execution_failure(exc):return ""
                raise
            if len(raw)!=32 or any(raw[:12]):return ""
            return "0x"+raw[-20:].hex()
        factory=await read_address("factory()")
        token0=await read_address("token0()")
        token1=await read_address("token1()")
        if not (factory.lower()==info["factory"].lower() and token0.lower()==info["token0"].lower()
                and token1.lower()==info["token1"].lower() and token0!=token1):return False
        factory_address=self.w3.to_checksum_address(factory)
        signature="getPair(address,address)" if info["type"]=="V2_PAIR" else "getPool(address,address,uint24)"
        data=bytes(self.w3.keccak(text=signature)[:4])+bytes.fromhex(token0[2:]).rjust(32,b"\0")+bytes.fromhex(token1[2:]).rjust(32,b"\0")
        if info["type"]=="V3_POOL":data+=int(info["fee"]).to_bytes(32,"big")
        try:
            raw=bytes(await self.w3.eth.call({"to":factory_address,"data":data},block_identifier=block_number))
        except Exception as exc:
            if is_contract_execution_failure(exc):return False
            raise
        return len(raw)==32 and not any(raw[:12]) and raw[-20:].hex()==info["pool"][2:].lower()

    async def scan_block(self, current, handler):
        events=await self.get_contract_creations(current)
        for event in events:await handler(self,event)
        self.advance_position(current)

    async def run(self, handler):
        head=await self.latest_block()
        # First installation starts with a bounded overlap. Subsequent starts use
        # the persisted cursor, including every block missed while offline.
        current=self.scan_position(max(0,head-20))
        print(f"HyperEVM monitor started chain_id={await self.chain_id()} from_block={current} head={head}",flush=True)
        failures=0
        while True:
            try:
                latest=await self.latest_block()
                while current<=latest:
                    await self.scan_block(current,handler)
                    current+=1;failures=0
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:raise
            except Exception:
                failures+=1
                traceback.print_exc()
                delay=min(60,max(POLL_INTERVAL,2**min(failures-1,6)))
                print(f"SCAN_RETRY block={current} attempt={failures} delay={delay}",flush=True)
                await asyncio.sleep(delay)

    async def get_logs(self, block_number: int):
        return await self.w3.eth.get_logs({"fromBlock":block_number,"toBlock":block_number})
