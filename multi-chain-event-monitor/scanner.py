import logging
from rpc import RpcClient


class ChainScanner:
    def __init__(self, name, rpc_url, store, chain_config, event_filter, defaults):
        self.name = name
        self.rpc = RpcClient(rpc_url)
        self.store = store
        self.chain_config = chain_config or {}
        self.event_filter = event_filter or {}
        self.confirmations = int(self.chain_config.get("confirmations", defaults["confirmations"]))
        self.start_block = self.chain_config.get("start_block")
        self.max_range = defaults["max_range"]

    def scan_once(self):
        # No address/topic means no event scan. This avoids accidental full-chain log scans.
        if not self.event_filter.get("addresses") and not self.event_filter.get("topics"):
            logging.info("[%s] event filters not configured; scanner is idle", self.name)
            return

        head = self.rpc.block_number()
        safe_head = head - self.confirmations
        if safe_head < 0:
            return

        cursor = self.store.get_cursor(self.name)
        if cursor is None:
            if self.start_block is None:
                # Safe default: begin at the current confirmed head, not from genesis.
                self.store.set_cursor(self.name, safe_head)
                logging.info("[%s] initialized at confirmed block %s; no historical backfill", self.name, safe_head)
                return
            cursor = int(self.start_block) - 1

        next_block = cursor + 1
        if next_block > safe_head:
            return

        end_block = min(next_block + self.max_range - 1, safe_head)
        logs = self.rpc.get_logs(next_block, end_block, self.event_filter)
        inserted = self.store.enqueue_logs(self.name, logs)
        self.store.set_cursor(self.name, end_block)
        logging.info(
            "[%s] scanned blocks %s-%s; received=%s new=%s",
            self.name, next_block, end_block, len(logs), inserted
        )
