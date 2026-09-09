import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from analyzers.classifier import classify
from analyzers.cluster import score
from analyzers.known_system_filter import KnownSystemFilter
from storage.database import Database

class CoreTests(unittest.TestCase):
    def test_conservative_classifier(self):
        self.assertEqual(classify(["0x70a08231"]),"Unknown")
        self.assertEqual(classify(["0x70a08231","0xa9059cbb","0x18160ddd"]),"ERC20")
    def test_known_filter_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"known.json"; p.write_text(json.dumps({"systems":[{"name":"PONS","addresses":{"factory":["0xabc"]}}]}))
            known=KnownSystemFilter(p).match("0xAbC")
            self.assertEqual(score([],known,False)[0],"SILENT")
            self.assertEqual(score(["Router"],known,False)[0],"A")
    def test_database_init_and_dedupe(self):
        async def run():
            with tempfile.TemporaryDirectory() as d:
                db=await Database(Path(d)/"radar.db").open()
                tables={r[0] for r in await (await db.db.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
                self.assertTrue({"processed_blocks","contracts","pools","alerts","observations"} <= tables)
                self.assertTrue(await db.reserve_alert("x",1,"B","test")); self.assertFalse(await db.reserve_alert("x",1,"B","test"))
                await db.close()
        asyncio.run(run())

if __name__ == "__main__": unittest.main()

