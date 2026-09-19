import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class MonitorTests(unittest.TestCase):
    def test_clean_username(self):
        self.assertEqual(app.clean(" @OpenAI "), "openai")
        with self.assertRaises(ValueError):
            app.clean("@bad-name")

    def test_load_migrates_legacy_account(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({"a": "openai"}), encoding="utf-8")
            with patch.object(app, "STATE", state_path):
                state = app.load()
        self.assertEqual(state["accounts"], ["openai"])

    def test_scan_establishes_baseline_then_detects_new_follow(self):
        state = {
            "accounts": ["source"],
            "running": True,
            "known": {},
            "ids": {},
            "offset": 0,
        }
        messages = []

        with patch.object(
            app,
            "newest_following",
            side_effect=[{"first": "First"}, {"first": "First", "new": "New"}],
        ), patch.object(app, "safe_send", messages.append):
            asyncio.run(app.scan_all(state))
            asyncio.run(app.scan_all(state))

        self.assertTrue(any("基线已建立" in item for item in messages))
        self.assertTrue(any("新关注了 @New" in item for item in messages))


if __name__ == "__main__":
    unittest.main()
