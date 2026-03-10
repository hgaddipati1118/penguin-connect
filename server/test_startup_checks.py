import sqlite3
import unittest
from unittest import mock

from db import SCHEMA
import startup_checks


def _build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


class StartupChecksTests(unittest.TestCase):
    def setUp(self):
        self.conn = _build_conn()

    def tearDown(self):
        self.conn.close()

    def test_strict_startup_requires_connected_gmail(self):
        with mock.patch(
            "startup_checks._check_apple_messages_access",
            return_value={
                "name": "apple_messages",
                "ok": True,
                "blocking": True,
                "reason": None,
                "detail": "ok",
            },
        ):
            with self.assertRaises(startup_checks.StartupReadinessError) as ctx:
                startup_checks.assert_startup_ready(self.conn, allow_missing_gmail=False)

        self.assertEqual(ctx.exception.failures[0]["reason"], "gmail_not_connected")

    def test_bootstrap_mode_allows_missing_gmail_only(self):
        with mock.patch(
            "startup_checks._check_apple_messages_access",
            return_value={
                "name": "apple_messages",
                "ok": True,
                "blocking": True,
                "reason": None,
                "detail": "ok",
            },
        ):
            readiness = startup_checks.assert_startup_ready(self.conn, allow_missing_gmail=True)

        self.assertTrue(readiness["ok"])
        gmail_check = next(item for item in readiness["checks"] if item["name"] == "gmail")
        self.assertFalse(gmail_check["ok"])
        self.assertFalse(gmail_check["blocking"])
        self.assertEqual(gmail_check["reason"], "gmail_not_connected")

    def test_bootstrap_mode_still_blocks_invalid_gmail_state(self):
        with mock.patch(
            "startup_checks._check_apple_messages_access",
            return_value={
                "name": "apple_messages",
                "ok": True,
                "blocking": True,
                "reason": None,
                "detail": "ok",
            },
        ), mock.patch(
            "startup_checks.get_connected_account",
            return_value={"gmail_email": "owner@gmail.com", "keychain_service": "svc"},
        ), mock.patch(
            "startup_checks._build_gmail_service",
            return_value=(None, "invalid_keychain_token_json"),
        ):
            with self.assertRaises(startup_checks.StartupReadinessError) as ctx:
                startup_checks.assert_startup_ready(self.conn, allow_missing_gmail=True)

        self.assertEqual(ctx.exception.failures[0]["reason"], "invalid_keychain_token_json")

    def test_startup_requires_apple_messages_access(self):
        with mock.patch(
            "startup_checks._check_apple_messages_access",
            return_value={
                "name": "apple_messages",
                "ok": False,
                "blocking": True,
                "reason": "imessage_db_unreadable",
                "detail": "not readable",
            },
        ), mock.patch(
            "startup_checks.get_connected_account",
            return_value={"gmail_email": "owner@gmail.com", "keychain_service": "svc"},
        ), mock.patch(
            "startup_checks._build_gmail_service",
            return_value=(object(), None),
        ):
            with self.assertRaises(startup_checks.StartupReadinessError) as ctx:
                startup_checks.assert_startup_ready(self.conn, allow_missing_gmail=True)

        self.assertEqual(ctx.exception.failures[0]["reason"], "imessage_db_unreadable")


if __name__ == "__main__":
    unittest.main()
