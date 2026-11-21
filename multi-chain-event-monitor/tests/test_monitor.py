import tempfile
import unittest
from pathlib import Path

from database import EventStore
from scanner import ChainScanner


class FakeRpc:
    def __init__(self, head=105):
        self.head = head
        self.queries = []

    def block_number(self):
        return self.head

    def get_logs(self, start, end, event_filter):
        self.queries.append((start, end, event_filter))
        return [{
            "blockNumber": hex(end),
            "transactionHash": "0xabc",
            "logIndex": "0x0",
            "address": "0x123",
            "topics": ["0xfeed"],
        }]


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(str(Path(self.tmp.name) / "test.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_event_deduplication_and_cursor(self):
        event = {
            "blockNumber": "0x10",
            "transactionHash": "0xABC",
            "logIndex": "0x1",
            "address": "0x123",
            "topics": [],
        }
        self.assertEqual(self.store.enqueue_logs("base", [event]), 1)
        self.assertEqual(self.store.enqueue_logs("base", [event]), 0)
        self.assertEqual(self.store.get_cursor("base"), None)
        self.store.set_cursor("base", 16)
        self.assertEqual(self.store.get_cursor("base"), 16)

    def test_no_filter_never_calls_rpc(self):
        scanner = ChainScanner("base", "http://unused", self.store, {}, {}, {
            "confirmations": 0, "max_range": 10
        })
        scanner.rpc = FakeRpc()
        scanner.scan_once()
        self.assertEqual(scanner.rpc.queries, [])
        self.assertIsNone(self.store.get_cursor("base"))

    def test_filtered_scan_initializes_then_scans_bounded_range(self):
        scanner = ChainScanner("base", "http://unused", self.store, {
            "start_block": 100, "confirmations": 2
        }, {"addresses": ["0x123"]}, {
            "confirmations": 2, "max_range": 3
        })
        scanner.rpc = FakeRpc(head=105)
        scanner.scan_once()
        self.assertEqual(self.store.get_cursor("base"), 102)
        scanner.scan_once()
        self.assertEqual(scanner.rpc.queries[0][0:2], (100, 102))
        self.assertEqual(scanner.rpc.queries[1][0:2], (103, 103))
        self.assertEqual(len(self.store.pending_events()), 1)


if __name__ == "__main__":
    unittest.main()
