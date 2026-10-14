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
        self.assertEqual(state["notified_follows"], {})
        self.assertEqual(state["error_notices"], {})

    def test_channel_post_is_accepted_as_command_message(self):
        post = {"chat": {"id": -100123}, "text": "/list"}
        self.assertEqual(app.update_message({"channel_post": post}), post)

    def test_scan_establishes_baseline_then_detects_new_follow_once(self):
        state = {
            "accounts": ["source"], "running": True, "known": {}, "ids": {},
            "offset": 0, "notified_follows": {}, "error_notices": {},
        }
        messages = []
        with patch.object(
            app, "newest_following",
            side_effect=[
                ({"first": "First"}, "123"),
                ({"first": "First", "new": "New"}, "123"),
                ({"first": "First", "new": "New"}, "123"),
            ],
        ), patch.object(
            app, "safe_send", side_effect=lambda message: messages.append(message) or True
        ), patch.object(app, "save"):
            asyncio.run(app.scan_all(state))
            asyncio.run(app.scan_all(state))
            asyncio.run(app.scan_all(state))
        self.assertEqual(state["ids"]["source"], "123")
        self.assertEqual(sum("新关注了 @New" in item for item in messages), 1)
        self.assertIn("source|new", state["notified_follows"])
        self.assertEqual(state["known"]["source"], ["first", "new"])

    def test_start_monitor_preserves_baseline(self):
        state = {"running": False, "known": {"fern": ["oldfollow"]}}
        message = app.start_monitor(state)
        self.assertTrue(state["running"])
        self.assertEqual(state["known"], {"fern": ["oldfollow"]})
        self.assertIn("保留已有历史基线", message)

    def test_legacy_state_schedules_complete_baseline_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "accounts": ["fern", "openai"], "running": True,
                "known": {"fern": ["old-page-user"], "openai": ["old-user"]},
                "ids": {}, "offset": 0,
            }), encoding="utf-8")
            with patch.object(app, "STATE", state_path):
                state = app.load()
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["baseline_reconcile"], ["fern", "openai"])

    def test_upgrade_reconciliation_suppresses_first_complete_diff(self):
        state = {
            "schema_version": 2, "baseline_reconcile": ["fern"],
            "accounts": ["fern"], "running": True,
            "known": {"fern": ["old-page-user"]}, "ids": {"fern": "123"},
            "offset": 0, "notified_follows": {}, "error_notices": {},
        }
        messages = []
        with patch.object(
            app, "newest_following",
            return_value=({"old-page-user": "OldPageUser", "page2-user": "Page2User"}, "123")
        ), patch.object(
            app, "safe_send", side_effect=lambda message: messages.append(message) or True
        ), patch.object(app, "save"):
            asyncio.run(app.scan_all(state))
        self.assertEqual(state["known"]["fern"], ["old-page-user", "page2-user"])
        self.assertEqual(state["baseline_reconcile"], [])
        self.assertFalse(any("新关注了" in item for item in messages))

    def test_missing_users_field_is_rejected(self):
        with patch.object(app, "resolve_id", return_value="123"), \
             patch.object(app, "xapi", return_value={"data": {"cursor_bottom": "cursor-1"}}):
            with self.assertRaisesRegex(RuntimeError, "缺少有效 users"):
                app.newest_following("fern", "123")

    def test_following_pages_are_aggregated(self):
        responses = [
            {"data": {"users": [{"screen_name": "First"}], "cursor_bottom": "page-2"}},
            {"data": {"users": [{"screen_name": "Second"}], "cursor_bottom": ""}},
        ]
        with patch.object(app, "resolve_id", return_value="123"), \
             patch.object(app, "xapi", side_effect=responses) as xapi_mock:
            current, uid = app.newest_following("fern", "123")
        self.assertEqual(uid, "123")
        self.assertEqual(set(current), {"first", "second"})
        self.assertEqual(xapi_mock.call_args_list[1].args[1]["cursor"], "page-2")

    def test_repeated_cursor_is_rejected(self):
        response = {"data": {"users": [{"screen_name": "First"}], "cursor_bottom": "same"}}
        with patch.object(app, "resolve_id", return_value="123"), \
             patch.object(app, "xapi", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "游标重复"):
                app.newest_following("fern", "123")

    def test_incomplete_api_failure_preserves_known_baseline(self):
        state = {
            "accounts": ["fern"], "running": True, "known": {"fern": ["oldfollow"]},
            "ids": {"fern": "123"}, "offset": 0, "notified_follows": {}, "error_notices": {},
        }
        messages = []
        with patch.object(app, "newest_following", side_effect=RuntimeError("missing users")), \
             patch.object(app, "safe_send", side_effect=lambda message: messages.append(message) or True), \
             patch.object(app, "save"):
            asyncio.run(app.scan_all(state))
        self.assertEqual(state["known"]["fern"], ["oldfollow"])
        self.assertEqual(len(messages), 1)
        self.assertIn("xAPI 查询失败", messages[0])

    def test_prior_marker_prevents_repeat_after_interrupted_scan(self):
        state = {
            "accounts": ["fern"], "running": True, "known": {"fern": ["oldfollow"]},
            "ids": {"fern": "123"}, "offset": 0,
            "notified_follows": {"fern|newfollow": 1700000000}, "error_notices": {},
        }
        messages = []
        with patch.object(
            app, "newest_following",
            return_value=({"oldfollow": "OldFollow", "newfollow": "NewFollow"}, "123")
        ), patch.object(
            app, "safe_send", side_effect=lambda message: messages.append(message) or True
        ), patch.object(app, "save"):
            asyncio.run(app.scan_all(state))
        self.assertFalse(any("新关注了 @NewFollow" in item for item in messages))
        self.assertIn("newfollow", state["known"]["fern"])

    def test_error_notifications_are_throttled(self):
        state = {"error_notices": {}}
        messages = []
        with patch.object(app, "safe_send", side_effect=lambda message: messages.append(message) or True), \
             patch.object(app, "save"):
            app.report_limited_error(state, "query:fern", "xAPI 查询失败：@fern", now=100000)
            app.report_limited_error(state, "query:fern", "xAPI 查询失败：@fern", now=100100)
        self.assertEqual(len(messages), 1)
        self.assertEqual(state["error_notices"]["query:fern"]["count"], 2)


if __name__ == "__main__":
    unittest.main()
