import sqlite3
import unittest
from unittest import mock

import app as app_module
from db import SCHEMA
from fastapi import HTTPException


def _build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


class AppStatusTests(unittest.TestCase):
    def test_startup_catchup_retry_delay_uses_gmail_retry_after(self):
        delay = app_module._startup_catchup_retry_delay(
            {"success": True, "skipped": True, "reason": "gmail_rate_limited", "retry_after_seconds": 12},
            5.0,
        )

        self.assertEqual(delay, 12.0)

    def test_startup_catchup_retry_delay_uses_batch_pause_for_queue_busy(self):
        delay = app_module._startup_catchup_retry_delay(
            {"success": True, "skipped": True, "reason": "queue_busy"},
            7.0,
        )

        self.assertEqual(delay, 7.0)

    def test_startup_catchup_retry_delay_stops_on_queue_idle(self):
        delay = app_module._startup_catchup_retry_delay(
            {"success": True, "skipped": True, "reason": "queue_idle"},
            7.0,
        )

        self.assertIsNone(delay)

    def test_status_includes_sync_metrics(self):
        conn = _build_conn()
        with mock.patch("app.get_connection", return_value=conn), mock.patch(
            "app.penguinconnect_get_gmail_connection_status",
            return_value={"connected": True, "gmail_email": "owner@gmail.com"},
        ), mock.patch(
            "app.penguinconnect_get_cached_sync_metrics",
            return_value={"totals": {"retry_queue_count": 2}},
        ), mock.patch(
            "app.penguinconnect_get_runtime_sync_status",
            return_value={"running": True, "mode": "backfill", "processed_conversations": 1},
        ), mock.patch(
            "app.get_sync_status",
            return_value={"penguin_connect": {"polling": True, "last_sync": "2026-03-04T12:00:00+00:00"}},
        ):
            result = app_module.get_status()

        self.assertIn("sync_metrics", result)
        self.assertEqual(result["sync_metrics"]["totals"]["retry_queue_count"], 2)
        self.assertTrue(result["sync_status"]["penguin_connect"]["running"])
        self.assertEqual(result["sync_status"]["penguin_connect"]["mode"], "backfill")

    def test_health_includes_sync_metrics(self):
        conn = _build_conn()
        with mock.patch("app.get_connection", return_value=conn), mock.patch(
            "app.penguinconnect_get_gmail_connection_status",
            return_value={"connected": True, "gmail_email": "owner@gmail.com"},
        ), mock.patch(
            "app.penguinconnect_get_cached_sync_metrics",
            return_value={"totals": {"failed_permanent_count": 1}},
        ), mock.patch(
            "app.penguinconnect_get_runtime_sync_status",
            return_value={"running": False, "last_completed_at": "2026-03-04T12:05:00+00:00"},
        ), mock.patch(
            "app.get_sync_status",
            return_value={"penguin_connect": {"polling": True, "last_sync": "2026-03-04T12:00:00+00:00"}},
        ):
            result = app_module.get_penguinconnect_health()

        self.assertIn("sync_metrics", result)
        self.assertEqual(result["sync_metrics"]["totals"]["failed_permanent_count"], 1)
        self.assertEqual(result["sync_status"]["penguin_connect"]["last_completed_at"], "2026-03-04T12:05:00+00:00")

    def test_map_sqlite_error_imessage_db_unreadable(self):
        err = sqlite3.OperationalError("unable to open database file")
        http_err = app_module._map_sqlite_error(err)
        self.assertEqual(http_err.status_code, 503)
        self.assertEqual(http_err.detail, "imessage_db_unreadable")

    def test_sync_endpoint_maps_imessage_db_unreadable(self):
        conn = _build_conn()
        try:
            with mock.patch("app.get_connection", return_value=conn), mock.patch(
                "app.penguinconnect_sync_conversations",
                side_effect=sqlite3.OperationalError("unable to open database file"),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    app_module.sync_penguinconnect_conversations(app_module.PenguinConnectSyncRequest())
        finally:
            conn.close()

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail, "imessage_db_unreadable")

    def test_sync_endpoint_passes_hours_override(self):
        conn = _build_conn()
        try:
            with mock.patch("app.get_connection", return_value=conn), mock.patch(
                "app.penguinconnect_sync_conversations",
                return_value={"success": True, "mode": "backfill", "days": 7, "hours": 5},
            ) as mock_sync:
                result = app_module.sync_penguinconnect_conversations(
                    app_module.PenguinConnectSyncRequest(mode="backfill", days=7, hours=5)
                )
        finally:
            conn.close()

        self.assertTrue(result["success"])
        mock_sync.assert_called_once_with(conn, mode="backfill", days=7, hours=5, verify_all=False)

    def test_sync_endpoint_passes_verify_all_flag(self):
        conn = _build_conn()
        try:
            with mock.patch("app.get_connection", return_value=conn), mock.patch(
                "app.penguinconnect_sync_conversations",
                return_value={"success": True, "mode": "backfill", "verify_all": True},
            ) as mock_sync:
                result = app_module.sync_penguinconnect_conversations(
                    app_module.PenguinConnectSyncRequest(mode="backfill", verify_all=True)
                )
        finally:
            conn.close()

        self.assertTrue(result["success"])
        mock_sync.assert_called_once_with(conn, mode="backfill", days=7, hours=None, verify_all=True)


if __name__ == "__main__":
    unittest.main()
