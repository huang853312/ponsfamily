import sqlite3
import tempfile
import unittest
from pathlib import Path

import database


class IdentityRetryStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "radar.db"
        database.init_platform_family_intelligence_db()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def result(self, status, evidence=None):
        return {
            "subject_key": "family:999",
            "family_id": 999,
            "source_url": "",
            "verification_status": status,
            "discovered_project_addresses": [],
            "evidence": evidence or [],
        }

    def test_no_data_exponential_backoff_and_reason(self):
        first = database.save_platform_identity_investigation(
            self.result("NO_DATA", [{"source": "identity_engine", "status": "NO_DATA", "detail": "TimeoutError"}])
        )
        self.assertEqual(first["attempt_count"], 1)
        self.assertEqual(first["backoff_seconds"], 600)
        self.assertGreater(first["next_retry_at"], 0)
        self.assertEqual(first["last_failure_reason"], "identity_engine:TimeoutError")

        second = database.save_platform_identity_investigation(self.result("NO_DATA"))
        self.assertEqual(second["attempt_count"], 2)
        self.assertEqual(second["backoff_seconds"], 1200)
        self.assertEqual(second["last_failure_reason"], "no_official_identity_sources")

    def test_partial_has_shorter_backoff(self):
        state = database.save_platform_identity_investigation(self.result("PARTIAL"))
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["backoff_seconds"], 300)
        self.assertEqual(state["last_failure_reason"], "cross_verification_incomplete")

    def test_verified_resets_retry_state(self):
        database.save_platform_identity_investigation(self.result("NO_DATA"))
        state = database.save_platform_identity_investigation(self.result("VERIFIED"))
        self.assertEqual(state["attempt_count"], 0)
        self.assertEqual(state["backoff_seconds"], 0)
        self.assertEqual(state["next_retry_at"], 0)
        self.assertEqual(state["last_failure_reason"], "")
        with sqlite3.connect(database.DB_PATH) as conn:
            row = conn.execute(
                "SELECT attempt_count,next_retry_at,backoff_seconds,last_failure_reason,last_success_at "
                "FROM platform_identity_investigations WHERE subject_key='family:999'"
            ).fetchone()
        self.assertEqual(row[:4], (0, 0, 0, ""))
        self.assertGreater(row[4], 0)


if __name__ == "__main__":
    unittest.main()
