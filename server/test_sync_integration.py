import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import penguin_connect
import db
import watcher


class SyncIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_data_dir = db.DATA_DIR
        db.DATA_DIR = Path(self.tmpdir.name)
        db.DB_PATH = Path(self.tmpdir.name) / "cache.db"
        db.init_db()

    def tearDown(self):
        try:
            watcher.stop_watchers()
        except Exception:
            pass
        db.DB_PATH = self.old_db_path
        db.DATA_DIR = self.old_data_dir
        self.tmpdir.cleanup()

    def test_watcher_and_manual_sync_are_serialized(self):
        started = threading.Event()
        manual_done = threading.Event()
        state_lock = threading.Lock()
        active = 0
        peak = 0
        results = []

        def fake_sync_unlocked(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            started.set()
            time.sleep(0.15)
            with state_lock:
                active -= 1
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        def run_manual_sync():
            conn = db.get_connection()
            try:
                results.append(penguin_connect.sync_conversations(conn, mode="incremental", days=7))
            finally:
                conn.close()
                manual_done.set()

        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_POLL_SECONDS": "10", "PENGUIN_CONNECT_POLL_INITIAL_DELAY_SECONDS": "0"},
            clear=False,
        ), mock.patch(
            "penguin_connect._initial_sync_bootstrapped",
            return_value=True,
        ), mock.patch(
            "penguin_connect._sync_conversations_unlocked",
            side_effect=fake_sync_unlocked,
        ):
            watcher.start_watchers()
            self.assertTrue(started.wait(1.0))

            manual_thread = threading.Thread(target=run_manual_sync)
            manual_thread.start()

            # Manual sync should be blocked while poller owns the sync lock.
            time.sleep(0.03)
            self.assertFalse(manual_done.is_set())

            manual_thread.join(timeout=2.0)
            self.assertTrue(manual_done.is_set())

        self.assertEqual(peak, 1)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])

    def test_many_parallel_manual_sync_calls_still_single_flight(self):
        state_lock = threading.Lock()
        active = 0
        peak = 0
        results = []

        def fake_sync_unlocked(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        def run_manual_sync():
            conn = db.get_connection()
            try:
                results.append(penguin_connect.sync_conversations(conn, mode="incremental", days=7))
            finally:
                conn.close()

        threads = [threading.Thread(target=run_manual_sync) for _ in range(8)]
        with mock.patch("penguin_connect._sync_conversations_unlocked", side_effect=fake_sync_unlocked):
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=2.0)

        self.assertEqual(peak, 1)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(r["success"] for r in results))

    def test_init_db_backfills_bootstrap_marker_for_existing_sync_rows(self):
        conn = db.get_connection()
        conn.close()

        legacy_schema = db.SCHEMA.replace("    initial_sync_completed_at TEXT,\n", "").replace(
            "    last_message_ts TEXT,\n",
            "",
        )

        raw_conn = sqlite3.connect(str(db.DB_PATH))
        try:
            raw_conn.executescript(legacy_schema)
            raw_conn.execute(
                """INSERT INTO penguin_connect_sync_state
                   (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
                   VALUES ('amc_legacy', '2026-03-04T10:00:00+00:00', '2026-03-04T10:00:00+00:00', '2026-03-04 10:05:00', '2026-03-04 10:05:00')"""
            )
            raw_conn.commit()
        finally:
            raw_conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            columns = {row[1] for row in migrated_conn.execute("PRAGMA table_info(penguin_connect_sync_state)").fetchall()}
            row = migrated_conn.execute(
                "SELECT initial_sync_completed_at, last_message_ts FROM penguin_connect_sync_state WHERE conversation_id = 'amc_legacy'"
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIn("initial_sync_completed_at", columns)
        self.assertIn("last_message_ts", columns)
        self.assertIsNotNone(row)
        self.assertEqual(row["initial_sync_completed_at"], "2026-03-04 10:05:00")
        self.assertEqual(row["last_message_ts"], "2026-03-04T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
