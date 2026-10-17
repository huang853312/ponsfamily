import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class MonitorTests(unittest.TestCase):
    def test_telegram_poll_timeout_detector(self):
        self.assertTrue(app.is_telegram_poll_timeout(TimeoutError("The read operation timed out")))
        self.assertTrue(app.is_telegram_poll_timeout(TimeoutError("read timed out")))
        self.assertTrue(app.is_telegram_poll_timeout(app.error.URLError(TimeoutError("timed out"))))
        self.assertFalse(app.is_telegram_poll_timeout(RuntimeError("Telegram HTTP 401: unauthorized")))
        self.assertFalse(app.is_telegram_poll_timeout(RuntimeError("xAPI PLATFORM_HTTP_402")))

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

    def test_load_purges_old_per_account_402_alerts_but_keeps_real_alerts(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "schema_version": 2,
                "accounts": ["brian_armstrong", "yorios9312"],
                "running": True,
                "known": {"brian_armstrong": ["old-follow"]},
                "error_notices": {
                    "query:brian_armstrong": {
                        "active": True,
                        "last_error": "xAPI PLATFORM_HTTP_402: Insufficient balance or API Key limit exceeded",
                    },
                    "query:other_error": {"active": True, "last_error": "temporary timeout"},
                    "xapi:billing-limit": {"active": True, "last_error": "global billing notice"},
                },
                "pending_notifications": [
                    {
                        "key": "error:query:brian_armstrong",
                        "category": "error",
                        "text": "xAPI 查询失败：@brian_armstrong：xAPI PLATFORM_HTTP_402: Insufficient balance or API Key limit exceeded",
                    },
                    {
                        "key": "error:query:yorios9312",
                        "category": "error",
                        "text": "xAPI 查询失败：@yorios9312：xAPI PLATFORM_HTTP_402: Insufficient balance or API Key limit exceeded",
                    },
                    {"key": "error:query:other_error", "category": "error", "text": "temporary timeout"},
                    {"key": "brian_armstrong|newfollow", "category": "follow", "text": "X关注提醒：新关注"},
                ],
                "api_pause_until": time.time() + 18000,
                "api_pause_reason": "PLATFORM_HTTP_402",
            }), encoding="utf-8")
            with patch.object(app, "STATE", state_path):
                state = app.load()
        keys = [item.get("key") for item in state["pending_notifications"]]
        self.assertNotIn("error:query:brian_armstrong", keys)
        self.assertNotIn("error:query:yorios9312", keys)
        self.assertIn("error:query:other_error", keys)
        self.assertIn("brian_armstrong|newfollow", keys)
        self.assertNotIn("query:brian_armstrong", state["error_notices"])
        self.assertIn("query:other_error", state["error_notices"])
        self.assertEqual(state["accounts"], ["brian_armstrong", "yorios9312"])
        self.assertGreater(state["api_pause_until"], time.time())

    def test_load_preserves_billing_pause_across_restart(self):
        pause_until = time.time() + 18000
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "accounts": ["fern"], "running": True,
                "known": {"fern": ["old-user"]},
                "api_pause_until": pause_until,
                "api_pause_reason": "PLATFORM_HTTP_402",
            }), encoding="utf-8")
            with patch.object(app, "STATE", state_path):
                state = app.load()
        self.assertEqual(state["api_pause_until"], pause_until)
        self.assertEqual(state["api_pause_reason"], "PLATFORM_HTTP_402")
        self.assertEqual(state["known"]["fern"], ["old-user"])

    def test_load_fails_closed_on_corrupt_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text("{broken", encoding="utf-8")
            with patch.object(app, "STATE", state_path):
                with self.assertRaisesRegex(RuntimeError, "拒绝重置"):
                    app.load()

    def test_clear_error_removes_stale_queued_notice(self):
        state = {
            "error_notices": {"xapi:billing-limit": {"active": True, "last_sent": 10}},
            "pending_notifications": [
                {"key": "error:xapi:billing-limit", "category": "error", "text": "old error"},
                {"key": "fern|newfollow", "category": "follow", "text": "new follow"},
            ],
        }
        app.clear_error(state, "xapi:billing-limit")
        self.assertFalse(state["error_notices"]["xapi:billing-limit"]["active"])
        self.assertEqual([x["key"] for x in state["pending_notifications"]], ["fern|newfollow"])

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
        self.assertEqual(messages, [])  # scanning enqueues; sender worker performs delivery
        self.assertEqual(sum(item["key"] == "source|new" for item in state["pending_notifications"]), 1)
        self.assertNotIn("source|new", state["notified_follows"])
        self.assertEqual(state["known"]["source"], ["first", "new"])
        self.assertEqual(sum(item["key"] == "source|new" for item in state["pending_notifications"]), 1)

    def test_scan_now_after_current_task_deadline_is_immediate(self):
        self.assertEqual(app.scan_completion_deadline(True, now=1000), 0.0)
        self.assertEqual(app.scan_completion_deadline(False, now=1000), 1000 + app.INTERVAL)

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

    def test_load_collapses_legacy_missing_data_error_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps({
                "accounts": ["fern"],
                "known": {"fern": ["known-user"]},
                "pending_notifications": [
                    {"key": "error:query:fern", "category": "error",
                     "text": "xAPI 查询失败：@fern 的关注列表响应缺少 data 对象",
                     "attempts": 0, "next_attempt_at": 0},
                    {"key": "error:query:other", "category": "error",
                     "text": "xAPI 查询失败：@other 的关注列表响应缺少 data 对象",
                     "attempts": 1, "next_attempt_at": 200},
                    {"key": "fern|genuine-follow", "category": "follow",
                     "text": "X关注提醒：@fern 新关注了 @genuine_follow",
                     "source": "fern", "target": "genuine_follow"},
                ],
            }), encoding="utf-8")
            with patch.object(app, "STATE", state_path):
                state = app.load()
        self.assertEqual(len([x for x in state["pending_notifications"] if x.get("category") == "error"]), 1)
        self.assertEqual(len([x for x in state["pending_notifications"] if x.get("category") == "follow"]), 1)
        summary = next(x for x in state["pending_notifications"] if x.get("category") == "error")
        self.assertEqual(summary["key"], "error:xapi:missing-data")
        self.assertEqual(summary["next_attempt_at"], 200)

    def test_platform_402_response_becomes_clear_billing_error(self):
        response = {
            "success": False,
            "error": {
                "code": "PLATFORM_HTTP_402",
                "message": "Insufficient balance or API Key limit exceeded",
                "transient": False,
            },
        }
        with patch.object(app, "resolve_id", return_value="123"), \
             patch.object(app, "xapi", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "PLATFORM_HTTP_402"):
                app.newest_following("fern", "123")

    def test_user_lookup_platform_402_is_not_misreported_as_missing_user(self):
        response = {
            "success": False,
            "error": {
                "code": "PLATFORM_HTTP_402",
                "message": "Insufficient balance or API Key limit exceeded",
                "transient": False,
            },
        }
        with patch.object(app, "xapi", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "PLATFORM_HTTP_402"):
                app.resolve_id("newaccount")

    def test_platform_402_processes_successful_accounts_before_pausing(self):
        state = {
            "accounts": ["fern", "other"], "running": True,
            "known": {"fern": ["old1"], "other": ["old2"]},
            "ids": {"fern": "1", "other": "2"}, "offset": 0,
            "notified_follows": {}, "error_notices": {}, "pending_notifications": [],
        }

        def lookup(name, cached_uid=None):
            if name == "fern":
                return {"old1": "Old1", "new-follow": "NewFollow"}, "1"
            raise RuntimeError("xAPI PLATFORM_HTTP_402: Insufficient balance or API Key limit exceeded")

        with patch.object(app, "newest_following", side_effect=lookup), patch.object(app, "save"):
            asyncio.run(app.scan_all(state))

        self.assertEqual(state["known"]["fern"], ["new-follow", "old1"])
        self.assertEqual(state["known"]["other"], ["old2"])
        self.assertEqual(state["last_scan_result"], "billing_blocked")
        self.assertEqual(state["last_scan_ok_accounts"], 1)
        self.assertEqual(state["last_scan_failed_accounts"], 1)
        follows = [x for x in state["pending_notifications"] if x.get("category") == "follow"]
        self.assertEqual(len(follows), 1)
        self.assertEqual(follows[0]["key"], "fern|new-follow")
        billing_notices = [
            x for x in state["pending_notifications"]
            if x.get("category") == "error"
        ]
        self.assertEqual([x["key"] for x in billing_notices], ["error:xapi:billing-limit"])
        self.assertFalse(any("error:query:" in x.get("key", "") for x in billing_notices))
        self.assertGreater(state["api_pause_until"], time.time())

    def test_platform_402_pauses_all_scans_and_preserves_baselines(self):
        state = {
            "accounts": ["fern", "other"], "running": True,
            "known": {"fern": ["old1"], "other": ["old2"]},
            "ids": {"fern": "1", "other": "2"}, "offset": 0,
            "notified_follows": {}, "error_notices": {}, "pending_notifications": [],
        }
        failure = RuntimeError("xAPI PLATFORM_HTTP_402: Insufficient balance or API Key limit exceeded")
        with patch.object(app, "newest_following", side_effect=[failure, failure]), \
             patch.object(app, "save"):
            asyncio.run(app.scan_all(state))
        self.assertGreater(state["api_pause_until"], time.time())
        self.assertEqual(state["api_pause_reason"], "PLATFORM_HTTP_402")
        self.assertEqual(state["known"]["fern"], ["old1"])
        self.assertEqual(state["known"]["other"], ["old2"])
        queued = [x for x in state["pending_notifications"] if x.get("key") == "error:xapi:billing-limit"]
        self.assertEqual(len(queued), 1)

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
        with patch.object(app, "newest_following", side_effect=RuntimeError("missing users")), \
             patch.object(app, "save"):
            asyncio.run(app.scan_all(state))
        self.assertEqual(state["known"]["fern"], ["oldfollow"])
        self.assertEqual(len(state["pending_notifications"]), 1)
        self.assertIn("xAPI 查询失败", state["pending_notifications"][0]["text"])

    def test_outbox_marks_notification_only_after_success(self):
        state = {
            "accounts": ["fern"], "known": {"fern": ["oldfollow"]}, "ids": {"fern": "123"},
            "notified_follows": {}, "pending_notifications": [{
                "key": "fern|newfollow", "text": "X关注提醒：@fern 新关注了 @NewFollow",
                "category": "follow", "source": "fern", "target": "newfollow",
                "created_at": 1, "attempts": 0, "next_attempt_at": 0,
            }], "last_follow_sent_at": 0,
        }
        with patch.object(app, "send") as send_mock, patch.object(app, "save"):
            delivered = asyncio.run(app.deliver_one_pending_notification(state))
        self.assertTrue(delivered)
        send_mock.assert_called_once()
        self.assertIn("fern|newfollow", state["notified_follows"])
        self.assertEqual(state["pending_notifications"], [])

    def test_429_keeps_notification_queued_and_honors_retry_after(self):
        state = {
            "pending_notifications": [{
                "key": "fern|newfollow", "text": "alert", "category": "follow",
                "source": "fern", "target": "newfollow", "attempts": 0, "next_attempt_at": 0,
            }],
            "notified_follows": {}, "last_follow_sent_at": 0,
        }
        with patch.object(app, "send", side_effect=RuntimeError(
            'Telegram HTTP 429: {"ok":false,"parameters":{"retry_after":20}}'
        )), patch.object(app, "save"):
            delivered = asyncio.run(app.deliver_one_pending_notification(state))
        self.assertFalse(delivered)
        self.assertEqual(len(state["pending_notifications"]), 1)
        self.assertNotIn("fern|newfollow", state["notified_follows"])
        self.assertGreaterEqual(state["pending_notifications"][0]["next_attempt_at"], time.time() + 20)

    def test_missing_data_errors_across_accounts_are_grouped(self):
        state = {
            "accounts": ["fern", "other"], "running": True,
            "known": {"fern": ["old1"], "other": ["old2"]}, "ids": {"fern": "1", "other": "2"},
            "offset": 0, "notified_follows": {}, "error_notices": {}, "pending_notifications": [],
        }
        with patch.object(
            app, "newest_following",
            side_effect=[
                RuntimeError("@fern 的关注列表响应缺少 data 对象；top_level_keys=['message']"),
                RuntimeError("@other 的关注列表响应缺少 data 对象；top_level_keys=['message']"),
            ],
        ), patch.object(app, "save"):
            asyncio.run(app.scan_all(state))
        queued = [x for x in state["pending_notifications"] if x.get("category") == "error"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["key"], "error:xapi:missing-data")
        self.assertEqual(state["known"]["fern"], ["old1"])
        self.assertEqual(state["known"]["other"], ["old2"])
        self.assertEqual(state["error_notices"]["xapi:missing-data"]["count"], 2)

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
        state = {"error_notices": {}, "pending_notifications": []}
        with patch.object(app, "save"):
            app.report_limited_error(state, "query:fern", "xAPI 查询失败：@fern", now=100000)
            app.report_limited_error(state, "query:fern", "xAPI 查询失败：@fern", now=100100)
        self.assertEqual(len(state["pending_notifications"]), 1)
        self.assertEqual(state["error_notices"]["query:fern"]["count"], 2)


if __name__ == "__main__":
    unittest.main()
