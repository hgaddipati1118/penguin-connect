import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import penguin_connect
import db
import watcher


class SyncIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.send_imessage_patcher = mock.patch(
            "penguin_connect.send_imessage",
            side_effect=AssertionError("Tests must mock send_imessage explicitly"),
        )
        self.send_imessage_patcher.start()
        with penguin_connect._sync_runtime_lock:
            penguin_connect._sync_runtime = penguin_connect._new_sync_runtime_state()
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_data_dir = db.DATA_DIR
        self.old_apple_messages_db = db.APPLE_MESSAGES_DB
        db.DATA_DIR = Path(self.tmpdir.name)
        db.DB_PATH = Path(self.tmpdir.name) / "cache.db"
        db.init_db()

    def tearDown(self):
        try:
            watcher.stop_watchers()
        except Exception:
            pass
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.send_imessage_patcher.stop()
        db.DB_PATH = self.old_db_path
        db.DATA_DIR = self.old_data_dir
        db.APPLE_MESSAGES_DB = self.old_apple_messages_db
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

    def test_sync_job_worker_commits_job_lease_before_running_sync(self):
        conn = db.get_connection()
        try:
            enqueued = penguin_connect.enqueue_sync_job(
                conn,
                mode="incremental",
                days=7,
                hours=None,
                verify_all=False,
                dedupe=False,
            )
            conn.commit()
        finally:
            conn.close()

        sync_started = threading.Event()
        allow_finish = threading.Event()
        worker_result = {}

        def fake_sync(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            sync_started.set()
            self.assertTrue(allow_finish.wait(1.0))
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        def run_worker():
            worker_conn = db.get_connection()
            try:
                worker_result["result"] = penguin_connect.run_sync_job_worker_once(worker_conn, owner="worker-a")
            finally:
                worker_conn.close()

        with mock.patch("penguin_connect.sync_conversations", side_effect=fake_sync):
            thread = threading.Thread(target=run_worker)
            thread.start()
            self.assertTrue(sync_started.wait(1.0))

            other_conn = sqlite3.connect(str(db.DB_PATH), timeout=0.1)
            try:
                other_conn.row_factory = sqlite3.Row
                other_conn.execute("PRAGMA journal_mode=WAL")
                other_conn.execute("PRAGMA foreign_keys=ON")
                second_job = penguin_connect.enqueue_sync_job(
                    other_conn,
                    mode="backfill",
                    days=7,
                    hours=None,
                    verify_all=False,
                    dedupe=False,
                )
                other_conn.commit()
            finally:
                other_conn.close()

            allow_finish.set()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(second_job["enqueued"])
        self.assertEqual(worker_result["result"]["queue_job_id"], enqueued["job_id"])
        self.assertEqual(worker_result["result"]["queue_job_status"], "succeeded")

    def test_run_incremental_sync_does_not_steal_startup_job(self):
        conn = db.get_connection()
        try:
            startup = penguin_connect.enqueue_sync_job(
                conn,
                mode="startup_catchup",
                days=7,
                hours=None,
                verify_all=False,
                dedupe=True,
            )
            incremental = penguin_connect.enqueue_sync_job(
                conn,
                mode="incremental",
                days=7,
                hours=None,
                verify_all=False,
                dedupe=True,
            )
            conn.commit()
        finally:
            conn.close()

        seen_modes: list[str] = []

        def fake_sync(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            seen_modes.append(mode)
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        with mock.patch("penguin_connect.sync_conversations", side_effect=fake_sync):
            result = penguin_connect.run_incremental_sync()

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["queue_job_id"], incremental["job_id"])
        self.assertEqual(seen_modes, ["incremental"])

        verify_conn = db.get_connection()
        try:
            startup_row = verify_conn.execute(
                "SELECT status, lease_owner, started_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
                (startup["job_id"],),
            ).fetchone()
            incremental_row = verify_conn.execute(
                "SELECT status, lease_owner, started_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
                (incremental["job_id"],),
            ).fetchone()
        finally:
            verify_conn.close()

        self.assertEqual(startup_row["status"], "queued")
        self.assertIsNone(startup_row["lease_owner"])
        self.assertIsNone(startup_row["started_at"])
        self.assertIsNone(startup_row["finished_at"])
        self.assertEqual(incremental_row["status"], "succeeded")

    def test_imessage_import_commits_pending_row_before_gmail_call(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com"]',
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'apple_messages', ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_import_lock",
                "iMessage;+;chat-import-lock",
                "Import Lock",
                "owner+am-import-lock@gmail.com",
            ),
        )
        conn.commit()
        conv = conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_import_lock",),
        ).fetchone()

        import_started = threading.Event()
        allow_finish = threading.Event()
        sync_result = {}

        def fake_import(_gmail_service, _raw_email, _thread_id, _unread, _in_reply_to):
            import_started.set()
            self.assertTrue(allow_finish.wait(1.0))
            return ({"id": "gmail-import-lock-1", "threadId": "thread-import-lock-1"}, None, "thread-import-lock-1")

        def run_sync():
            worker_conn = db.get_connection()
            try:
                sync_result["result"] = penguin_connect._sync_conversation_imessage_to_gmail(
                    worker_conn,
                    object(),
                    conv,
                    mode="startup_catchup",
                    days=7,
                    hours=None,
                    cutoff_iso=None,
                    verify_all=False,
                )
            finally:
                worker_conn.close()

        with mock.patch(
            "penguin_connect._fetch_apple_messages_messages_for_conversation",
            return_value=[
                {
                    "text": "hello",
                    "timestamp": "2026-03-13T22:00:00+00:00",
                    "is_from_me": False,
                    "attachments": None,
                    "native_message_id": "1",
                    "chat_id": "iMessage;+;chat-import-lock",
                }
            ],
        ), mock.patch(
            "penguin_connect._get_apple_messages_unread_count_for_conversation",
            return_value=1,
        ), mock.patch(
            "penguin_connect._resolve_imessage_sender_and_subject",
            return_value=("Import Lock", "Import Lock"),
        ), mock.patch(
            "penguin_connect._build_import_email",
            return_value="raw-email",
        ), mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            side_effect=fake_import,
        ), mock.patch(
            "penguin_connect._reconcile_conversation_gmail_read_state",
            return_value=None,
        ):
            thread = threading.Thread(target=run_sync)
            thread.start()
            self.assertTrue(import_started.wait(1.0))

            other_conn = sqlite3.connect(str(db.DB_PATH), timeout=0.1)
            try:
                other_conn.row_factory = sqlite3.Row
                other_conn.execute("PRAGMA journal_mode=WAL")
                other_conn.execute("PRAGMA foreign_keys=ON")
                second_job = penguin_connect.enqueue_sync_job(
                    other_conn,
                    mode="incremental",
                    days=7,
                    hours=None,
                    verify_all=False,
                    dedupe=False,
                )
                other_conn.commit()
            finally:
                other_conn.close()

            allow_finish.set()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(second_job["enqueued"])
        self.assertEqual(sync_result["result"]["imessage_imported"], 1)
        verify_conn = db.get_connection()
        try:
            row = verify_conn.execute(
                """SELECT gmail_message_id, gmail_thread_id
                   FROM penguin_connect_messages
                   WHERE conversation_id = ? AND provider_message_id = ?""",
                ("amc_import_lock", "imessage:1"),
            ).fetchone()
        finally:
            verify_conn.close()
        self.assertEqual(row["gmail_message_id"], "gmail-import-lock-1")
        self.assertEqual(row["gmail_thread_id"], "thread-import-lock-1")

    def test_incremental_sync_commits_discovery_before_continuing(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com"]',
            ),
        )
        conn.commit()
        conn.close()

        build_started = threading.Event()
        allow_finish = threading.Event()
        worker_result = {}

        def fake_discovery(worker_conn, gmail_email):
            worker_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
                   VALUES (?, 'apple_messages', ?, ?, ?, 'dm', '[]', ?, 'active')""",
                (
                    gmail_email,
                    "amc_discovery_commit",
                    "iMessage;-;+15550001111",
                    "Discovery Commit",
                    "owner+am-discovery@gmail.com",
                ),
            )

        def fake_build(*_args, **_kwargs):
            build_started.set()
            self.assertTrue(allow_finish.wait(1.0))
            return object(), None

        def run_sync():
            worker_conn = db.get_connection()
            try:
                worker_result["result"] = penguin_connect.sync_conversations(
                    worker_conn,
                    mode="incremental",
                    days=7,
                )
            finally:
                worker_conn.close()

        with mock.patch("penguin_connect.ensure_conversations_discovered", side_effect=fake_discovery), mock.patch(
            "penguin_connect._build_gmail_service",
            side_effect=fake_build,
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._select_conversations_for_sync",
            return_value=(
                [],
                {
                    "discovered_conversations": 1,
                    "selected_conversations": 0,
                    "selection_strategy": "test_selection",
                },
            ),
        ):
            thread = threading.Thread(target=run_sync)
            thread.start()
            self.assertTrue(build_started.wait(1.0))

            other_conn = sqlite3.connect(str(db.DB_PATH), timeout=0.1)
            try:
                other_conn.row_factory = sqlite3.Row
                other_conn.execute("PRAGMA journal_mode=WAL")
                other_conn.execute("PRAGMA foreign_keys=ON")
                second_job = penguin_connect.enqueue_sync_job(
                    other_conn,
                    mode="startup_catchup",
                    days=7,
                    hours=None,
                    verify_all=False,
                    dedupe=False,
                )
                other_conn.commit()
            finally:
                other_conn.close()

            allow_finish.set()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(second_job["enqueued"])
        self.assertTrue(worker_result["result"]["success"])

    def test_incremental_sync_commits_send_as_refresh_before_selection(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com"]',
            ),
        )
        conn.commit()
        conn.close()

        selection_started = threading.Event()
        allow_finish = threading.Event()
        worker_result = {}

        def fake_refresh(worker_conn, _gmail_service, gmail_email):
            worker_conn.execute(
                """UPDATE penguin_connect_accounts
                   SET primary_send_as = ?, send_as_aliases = ?, updated_at = datetime('now')
                   WHERE gmail_email = ?""",
                ("owner@gmail.com", '["owner@gmail.com"]', gmail_email),
            )
            return ["owner@gmail.com"], "owner@gmail.com"

        def fake_select(*_args, **_kwargs):
            selection_started.set()
            self.assertTrue(allow_finish.wait(1.0))
            return (
                [],
                {
                    "discovered_conversations": 0,
                    "selected_conversations": 0,
                    "selection_strategy": "test_selection",
                },
            )

        def run_sync():
            worker_conn = db.get_connection()
            try:
                worker_result["result"] = penguin_connect.sync_conversations(
                    worker_conn,
                    mode="incremental",
                    days=7,
                )
            finally:
                worker_conn.close()

        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=0), mock.patch(
            "penguin_connect._build_gmail_service",
            return_value=(object(), None),
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            side_effect=fake_refresh,
        ), mock.patch(
            "penguin_connect._select_conversations_for_sync",
            side_effect=fake_select,
        ):
            thread = threading.Thread(target=run_sync)
            thread.start()
            self.assertTrue(selection_started.wait(1.0))

            other_conn = sqlite3.connect(str(db.DB_PATH), timeout=0.1)
            try:
                other_conn.row_factory = sqlite3.Row
                other_conn.execute("PRAGMA journal_mode=WAL")
                other_conn.execute("PRAGMA foreign_keys=ON")
                second_job = penguin_connect.enqueue_sync_job(
                    other_conn,
                    mode="startup_catchup",
                    days=7,
                    hours=None,
                    verify_all=False,
                    dedupe=False,
                )
                other_conn.commit()
            finally:
                other_conn.close()

            allow_finish.set()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(second_job["enqueued"])
        self.assertTrue(worker_result["result"]["success"])

    def test_gmail_alias_activity_commits_poll_state_before_backstop_scan(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com"]',
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'apple_messages', ?, ?, ?, 'dm', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_poll_state_commit",
                "iMessage;-;+15550002222",
                "Poll State Commit",
                "owner+am-poll-state@gmail.com",
            ),
        )
        conn.commit()
        conversations = conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE gmail_email = ?",
            ("owner@gmail.com",),
        ).fetchall()
        conn.close()

        scan_started = threading.Event()
        allow_finish = threading.Event()
        worker_result = {}

        def fake_backstop(_gmail_service, _conversations):
            scan_started.set()
            self.assertTrue(allow_finish.wait(1.0))
            return {}, {}

        def run_scan():
            worker_conn = db.get_connection()
            try:
                worker_result["result"] = penguin_connect._list_recent_gmail_alias_activity(
                    worker_conn,
                    object(),
                    "owner@gmail.com",
                    conversations,
                )
            finally:
                worker_conn.close()

        with mock.patch(
            "penguin_connect._get_gmail_mailbox_history_id",
            return_value="12345",
        ), mock.patch(
            "penguin_connect._list_recent_sent_alias_mailbox_activity",
            side_effect=fake_backstop,
        ):
            thread = threading.Thread(target=run_scan)
            thread.start()
            self.assertTrue(scan_started.wait(1.0))

            other_conn = sqlite3.connect(str(db.DB_PATH), timeout=0.1)
            try:
                other_conn.row_factory = sqlite3.Row
                other_conn.execute("PRAGMA journal_mode=WAL")
                other_conn.execute("PRAGMA foreign_keys=ON")
                second_job = penguin_connect.enqueue_sync_job(
                    other_conn,
                    mode="incremental",
                    days=7,
                    hours=None,
                    verify_all=False,
                    dedupe=False,
                )
                other_conn.commit()
            finally:
                other_conn.close()

            allow_finish.set()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(second_job["enqueued"])
        verify_conn = db.get_connection()
        try:
            poll_state = verify_conn.execute(
                "SELECT last_gmail_history_id FROM penguin_connect_poll_state WHERE gmail_email = ?",
                ("owner@gmail.com",),
            ).fetchone()
        finally:
            verify_conn.close()

        self.assertEqual(poll_state["last_gmail_history_id"], "12345")
        self.assertEqual(worker_result["result"][1]["last_gmail_history_id"], "12345")

    def test_gmail_to_imessage_commits_ignored_row_before_fetching_next_message(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com"]',
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'apple_messages', ?, ?, ?, 'dm', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_ignore_commit",
                "iMessage;-;+15550003333",
                "Ignore Commit",
                "owner+am-ignore@gmail.com",
            ),
        )
        conn.commit()
        conv = conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_ignore_commit",),
        ).fetchone()
        conn.close()

        second_fetch_started = threading.Event()
        allow_finish = threading.Event()
        worker_result = {}

        def fake_gmail_execute(_fn):
            call_index = fake_gmail_execute.call_count
            fake_gmail_execute.call_count += 1
            if call_index == 0:
                return {
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Owner <owner@gmail.com>"},
                            {"name": "To", "value": conv["alias_email"]},
                            {"name": "Subject", "value": "Draft 1"},
                        ]
                    },
                    "labelIds": ["DRAFT"],
                    "internalDate": "1710376800000",
                    "historyId": "1",
                    "snippet": "draft one",
                }
            if call_index == 1:
                second_fetch_started.set()
                self.assertTrue(allow_finish.wait(1.0))
                return {
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Owner <owner@gmail.com>"},
                            {"name": "To", "value": conv["alias_email"]},
                            {"name": "Subject", "value": "Draft 2"},
                        ]
                    },
                    "labelIds": ["DRAFT"],
                    "internalDate": "1710376860000",
                    "historyId": "2",
                    "snippet": "draft two",
                }
            raise AssertionError(f"unexpected gmail execute call {call_index}")

        fake_gmail_execute.call_count = 0

        def run_sync():
            worker_conn = db.get_connection()
            try:
                worker_result["result"] = penguin_connect._sync_conversation_gmail_to_imessage(
                    worker_conn,
                    object(),
                    conv,
                    "owner@gmail.com",
                    ["owner@gmail.com"],
                    7,
                )
            finally:
                worker_conn.close()

        with mock.patch(
            "penguin_connect._list_gmail_messages_to_alias",
            return_value=[{"id": "draft-1"}, {"id": "draft-2"}],
        ), mock.patch(
            "penguin_connect._gmail_execute",
            side_effect=fake_gmail_execute,
        ):
            thread = threading.Thread(target=run_sync)
            thread.start()
            self.assertTrue(second_fetch_started.wait(1.0))

            other_conn = sqlite3.connect(str(db.DB_PATH), timeout=0.1)
            try:
                other_conn.row_factory = sqlite3.Row
                other_conn.execute("PRAGMA journal_mode=WAL")
                other_conn.execute("PRAGMA foreign_keys=ON")
                second_job = penguin_connect.enqueue_sync_job(
                    other_conn,
                    mode="startup_catchup",
                    days=7,
                    hours=None,
                    verify_all=False,
                    dedupe=False,
                )
                other_conn.commit()
            finally:
                other_conn.close()

            allow_finish.set()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(second_job["enqueued"])
        verify_conn = db.get_connection()
        try:
            ignored_count = verify_conn.execute(
                """SELECT COUNT(*)
                   FROM penguin_connect_messages
                   WHERE conversation_id = ?
                     AND provider = 'gmail'
                     AND direction = 'email_to_imessage'
                     AND json_extract(metadata, '$.reason') = 'gmail_draft_message'""",
                ("amc_ignore_commit",),
            ).fetchone()[0]
        finally:
            verify_conn.close()

        self.assertEqual(worker_result["result"]["email_to_imessage"], 0)
        self.assertEqual(ignored_count, 2)

    def test_init_db_backfills_bootstrap_marker_for_existing_sync_rows(self):
        conn = db.get_connection()
        conn.close()

        legacy_schema = db.SCHEMA.replace("    initial_sync_completed_at TEXT,\n", "").replace(
            "    initial_sync_empty_verified_at TEXT,\n",
            "",
        ).replace(
            "    last_message_ts TEXT,\n",
            "",
        ).replace(
            "    next_full_verify_at TEXT,\n",
            "",
        ).replace(
            "    full_verify_completed_at TEXT,\n",
            "",
        )

        raw_conn = sqlite3.connect(str(db.DB_PATH))
        try:
            raw_conn.executescript(legacy_schema)
            raw_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
                   VALUES ('owner@gmail.com', 'imessage', 'amc_legacy', 'chat-legacy', 'Legacy Conversation', 'dm', '[]', 'owner+am-legacy@gmail.com', 'active')"""
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction, sender_email,
                    sender_name, subject, body_text, message_timestamp, is_read, metadata)
                   VALUES ('amc_legacy', 'imessage', 'imessage:legacy-1', 'gmail-legacy-1', 'thread-legacy-1',
                           'imessage_to_email', 'owner+am-legacy@gmail.com', 'Legacy', 'Legacy Conversation',
                           'hello', '2026-03-04T10:00:00+00:00', 1, '{\"delivery_status\":\"delivered\"}')"""
            )
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
            conv = migrated_conn.execute(
                """SELECT conversation_id
                   FROM penguin_connect_conversations
                   WHERE imessage_chat_id = 'chat-legacy'"""
            ).fetchone()
            row = migrated_conn.execute(
                """SELECT initial_sync_completed_at, initial_sync_empty_verified_at, last_message_ts, next_full_verify_at, full_verify_completed_at
                   FROM penguin_connect_sync_state
                   WHERE conversation_id = ?""",
                (conv["conversation_id"],),
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIn("initial_sync_completed_at", columns)
        self.assertIn("initial_sync_empty_verified_at", columns)
        self.assertIn("last_message_ts", columns)
        self.assertIn("next_full_verify_at", columns)
        self.assertIn("full_verify_completed_at", columns)
        self.assertIsNotNone(conv)
        self.assertIsNotNone(row)
        self.assertEqual(row["initial_sync_completed_at"], "2026-03-04 10:05:00")
        self.assertIsNone(row["initial_sync_empty_verified_at"])
        self.assertEqual(row["last_message_ts"], "2026-03-04T10:00:00+00:00")
        self.assertIsNotNone(row["next_full_verify_at"])
        self.assertIsNone(row["full_verify_completed_at"])

    def test_init_db_clears_false_bootstrap_without_materialized_history(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, 'dm', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_false_bootstrap",
                "chat-false-bootstrap",
                "False Bootstrap",
                "owner+am-false-bootstrap@gmail.com",
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, initial_sync_completed_at, next_full_verify_at, full_verify_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_false_bootstrap",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-07T10:05:00+00:00",
                "2026-03-05T10:05:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            conv = migrated_conn.execute(
                """SELECT conversation_id
                   FROM penguin_connect_conversations
                   WHERE imessage_chat_id = 'chat-false-bootstrap'"""
            ).fetchone()
            row = migrated_conn.execute(
                """SELECT initial_sync_completed_at, next_full_verify_at, full_verify_completed_at
                   FROM penguin_connect_sync_state
                   WHERE conversation_id = ?""",
                (conv["conversation_id"],),
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIsNotNone(conv)
        self.assertIsNotNone(row)
        self.assertIsNone(row["initial_sync_completed_at"])
        self.assertIsNone(row["next_full_verify_at"])
        self.assertIsNone(row["full_verify_completed_at"])

    def test_init_db_backfills_missing_conversation_gmail_thread_id_from_messages(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, 'dm', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_thread_backfill",
                "chat-thread-backfill",
                "Thread Backfill",
                "owner+am-thread-backfill@gmail.com",
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, sender_name, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', ?, ?, ?, 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
            (
                "amc_thread_backfill",
                "imessage:thread-backfill-1",
                "gmail-thread-backfill-1",
                "thread-backfill-1",
                "owner+am-thread-backfill@gmail.com",
                "Thread Backfill",
                "Thread Backfill",
                "hello",
                "2026-03-04T10:00:00+00:00",
                json.dumps({"delivery_status": "delivered"}),
            ),
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            conv = migrated_conn.execute(
                """SELECT conversation_id, gmail_thread_id
                   FROM penguin_connect_conversations
                   WHERE imessage_chat_id = 'chat-thread-backfill'"""
            ).fetchone()
            row = migrated_conn.execute(
                "SELECT gmail_thread_id FROM penguin_connect_conversations WHERE conversation_id = ?",
                (conv["conversation_id"],),
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIsNotNone(conv)
        self.assertIsNotNone(row)
        self.assertEqual(row["gmail_thread_id"], "thread-backfill-1")

    def test_init_db_backfills_pending_gmail_delivery_bodies_to_latest_text(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, 'apple_messages', ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_pending",
                "RCS;-;+15127436385",
                "Taylor",
                '["+15127436385"]',
                "owner+am-pending@gmail.com",
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp, metadata)
               VALUES (?, 'gmail', 'gmail:pending', 'email_to_imessage', ?, '2026-03-10T10:00:00+00:00', ?)""",
            (
                "amc_pending",
                "Latest reply\n\nQuoted context:\n> Alice: Older line",
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "source_body_text": "Latest reply\n\nOn Tue, Mar 10, 2026 at 10:00 AM Alice <alice@example.com> wrote:\n> Older line",
                        "quoted_context_count": 1,
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            row = migrated_conn.execute(
                "SELECT body_text, metadata FROM penguin_connect_messages WHERE provider_message_id = 'gmail:pending'"
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertEqual(row["body_text"], "Latest reply")
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata["source_body_text"], "Latest reply")
        self.assertEqual(metadata["gmail_body_source"], "plain")
        self.assertTrue(metadata["gmail_quoted_content_removed"])
        self.assertNotIn("quoted_context_count", metadata)

    def test_init_db_backfills_self_authored_sender_names(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com", "ops@company.com"]',
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, 'apple_messages', ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_sender_backfill",
                "RCS;-;+15127436385",
                "Taylor",
                '["+15127436385"]',
                "owner+am-sender@gmail.com",
            ),
        )
        conn.executemany(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            [
                (
                    "amc_sender_backfill",
                    "gmail",
                    "gmail:self",
                    "email_to_imessage",
                    "owner@gmail.com",
                    "Owner <owner@gmail.com>",
                    "Email body",
                    "2026-03-11T10:00:00+00:00",
                    "{}",
                ),
                (
                    "amc_sender_backfill",
                    "manual",
                    "manual:self",
                    "manual_to_imessage",
                    "owner@gmail.com",
                    "owner@gmail.com",
                    "Manual body",
                    "2026-03-11T10:05:00+00:00",
                    "{}",
                ),
            ],
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            rows = migrated_conn.execute(
                """SELECT provider_message_id, sender_name
                   FROM penguin_connect_messages
                   WHERE provider_message_id IN ('gmail:self', 'manual:self')
                   ORDER BY provider_message_id""",
            ).fetchall()
        finally:
            migrated_conn.close()

        self.assertEqual(
            {row["provider_message_id"]: row["sender_name"] for row in rows},
            {
                "gmail:self": "Owner",
                "manual:self": "Me",
            },
        )

    def test_init_db_backfills_self_authored_imessage_sender_names(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_imessage_sender_backfill",
                "iMessage;+;chat-self",
                "Taylor",
                '["+15127436385"]',
                "owner+am-imessage@gmail.com",
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', 'imessage:self', 'imessage_to_email', ?, ?, ?, ?, 1, ?)""",
            (
                "amc_imessage_sender_backfill",
                "owner+am-imessage@gmail.com",
                "Taylor",
                "Sent from Messages",
                "2026-03-11T11:00:00+00:00",
                json.dumps({"is_from_me": True}),
            ),
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            row = migrated_conn.execute(
                """SELECT sender_name
                   FROM penguin_connect_messages
                   WHERE provider_message_id = 'imessage:self'"""
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["sender_name"], "Me")

    def test_init_db_backfills_historical_gmail_delivery_bodies_when_parser_improves(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_historical",
                "iMessage;+;chat36804801746842713",
                "Julian",
                '["+15127436385"]',
                "owner+am-historical@gmail.com",
            ),
        )
        conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp, metadata)
               VALUES (?, 'gmail', 'gmail:historical', 'email_to_imessage', ?, '2026-03-10T10:00:00+00:00', ?)""",
            (
                "amc_historical",
                "Haha, that&#39;s a feature!\n\nThomas Lentine\nFounder\n973-555-0101",
                json.dumps(
                    {
                        "delivery_status": "delivered",
                        "source_body_text": "Haha, that&#39;s a feature!\n\nThomas Lentine\nFounder\n973-555-0101",
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            row = migrated_conn.execute(
                "SELECT body_text, metadata FROM penguin_connect_messages WHERE provider_message_id = 'gmail:historical'"
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertEqual(row["body_text"], "Haha, that's a feature!")
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata["source_body_text"], "Haha, that's a feature!")
        self.assertTrue(metadata["gmail_signature_removed"])

    def test_init_db_backfills_wrapped_reply_header_from_historical_gmail_delivery(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_wrapped_reply_historical",
                "iMessage;+;chat-wrapped-reply",
                "Crow",
                '["+15127436385"]',
                "owner+am-wrapped@gmail.com",
            ),
        )
        raw_body = (
            "Test email\r\n\r\nOn Fri, Mar 13, 2026 at 1:38 PM Dhruv Roonga <\r\n"
            "owner+am-wrapped@gmail.com> wrote:\r\n\r\n"
            "> older quoted text"
        )
        conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp, metadata)
               VALUES (?, 'gmail', 'gmail:wrapped-historical', 'email_to_imessage', ?, '2026-03-13T20:40:48+00:00', ?)""",
            (
                "amc_wrapped_reply_historical",
                raw_body,
                json.dumps(
                    {
                        "delivery_status": "delivered",
                        "source_body_text": raw_body,
                        "source_body_text_raw": raw_body,
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            row = migrated_conn.execute(
                "SELECT body_text, metadata FROM penguin_connect_messages WHERE provider_message_id = 'gmail:wrapped-historical'"
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertEqual(row["body_text"], "Test email")
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata["source_body_text"], "Test email")
        self.assertEqual(metadata["source_body_text_raw"], raw_body)
        self.assertTrue(metadata["gmail_quoted_content_removed"])
        self.assertTrue(metadata["gmail_body_safe_for_send"])

    def test_init_db_backfills_historical_gmail_delivery_using_html_first_extraction(self):
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_historical_html_first",
                "iMessage;+;chat-html-first",
                "Crow",
                '["+15127436385"]',
                "owner+am-html-first@gmail.com",
            ),
        )
        raw_plain = (
            "Test email\r\n\r\nSent with [Slashy](https://slashy.com)\r\n\r\nOn Fri, Mar 13, 2026 at 1:38 PM Dhruv Roonga <\r\n"
            "owner+am-html-first@gmail.com> wrote:\r\n\r\n"
            "> older quoted text"
        )
        raw_html = (
            "<div>Test email</div>"
            '<div class="slashy-signature"><br/>Sent with <a href="https://slashy.com">Slashy</a></div>'
            '<div class="gmail_quote">'
            '<div class="gmail_attr">On Fri, Mar 13, 2026 at 1:38 PM Dhruv Roonga wrote:</div>'
            "<blockquote>older quoted text</blockquote>"
            "</div>"
        )
        conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp, metadata)
               VALUES (?, 'gmail', 'gmail:historical-html-first', 'email_to_imessage', ?, '2026-03-13T20:40:48+00:00', ?)""",
            (
                "amc_historical_html_first",
                raw_plain,
                json.dumps(
                    {
                        "delivery_status": "delivered",
                        "source_body_text": raw_plain,
                        "source_body_text_raw": raw_plain,
                        "source_body_html_raw": raw_html,
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            row = migrated_conn.execute(
                "SELECT body_text, metadata FROM penguin_connect_messages WHERE provider_message_id = 'gmail:historical-html-first'"
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertEqual(row["body_text"], "Test email\nSent with\nSlashy")
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata["source_body_text"], "Test email\nSent with\nSlashy")
        self.assertEqual(metadata["gmail_body_source"], "html")
        self.assertEqual(metadata["source_body_html_raw"], raw_html)
        self.assertTrue(metadata["gmail_quoted_content_removed"])
        self.assertTrue(metadata["gmail_body_safe_for_send"])

    def test_init_db_migrates_legacy_conversation_ids_to_provider_aware_ids(self):
        conn = db.get_connection()
        conn.close()

        legacy_schema = db.SCHEMA.replace("    source_provider TEXT NOT NULL DEFAULT 'imessage',\n", "").replace(
            "    UNIQUE(gmail_email, source_provider, imessage_chat_id)\n",
            "    UNIQUE(gmail_email, imessage_chat_id)\n",
        )
        old_id = penguin_connect._legacy_conversation_id("owner@gmail.com", "chat-legacy")
        new_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-legacy", "imessage")

        raw_conn = sqlite3.connect(str(db.DB_PATH))
        try:
            raw_conn.executescript(legacy_schema)
            raw_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
                   VALUES (?, ?, ?, ?, 'dm', '[]', ?, 'active')""",
                ("owner@gmail.com", old_id, "chat-legacy", "Legacy Chat", "owner+am-legacy@gmail.com"),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_aliases
                   (conversation_id, alias_email, alias_local_part, status)
                   VALUES (?, ?, ?, 'active')""",
                (old_id, "owner+am-legacy@gmail.com", "owner+am-legacy"),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp)
                   VALUES (?, 'imessage', 'imessage:legacy-1', 'imessage_to_email', 'hello', '2026-03-04T10:00:00+00:00')""",
                (old_id,),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_sync_state
                   (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
                   VALUES (?, '2026-03-04T10:00:00+00:00', NULL, '2026-03-04 10:05:00', '2026-03-04 10:05:00')""",
                (old_id,),
            )
            raw_conn.commit()
        finally:
            raw_conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            conv = migrated_conn.execute(
                "SELECT conversation_id, source_provider FROM penguin_connect_conversations WHERE imessage_chat_id = ?",
                ("chat-legacy",),
            ).fetchone()
            provider_unique_indexes = []
            for index_row in migrated_conn.execute("PRAGMA index_list(penguin_connect_conversations)").fetchall():
                if not index_row["unique"]:
                    continue
                columns = [
                    info["name"]
                    for info in migrated_conn.execute(f"PRAGMA index_info('{index_row['name']}')").fetchall()
                ]
                provider_unique_indexes.append(columns)
            alias = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_aliases WHERE alias_email = ?",
                ("owner+am-legacy@gmail.com",),
            ).fetchone()
            message = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_messages WHERE provider_message_id = ?",
                ("imessage:legacy-1",),
            ).fetchone()
            sync_state = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_sync_state WHERE conversation_id = ?",
                (new_id,),
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertEqual(conv["conversation_id"], new_id)
        self.assertEqual(conv["source_provider"], "imessage")
        self.assertIn(["gmail_email", "source_provider", "imessage_chat_id"], provider_unique_indexes)
        self.assertEqual(alias["conversation_id"], new_id)
        self.assertEqual(message["conversation_id"], new_id)
        self.assertEqual(sync_state["conversation_id"], new_id)
        self.assertNotEqual(old_id, new_id)

    def test_init_db_migrates_apple_messages_routes_to_guid_and_service_provider(self):
        conn = db.get_connection()
        conn.close()

        legacy_schema = (
            db.SCHEMA.replace("    imessage_chat_identifier TEXT,\n", "")
            .replace("    imessage_service_name TEXT,\n", "")
        )
        old_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-legacy", "imessage")
        new_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "SMS;+;chat-legacy-guid", "sms")

        apple_messages_db = Path(self.tmpdir.name) / "messages-chat.db"
        messages_conn = sqlite3.connect(str(apple_messages_db))
        try:
            messages_conn.executescript(
                """
                CREATE TABLE chat (
                    ROWID INTEGER PRIMARY KEY,
                    guid TEXT,
                    chat_identifier TEXT,
                    display_name TEXT,
                    service_name TEXT,
                    is_archived INTEGER DEFAULT 0
                );
                CREATE TABLE message (
                    ROWID INTEGER PRIMARY KEY,
                    date INTEGER
                );
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                """
            )
            messages_conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name, is_archived) VALUES (1, ?, ?, '', 'SMS', 0)",
                ("SMS;+;chat-legacy-guid", "chat-legacy"),
            )
            messages_conn.execute("INSERT INTO message(ROWID, date) VALUES (1, 1000)")
            messages_conn.execute("INSERT INTO chat_message_join(chat_id, message_id) VALUES (1, 1)")
            messages_conn.commit()
        finally:
            messages_conn.close()
        db.APPLE_MESSAGES_DB = apple_messages_db

        raw_conn = sqlite3.connect(str(db.DB_PATH))
        try:
            raw_conn.executescript(legacy_schema)
            raw_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
                   VALUES (?, 'imessage', ?, ?, ?, 'dm', '[]', ?, 'active')""",
                ("owner@gmail.com", old_id, "chat-legacy", "Legacy Chat", "owner+am-legacy@gmail.com"),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_aliases
                   (conversation_id, alias_email, alias_local_part, status)
                   VALUES (?, ?, ?, 'active')""",
                (old_id, "owner+am-legacy@gmail.com", "owner+am-legacy"),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp)
                   VALUES (?, 'gmail', 'gmail:legacy-1', 'email_to_imessage', 'hello', '2026-03-04T10:00:00+00:00')""",
                (old_id,),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_sync_state
                   (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
                   VALUES (?, NULL, '2026-03-04T10:00:00+00:00', '2026-03-04 10:05:00', '2026-03-04 10:05:00')""",
                (old_id,),
            )
            raw_conn.commit()
        finally:
            raw_conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            conv = migrated_conn.execute(
                """SELECT conversation_id, source_provider, imessage_chat_id, imessage_chat_identifier, imessage_service_name
                   FROM penguin_connect_conversations
                   WHERE conversation_id = ?""",
                (new_id,),
            ).fetchone()
            alias = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_aliases WHERE alias_email = ?",
                ("owner+am-legacy@gmail.com",),
            ).fetchone()
            message = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_messages WHERE provider_message_id = ?",
                ("gmail:legacy-1",),
            ).fetchone()
            sync_state = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_sync_state WHERE conversation_id = ?",
                (new_id,),
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIsNotNone(conv)
        self.assertEqual(conv["conversation_id"], new_id)
        self.assertEqual(conv["source_provider"], "sms")
        self.assertEqual(conv["imessage_chat_id"], "SMS;+;chat-legacy-guid")
        self.assertEqual(conv["imessage_chat_identifier"], "chat-legacy")
        self.assertEqual(conv["imessage_service_name"], "SMS")
        self.assertEqual(alias["conversation_id"], new_id)
        self.assertEqual(message["conversation_id"], new_id)
        self.assertEqual(sync_state["conversation_id"], new_id)

    def test_init_db_resolves_ambiguous_apple_messages_identifier_by_timestamp_overlap(self):
        conn = db.get_connection()
        conn.close()

        legacy_schema = (
            db.SCHEMA.replace("    imessage_chat_identifier TEXT,\n", "")
            .replace("    imessage_service_name TEXT,\n", "")
        )
        old_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-shared", "imessage")
        new_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "SMS;+;chat-shared", "sms")

        sms_ts = int(3_000_000_000)
        imessage_ts = int(1_000_000_000)
        sms_iso = (db.APPLE_MESSAGES_EPOCH + timedelta(seconds=sms_ts / 1_000_000_000)).isoformat()

        apple_messages_db = Path(self.tmpdir.name) / "messages-chat-ambiguous.db"
        messages_conn = sqlite3.connect(str(apple_messages_db))
        try:
            messages_conn.executescript(
                """
                CREATE TABLE chat (
                    ROWID INTEGER PRIMARY KEY,
                    guid TEXT,
                    chat_identifier TEXT,
                    display_name TEXT,
                    service_name TEXT,
                    is_archived INTEGER DEFAULT 0
                );
                CREATE TABLE message (
                    ROWID INTEGER PRIMARY KEY,
                    date INTEGER
                );
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                """
            )
            messages_conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name, is_archived) VALUES (1, ?, ?, '', 'iMessage', 0)",
                ("iMessage;+;chat-shared", "chat-shared"),
            )
            messages_conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name, is_archived) VALUES (2, ?, ?, '', 'SMS', 0)",
                ("SMS;+;chat-shared", "chat-shared"),
            )
            messages_conn.execute("INSERT INTO message(ROWID, date) VALUES (1, ?)", (imessage_ts,))
            messages_conn.execute("INSERT INTO message(ROWID, date) VALUES (2, ?)", (sms_ts,))
            messages_conn.execute("INSERT INTO chat_message_join(chat_id, message_id) VALUES (1, 1)")
            messages_conn.execute("INSERT INTO chat_message_join(chat_id, message_id) VALUES (2, 2)")
            messages_conn.commit()
        finally:
            messages_conn.close()
        db.APPLE_MESSAGES_DB = apple_messages_db

        raw_conn = sqlite3.connect(str(db.DB_PATH))
        try:
            raw_conn.executescript(legacy_schema)
            raw_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
                   VALUES (?, 'imessage', ?, ?, ?, 'dm', '[]', ?, 'active')""",
                ("owner@gmail.com", old_id, "chat-shared", "Legacy Shared Chat", "owner+am-shared@gmail.com"),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp)
                   VALUES (?, 'imessage', 'imessage:legacy-1', 'imessage_to_email', 'hello', ?)""",
                (old_id, sms_iso),
            )
            raw_conn.commit()
        finally:
            raw_conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            conv = migrated_conn.execute(
                """SELECT conversation_id, source_provider, imessage_chat_id, imessage_chat_identifier, imessage_service_name
                   FROM penguin_connect_conversations
                   WHERE conversation_id = ?""",
                (new_id,),
            ).fetchone()
            moved_message = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_messages WHERE provider_message_id = 'imessage:legacy-1'"
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIsNotNone(conv)
        self.assertEqual(conv["conversation_id"], new_id)
        self.assertEqual(conv["source_provider"], "sms")
        self.assertEqual(conv["imessage_chat_id"], "SMS;+;chat-shared")
        self.assertEqual(conv["imessage_chat_identifier"], "chat-shared")
        self.assertEqual(conv["imessage_service_name"], "SMS")
        self.assertEqual(moved_message["conversation_id"], new_id)

    def test_init_db_resolves_ambiguous_apple_messages_identifier_when_only_one_route_has_activity(self):
        conn = db.get_connection()
        conn.close()

        legacy_schema = (
            db.SCHEMA.replace("    imessage_chat_identifier TEXT,\n", "")
            .replace("    imessage_service_name TEXT,\n", "")
        )
        old_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-group-shared", "imessage")
        new_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "SMS;+;chat-group-shared", "sms")

        sms_ts = int(4_000_000_000)
        apple_messages_db = Path(self.tmpdir.name) / "messages-chat-single-active-route.db"
        messages_conn = sqlite3.connect(str(apple_messages_db))
        try:
            messages_conn.executescript(
                """
                CREATE TABLE chat (
                    ROWID INTEGER PRIMARY KEY,
                    guid TEXT,
                    chat_identifier TEXT,
                    display_name TEXT,
                    service_name TEXT,
                    is_archived INTEGER DEFAULT 0
                );
                CREATE TABLE message (
                    ROWID INTEGER PRIMARY KEY,
                    date INTEGER
                );
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                """
            )
            messages_conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name, is_archived) VALUES (1, ?, ?, 'Legacy Group', 'iMessage', 0)",
                ("iMessage;+;chat-group-shared", "chat-group-shared"),
            )
            messages_conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name, is_archived) VALUES (2, ?, ?, 'Legacy Group', 'SMS', 0)",
                ("SMS;+;chat-group-shared", "chat-group-shared"),
            )
            messages_conn.execute("INSERT INTO message(ROWID, date) VALUES (1, ?)", (sms_ts,))
            messages_conn.execute("INSERT INTO chat_message_join(chat_id, message_id) VALUES (2, 1)")
            messages_conn.commit()
        finally:
            messages_conn.close()
        db.APPLE_MESSAGES_DB = apple_messages_db

        raw_conn = sqlite3.connect(str(db.DB_PATH))
        try:
            raw_conn.executescript(legacy_schema)
            raw_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
                   VALUES (?, 'imessage', ?, ?, ?, 'group', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    old_id,
                    "chat-group-shared",
                    "Legacy Group",
                    '["+15127436385","+14155550101"]',
                    "owner+am-group@gmail.com",
                ),
            )
            raw_conn.commit()
        finally:
            raw_conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            conv = migrated_conn.execute(
                """SELECT conversation_id, source_provider, imessage_chat_id, imessage_chat_identifier, imessage_service_name
                   FROM penguin_connect_conversations
                   WHERE conversation_id = ?""",
                (new_id,),
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIsNotNone(conv)
        self.assertEqual(conv["conversation_id"], new_id)
        self.assertEqual(conv["source_provider"], "sms")
        self.assertEqual(conv["imessage_chat_id"], "SMS;+;chat-group-shared")
        self.assertEqual(conv["imessage_chat_identifier"], "chat-group-shared")
        self.assertEqual(conv["imessage_service_name"], "SMS")

    def test_init_db_merges_legacy_group_row_into_existing_canonical_route(self):
        conn = db.get_connection()
        conn.close()

        legacy_schema = (
            db.SCHEMA.replace("    imessage_chat_identifier TEXT,\n", "")
            .replace("    imessage_service_name TEXT,\n", "")
        )
        old_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-group-existing", "imessage")
        target_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", "SMS;+;chat-group-existing", "sms")

        sms_ts = int(5_000_000_000)
        apple_messages_db = Path(self.tmpdir.name) / "messages-chat-existing-target.db"
        messages_conn = sqlite3.connect(str(apple_messages_db))
        try:
            messages_conn.executescript(
                """
                CREATE TABLE chat (
                    ROWID INTEGER PRIMARY KEY,
                    guid TEXT,
                    chat_identifier TEXT,
                    display_name TEXT,
                    service_name TEXT,
                    is_archived INTEGER DEFAULT 0
                );
                CREATE TABLE message (
                    ROWID INTEGER PRIMARY KEY,
                    date INTEGER
                );
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                """
            )
            messages_conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name, is_archived) VALUES (1, ?, ?, 'Legacy Group', 'iMessage', 0)",
                ("iMessage;+;chat-group-existing", "chat-group-existing"),
            )
            messages_conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name, is_archived) VALUES (2, ?, ?, 'Legacy Group', 'SMS', 0)",
                ("SMS;+;chat-group-existing", "chat-group-existing"),
            )
            messages_conn.execute("INSERT INTO message(ROWID, date) VALUES (1, ?)", (sms_ts,))
            messages_conn.execute("INSERT INTO chat_message_join(chat_id, message_id) VALUES (2, 1)")
            messages_conn.commit()
        finally:
            messages_conn.close()
        db.APPLE_MESSAGES_DB = apple_messages_db

        raw_conn = sqlite3.connect(str(db.DB_PATH))
        try:
            raw_conn.executescript(legacy_schema)
            raw_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
                   VALUES (?, 'imessage', ?, ?, ?, 'group', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    old_id,
                    "chat-group-existing",
                    "Legacy Group",
                    '["+15127436385","+14155550101"]',
                    "owner+am-legacy-group@gmail.com",
                ),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, source_provider, conversation_id, imessage_chat_id, imessage_chat_identifier, imessage_service_name,
                    display_name, chat_type, participants, alias_email, status)
                   VALUES (?, 'sms', ?, ?, ?, 'SMS', ?, 'group', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    target_id,
                    "SMS;+;chat-group-existing",
                    "chat-group-existing",
                    "Legacy Group",
                    '["+15127436385","+14155550101"]',
                    "owner+am-target-group@gmail.com",
                ),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_aliases
                   (conversation_id, alias_email, alias_local_part, status)
                   VALUES (?, ?, ?, 'active')""",
                (old_id, "owner+am-legacy-group@gmail.com", "owner+am-legacy-group"),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, body_text, message_timestamp)
                   VALUES (?, 'gmail', 'gmail:legacy-group-1', 'email_to_imessage', 'hello', '2026-03-01T10:00:00+00:00')""",
                (old_id,),
            )
            raw_conn.execute(
                """INSERT INTO penguin_connect_sync_state
                   (conversation_id, last_imessage_ts, last_gmail_ts, last_message_ts, initial_sync_completed_at, last_synced_at, updated_at)
                   VALUES (?, '2026-03-01T10:00:00+00:00', '2026-03-01T10:05:00+00:00', '2026-03-01T10:05:00+00:00',
                           '2026-03-01T10:06:00+00:00', '2026-03-01T10:06:00+00:00', '2026-03-01T10:06:00+00:00')""",
                (old_id,),
            )
            raw_conn.commit()
        finally:
            raw_conn.close()

        db.init_db()

        migrated_conn = db.get_connection()
        try:
            target = migrated_conn.execute(
                """SELECT conversation_id, source_provider, imessage_chat_id, imessage_chat_identifier, imessage_service_name, alias_email
                   FROM penguin_connect_conversations
                   WHERE conversation_id = ?""",
                (target_id,),
            ).fetchone()
            legacy = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_conversations WHERE conversation_id = ?",
                (old_id,),
            ).fetchone()
            alias = migrated_conn.execute(
                "SELECT conversation_id, status FROM penguin_connect_aliases WHERE alias_email = ?",
                ("owner+am-legacy-group@gmail.com",),
            ).fetchone()
            message = migrated_conn.execute(
                "SELECT conversation_id FROM penguin_connect_messages WHERE provider_message_id = 'gmail:legacy-group-1'"
            ).fetchone()
            sync_state = migrated_conn.execute(
                "SELECT conversation_id, last_gmail_ts FROM penguin_connect_sync_state WHERE conversation_id = ?",
                (target_id,),
            ).fetchone()
        finally:
            migrated_conn.close()

        self.assertIsNotNone(target)
        self.assertIsNone(legacy)
        self.assertEqual(target["source_provider"], "sms")
        self.assertEqual(target["imessage_chat_id"], "SMS;+;chat-group-existing")
        self.assertEqual(alias["conversation_id"], target_id)
        self.assertEqual(alias["status"], "active")
        self.assertEqual(message["conversation_id"], target_id)
        self.assertEqual(sync_state["conversation_id"], target_id)
        self.assertEqual(sync_state["last_gmail_ts"], "2026-03-01T10:05:00+00:00")


if __name__ == "__main__":
    unittest.main()
