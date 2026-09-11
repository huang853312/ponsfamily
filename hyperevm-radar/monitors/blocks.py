import traceback
import asyncio
from web3 import AsyncWeb3, AsyncHTTPProvider
from config import RPC_URL, POLL_INTERVAL
from address_book import load_deployers
from database import save_token_creator, save_relation

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
                try:
                    receipt = await self.w3.eth.get_transaction_receipt(tx["hash"])
                    address = receipt.get("contractAddress")
                    if address:
                        save_token_creator(address, tx.get("from", ""), block_number, tx["hash"].hex())
                        events.append({
                            "address": address,
                            "creator": tx.get("from", ""),
                            "block_number": block_number,
                            "tx_hash": tx["hash"].hex(),
                        })
                except Exception:
                    pass

        return events

    async def get_code(self, address: str):
        checksum = self.w3.to_checksum_address(address)
        return bytes(await self.w3.eth.get_code(checksum))

    async def run(self, handler):
        current = await self.latest_block()
        print(f"HyperEVM monitor started chain_id={await self.chain_id()} from_block={current}")

        while True:
            try:
                latest = await self.latest_block()

                while current <= latest:
                    events = await self.get_contract_creations(current)
                    for event in events:
                        await handler(self, event)
                    current += 1

            except Exception as e:
                traceback.print_exc()

            await asyncio.sleep(POLL_INTERVAL)

    async def get_logs(self, block_number: int):
        try:
            return await self.w3.eth.get_logs({
                "fromBlock": block_number,
                "toBlock": block_number
            })
        except Exception as e:
            print(f"log error block={block_number}: {e}")
            return []
