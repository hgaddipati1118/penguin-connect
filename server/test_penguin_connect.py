import base64
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest import mock

import penguin_connect
import browse_sources
import db
from db import SCHEMA


class PenguinConnectTests(unittest.TestCase):
    def setUp(self):
        with penguin_connect._sync_runtime_lock:
            penguin_connect._sync_runtime = penguin_connect._new_sync_runtime_state()
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.signature_markers_patcher = mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_SIGNATURE_MARKERS_FILE": str(Path(self.tmpdir.name) / "missing-signature-markers.json"),
                "PENGUIN_CONNECT_EXCLUDED_CHATS_FILE": str(Path(self.tmpdir.name) / "missing-excluded-chats.json"),
            },
            clear=False,
        )
        self.signature_markers_patcher.start()
        self.send_imessage_patcher = mock.patch(
            "penguin_connect.send_imessage",
            side_effect=AssertionError("Tests must mock send_imessage explicitly"),
        )
        self.send_imessage_patcher.start()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com", "ops@company.com"]',
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_test",
                "chat-123",
                "Family Group",
                "owner+am-test@gmail.com",
            ),
        )

    def tearDown(self):
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.send_imessage_patcher.stop()
        self.conn.close()
        self.signature_markers_patcher.stop()
        self.tmpdir.cleanup()

    def _conversation_row(self):
        return self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_test",),
        ).fetchone()

    def test_conversation_id_is_deterministic(self):
        one = penguin_connect.deterministic_conversation_id("Owner@Gmail.com", "chat-A")
        two = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-A")
        three = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-B")
        four = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-A", "telegram")
        self.assertEqual(one, two)
        self.assertNotEqual(one, three)
        self.assertNotEqual(one, four)

    def test_send_to_source_conversation_logs_attempt_and_result(self):
        conv = self._conversation_row()

        with mock.patch("penguin_connect.log_action") as mock_log, mock.patch(
            "penguin_connect.send_imessage",
            return_value=(True, None),
        ):
            ok, error = penguin_connect._send_to_source_conversation(conv, "hello there")

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual([call.args[0] for call in mock_log.call_args_list], ["source_send_attempt", "source_send_result"])
        second_call = mock_log.call_args_list[1]
        self.assertTrue(second_call.kwargs["ok"])
        self.assertEqual(second_call.kwargs["body_length"], len("hello there"))
        self.assertNotIn("hello there", json.dumps(second_call.kwargs))

    def test_sync_window_cutoff_prefers_hours_override(self):
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 3, 7, 12, 0, tzinfo=tz or timezone.utc)

        with mock.patch("penguin_connect.datetime", FixedDateTime):
            cutoff = penguin_connect._sync_window_cutoff(days=7, hours=5)

        self.assertEqual(cutoff.isoformat(), "2026-03-07T07:00:00+00:00")

    def test_default_gmail_scopes_include_full_mailbox_access(self):
        self.assertIn("https://mail.google.com/", penguin_connect.GMAIL_SCOPES)

    def test_backfill_gmail_write_pause_defaults_for_backfill_and_zero_for_incremental(self):
        pause_backfill = penguin_connect._sync_gmail_write_pause_seconds("backfill", verify_all=False)
        pause_incremental = penguin_connect._sync_gmail_write_pause_seconds("incremental", verify_all=False)
        self.assertGreaterEqual(pause_backfill, 0.0)
        self.assertEqual(pause_incremental, 0.0)

    def test_backfill_gmail_write_pause_uses_env_override(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_BACKFILL_WRITE_PAUSE_SECONDS": "0.8"}, clear=False):
            pause = penguin_connect._sync_gmail_write_pause_seconds("backfill", verify_all=False)
        self.assertEqual(pause, 0.8)

    def test_backfill_gmail_write_pause_scales_with_rate_limit_streak(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_rate_limit_streak)
               VALUES (?, ?)""",
            ("owner@gmail.com", 3),
        )

        pause = penguin_connect._sync_gmail_write_pause_seconds(
            "startup_catchup",
            verify_all=False,
            conn=self.conn,
            gmail_email="owner@gmail.com",
        )

        self.assertEqual(pause, 1.2)

    def test_reserve_gmail_write_budget_consumes_backfill_and_total_tokens(self):
        now_dt = datetime(2026, 3, 15, 20, 0, 0, tzinfo=timezone.utc)
        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_GMAIL_WRITE_BUDGET_UNITS_PER_MINUTE": "120",
                "PENGUIN_CONNECT_GMAIL_BACKFILL_WRITE_BUDGET_UNITS_PER_MINUTE": "60",
                "PENGUIN_CONNECT_GMAIL_WRITE_OPERATION_COST_UNITS": "25",
            },
            clear=False,
        ):
            reservation = penguin_connect._reserve_gmail_write_budget(
                self.conn,
                "owner@gmail.com",
                "backfill",
                now_dt=now_dt,
            )

        self.assertTrue(reservation["granted"])
        self.assertEqual(reservation["total_tokens_remaining"], 95.0)
        self.assertEqual(reservation["backfill_tokens_remaining"], 35.0)
        row = self.conn.execute(
            """SELECT gmail_write_budget_tokens, gmail_backfill_budget_tokens, gmail_write_budget_updated_at
               FROM penguin_connect_poll_state
               WHERE gmail_email = ?""",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertEqual(row["gmail_write_budget_tokens"], 95.0)
        self.assertEqual(row["gmail_backfill_budget_tokens"], 35.0)
        self.assertEqual(row["gmail_write_budget_updated_at"], now_dt.isoformat())

    def test_reserve_gmail_write_budget_preserves_backfill_reserve_for_incremental_lane(self):
        now_dt = datetime(2026, 3, 15, 20, 0, 0, tzinfo=timezone.utc)
        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_GMAIL_WRITE_BUDGET_UNITS_PER_MINUTE": "120",
                "PENGUIN_CONNECT_GMAIL_BACKFILL_WRITE_BUDGET_UNITS_PER_MINUTE": "60",
                "PENGUIN_CONNECT_GMAIL_WRITE_OPERATION_COST_UNITS": "25",
            },
            clear=False,
        ):
            reservation = penguin_connect._reserve_gmail_write_budget(
                self.conn,
                "owner@gmail.com",
                "incremental",
                now_dt=now_dt,
            )

        self.assertTrue(reservation["granted"])
        self.assertEqual(reservation["total_tokens_remaining"], 95.0)
        self.assertEqual(reservation["backfill_tokens_remaining"], 60.0)

    def test_reserve_gmail_write_budget_returns_retry_after_when_backfill_reserve_is_empty(self):
        now_dt = datetime(2026, 3, 15, 20, 0, 0, tzinfo=timezone.utc)
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_write_budget_tokens, gmail_backfill_budget_tokens, gmail_write_budget_updated_at)
               VALUES (?, ?, ?, ?)""",
            ("owner@gmail.com", 120.0, 10.0, now_dt.isoformat()),
        )

        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_GMAIL_WRITE_BUDGET_UNITS_PER_MINUTE": "120",
                "PENGUIN_CONNECT_GMAIL_BACKFILL_WRITE_BUDGET_UNITS_PER_MINUTE": "60",
                "PENGUIN_CONNECT_GMAIL_WRITE_OPERATION_COST_UNITS": "25",
            },
            clear=False,
        ):
            reservation = penguin_connect._reserve_gmail_write_budget(
                self.conn,
                "owner@gmail.com",
                "backfill",
                now_dt=now_dt,
            )

        self.assertFalse(reservation["granted"])
        self.assertEqual(reservation["retry_after_seconds"], 15)

    def test_reserve_gmail_write_budget_refills_tokens_from_elapsed_time(self):
        base_dt = datetime(2026, 3, 15, 20, 0, 0, tzinfo=timezone.utc)
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_write_budget_tokens, gmail_backfill_budget_tokens, gmail_write_budget_updated_at)
               VALUES (?, ?, ?, ?)""",
            ("owner@gmail.com", 0.0, 0.0, base_dt.isoformat()),
        )

        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_GMAIL_WRITE_BUDGET_UNITS_PER_MINUTE": "120",
                "PENGUIN_CONNECT_GMAIL_BACKFILL_WRITE_BUDGET_UNITS_PER_MINUTE": "60",
                "PENGUIN_CONNECT_GMAIL_WRITE_OPERATION_COST_UNITS": "25",
            },
            clear=False,
        ):
            reservation = penguin_connect._reserve_gmail_write_budget(
                self.conn,
                "owner@gmail.com",
                "incremental",
                now_dt=base_dt + timedelta(seconds=30),
            )

        self.assertTrue(reservation["granted"])
        self.assertEqual(reservation["total_tokens_remaining"], 35.0)
        self.assertEqual(reservation["backfill_tokens_remaining"], 30.0)

    def test_import_message_to_gmail_waits_for_budget_before_import(self):
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.return_value = {
            "id": "gm-1",
            "threadId": "th-1",
        }

        with mock.patch("penguin_connect._wait_for_gmail_write_budget") as mock_wait:
            data, error = penguin_connect._import_message_to_gmail(
                self.conn,
                gmail_service,
                "owner@gmail.com",
                "backfill",
                "raw-message",
                None,
                True,
            )

        self.assertIsNone(error)
        self.assertEqual(data["id"], "gm-1")
        mock_wait.assert_called_once_with(self.conn, "owner@gmail.com", "backfill")

    def test_record_backfill_daily_gmail_imports_increments_active_window(self):
        base_dt = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_backfill_daily_import_count, gmail_backfill_daily_window_started_at)
               VALUES (?, ?, ?)""",
            ("owner@gmail.com", 12, base_dt.isoformat()),
        )

        penguin_connect._record_backfill_daily_gmail_imports(
            self.conn,
            "owner@gmail.com",
            5,
            now_dt=base_dt + timedelta(hours=2),
        )

        row = self.conn.execute(
            """SELECT gmail_backfill_daily_import_count, gmail_backfill_daily_window_started_at
               FROM penguin_connect_poll_state
               WHERE gmail_email = ?""",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertEqual(row["gmail_backfill_daily_import_count"], 17)
        self.assertEqual(row["gmail_backfill_daily_window_started_at"], base_dt.isoformat())

    def test_record_backfill_daily_gmail_imports_resets_expired_window(self):
        base_dt = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_backfill_daily_import_count, gmail_backfill_daily_window_started_at)
               VALUES (?, ?, ?)""",
            ("owner@gmail.com", 12, base_dt.isoformat()),
        )

        penguin_connect._record_backfill_daily_gmail_imports(
            self.conn,
            "owner@gmail.com",
            5,
            now_dt=base_dt + timedelta(days=1, minutes=1),
        )

        row = self.conn.execute(
            """SELECT gmail_backfill_daily_import_count, gmail_backfill_daily_window_started_at
               FROM penguin_connect_poll_state
               WHERE gmail_email = ?""",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertEqual(row["gmail_backfill_daily_import_count"], 5)
        self.assertEqual(row["gmail_backfill_daily_window_started_at"], (base_dt + timedelta(days=1, minutes=1)).isoformat())

    def test_backfill_daily_import_pause_returns_retry_when_cap_hit(self):
        base_dt = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_backfill_daily_import_count, gmail_backfill_daily_window_started_at)
               VALUES (?, ?, ?)""",
            ("owner@gmail.com", 500, base_dt.isoformat()),
        )

        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_BACKFILL_DAILY_GMAIL_IMPORT_CAP": "500"},
            clear=False,
        ):
            pause = penguin_connect._backfill_daily_import_pause(
                self.conn,
                "owner@gmail.com",
                now_dt=base_dt + timedelta(hours=4),
            )

        self.assertIsNotNone(pause)
        self.assertEqual(pause["backfill_daily_import_cap"], 500)
        self.assertEqual(pause["backfill_daily_import_count"], 500)
        self.assertEqual(pause["retry_after_seconds"], 20 * 60 * 60)

    def test_set_gmail_rate_limit_pause_escalates_by_streak(self):
        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_GMAIL_RATE_LIMIT_PAUSE_SECONDS": "30",
                "PENGUIN_CONNECT_GMAIL_RATE_LIMIT_MAX_PAUSE_SECONDS": "90",
            },
            clear=False,
        ):
            first = penguin_connect._set_gmail_rate_limit_pause(self.conn, "owner@gmail.com", None)
            second = penguin_connect._set_gmail_rate_limit_pause(self.conn, "owner@gmail.com", None)
            third = penguin_connect._set_gmail_rate_limit_pause(self.conn, "owner@gmail.com", None)

        self.assertEqual(first["retry_after_seconds"], 30)
        self.assertEqual(first["rate_limit_streak"], 1)
        self.assertEqual(second["retry_after_seconds"], 60)
        self.assertEqual(second["rate_limit_streak"], 2)
        self.assertEqual(third["retry_after_seconds"], 90)
        self.assertEqual(third["rate_limit_streak"], 3)

    def test_record_gmail_sync_success_resets_rate_limit_streak_after_write(self):
        future_pause = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_rate_limited_until, gmail_rate_limit_streak)
               VALUES (?, ?, ?)""",
            ("owner@gmail.com", future_pause, 4),
        )

        penguin_connect._record_gmail_sync_success(
            self.conn,
            "owner@gmail.com",
            wrote_to_gmail=True,
        )

        row = self.conn.execute(
            """SELECT gmail_rate_limited_until, gmail_rate_limit_streak
               FROM penguin_connect_poll_state
               WHERE gmail_email = ?""",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertIsNone(row["gmail_rate_limited_until"])
        self.assertEqual(row["gmail_rate_limit_streak"], 0)

    def test_record_gmail_sync_success_decays_rate_limit_streak_without_write(self):
        future_pause = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_rate_limited_until, gmail_rate_limit_streak)
               VALUES (?, ?, ?)""",
            ("owner@gmail.com", future_pause, 3),
        )

        penguin_connect._record_gmail_sync_success(
            self.conn,
            "owner@gmail.com",
            wrote_to_gmail=False,
        )

        row = self.conn.execute(
            """SELECT gmail_rate_limited_until, gmail_rate_limit_streak
               FROM penguin_connect_poll_state
               WHERE gmail_email = ?""",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertIsNone(row["gmail_rate_limited_until"])
        self.assertEqual(row["gmail_rate_limit_streak"], 2)

    def test_startup_catchup_limit_defaults_to_all_pending_conversations(self):
        self.assertEqual(penguin_connect._startup_catchup_conversations_per_run(), 5)

    def test_startup_catchup_limit_honors_env_override(self):
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN": "2"},
            clear=False,
        ):
            self.assertEqual(penguin_connect._startup_catchup_conversations_per_run(), 2)

    def test_startup_catchup_limit_treats_non_positive_values_as_unbounded(self):
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN": "0"},
            clear=False,
        ):
            self.assertIsNone(penguin_connect._startup_catchup_conversations_per_run())

    def test_incremental_selection_limit_defaults_to_all_hot_up_to_cap(self):
        self.assertEqual(penguin_connect._incremental_selection_limit(0), 1)
        self.assertEqual(penguin_connect._incremental_selection_limit(4), 4)
        self.assertEqual(
            penguin_connect._incremental_selection_limit(99),
            penguin_connect.MAX_INCREMENTAL_CONVERSATIONS_PER_RUN,
        )

    def test_incremental_selection_limit_honors_explicit_env_override(self):
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_INCREMENTAL_CONVERSATIONS_PER_RUN": "2"},
            clear=False,
        ):
            self.assertEqual(penguin_connect._incremental_selection_limit(6), 2)

    def test_sync_job_worker_prioritizes_incremental_before_startup_without_dedupe_key(self):
        startup = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="startup_catchup",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=False,
        )
        incremental = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="incremental",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=False,
        )
        self.conn.commit()

        seen_modes = []

        def fake_sync(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            seen_modes.append(mode)
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        with mock.patch("penguin_connect.sync_conversations", side_effect=fake_sync):
            result = penguin_connect.run_sync_job_worker_once(self.conn, owner="background-worker")

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["queue_job_id"], incremental["job_id"])
        self.assertEqual(seen_modes, ["incremental"])

        startup_row = self.conn.execute(
            "SELECT status FROM penguin_connect_jobs WHERE id = ?",
            (startup["job_id"],),
        ).fetchone()
        self.assertEqual(startup_row["status"], "queued")

    def test_build_gmail_service_uses_configured_http_timeout(self):
        token_json = {
            "token": "tok",
            "refresh_token": "refresh",
            "client_id": "client",
            "client_secret": "secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["scope"],
        }
        creds = mock.Mock(valid=True, expired=False, refresh_token="refresh")
        authed_http = mock.Mock()
        http = mock.Mock()

        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_GMAIL_HTTP_TIMEOUT_SECONDS": "25"}, clear=False), mock.patch(
            "penguin_connect._read_keychain_secret",
            return_value=json.dumps(token_json),
        ), mock.patch(
            "google.oauth2.credentials.Credentials",
            return_value=creds,
        ), mock.patch(
            "google.auth.transport.requests.Request",
        ), mock.patch(
            "httplib2.Http",
            return_value=http,
        ) as mock_http, mock.patch(
            "google_auth_httplib2.AuthorizedHttp",
            return_value=authed_http,
        ) as mock_authed, mock.patch(
            "googleapiclient.discovery.build",
            return_value="gmail-service",
        ) as mock_build:
            service, err = penguin_connect._build_gmail_service("owner@gmail.com", "svc")

        self.assertEqual(service, "gmail-service")
        self.assertIsNone(err)
        mock_http.assert_called_once_with(timeout=25)
        mock_authed.assert_called_once_with(creds, http=http)
        mock_build.assert_called_once_with("gmail", "v1", http=authed_http, cache_discovery=False)

    def test_list_gmail_messages_to_alias_queries_sent_mail_only(self):
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {"messages": []}

        penguin_connect._list_gmail_messages_to_alias(
            gmail_service,
            "owner+am-test@gmail.com",
            "2026-03-04T09:00:00+00:00",
        )

        query = gmail_service.users.return_value.messages.return_value.list.call_args.kwargs["q"]
        self.assertIn("to:owner+am-test@gmail.com", query)
        self.assertIn("in:sent", query)

    def test_sender_gate_blocks_non_connected_sender(self):
        result = penguin_connect.send_manual_message(
            self.conn,
            conversation_id="amc_test",
            sender_email="attacker@example.com",
            body_text="hello",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "sender_not_connected_gmail")
        self.assertEqual(result["status_code"], 403)

    def test_sender_gate_allows_primary_or_verified_send_as(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)):
            primary = penguin_connect.send_manual_message(
                self.conn,
                conversation_id="amc_test",
                sender_email="owner@gmail.com",
                body_text="hello from primary",
            )
            alias = penguin_connect.send_manual_message(
                self.conn,
                conversation_id="amc_test",
                sender_email="ops@company.com",
                body_text="hello from alias",
            )

        self.assertTrue(primary["success"])
        self.assertTrue(alias["success"])


    def test_disconnect_and_reconnect_provisions_fresh_alias(self):
        first_alias = self.conn.execute(
            "SELECT alias_email FROM penguin_connect_conversations WHERE conversation_id='amc_test'"
        ).fetchone()[0]

        disconnected = penguin_connect.disconnect_conversation(self.conn, "amc_test")
        self.assertTrue(disconnected["success"])

        reconnected = penguin_connect.reconnect_conversation(self.conn, "amc_test")
        self.assertTrue(reconnected["success"])
        self.assertNotEqual(first_alias, reconnected["alias_email"])
        self.assertIn("+am-", reconnected["alias_email"])

    def test_reconnect_requires_fresh_bootstrap_state(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )

        disconnected = penguin_connect.disconnect_conversation(self.conn, "amc_test")
        reconnected = penguin_connect.reconnect_conversation(self.conn, "amc_test")
        state = self.conn.execute(
            "SELECT 1 FROM penguin_connect_sync_state WHERE conversation_id = ?",
            ("amc_test",),
        ).fetchone()

        self.assertTrue(disconnected["success"])
        self.assertTrue(reconnected["success"])
        self.assertIsNone(state)

    def test_upsert_sync_state_does_not_regress_timestamps(self):
        cid = "amc_test"
        newer = "2026-03-04T10:00:00+00:00"
        older = "2026-03-03T10:00:00+00:00"
        penguin_connect._upsert_sync_state(self.conn, cid, newer, "101", newer, "123")
        penguin_connect._upsert_sync_state(self.conn, cid, older, "99", older, None)

        state = self.conn.execute(
            "SELECT last_imessage_ts, last_imessage_native_message_id, last_gmail_ts, last_message_ts, last_gmail_history_id FROM penguin_connect_sync_state WHERE conversation_id = ?",
            (cid,),
        ).fetchone()
        self.assertEqual(state["last_imessage_ts"], newer)
        self.assertEqual(state["last_imessage_native_message_id"], "101")
        self.assertEqual(state["last_gmail_ts"], newer)
        self.assertEqual(state["last_message_ts"], newer)
        self.assertEqual(state["last_gmail_history_id"], "123")

    def test_upsert_sync_state_keeps_higher_native_cursor_for_equal_timestamp(self):
        cid = "amc_test"
        same_ts = "2026-03-04T10:00:00+00:00"
        penguin_connect._upsert_sync_state(self.conn, cid, same_ts, "101", None, None)
        penguin_connect._upsert_sync_state(self.conn, cid, same_ts, "102", None, None)

        state = self.conn.execute(
            "SELECT last_imessage_ts, last_imessage_native_message_id FROM penguin_connect_sync_state WHERE conversation_id = ?",
            (cid,),
        ).fetchone()
        self.assertEqual(state["last_imessage_ts"], same_ts)
        self.assertEqual(state["last_imessage_native_message_id"], "102")

    def test_initial_sync_bootstrapped_requires_completed_initial_sync(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
            ),
        )

        self.assertFalse(penguin_connect._initial_sync_bootstrapped(self.conn))

    def test_mark_conversation_bootstrapped_schedules_full_verify_once(self):
        with mock.patch("penguin_connect._now_iso", return_value="2026-03-04T10:05:00+00:00"):
            penguin_connect._mark_conversation_bootstrapped(self.conn, "amc_test")

        first = self.conn.execute(
            """SELECT initial_sync_completed_at, next_full_verify_at, full_verify_completed_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        with mock.patch("penguin_connect._now_iso", return_value="2026-03-05T10:05:00+00:00"):
            penguin_connect._mark_conversation_bootstrapped(self.conn, "amc_test")

        second = self.conn.execute(
            """SELECT initial_sync_completed_at, next_full_verify_at, full_verify_completed_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        self.assertEqual(first["initial_sync_completed_at"], "2026-03-04T10:05:00+00:00")
        self.assertTrue(first["next_full_verify_at"])
        self.assertIsNone(first["full_verify_completed_at"])
        self.assertEqual(second["initial_sync_completed_at"], first["initial_sync_completed_at"])
        self.assertEqual(second["next_full_verify_at"], first["next_full_verify_at"])

    def test_mark_conversation_bootstrapped_records_empty_verification_once(self):
        with mock.patch("penguin_connect._now_iso", return_value="2026-03-04T10:05:00+00:00"):
            penguin_connect._mark_conversation_bootstrapped(self.conn, "amc_test", empty_verified=True)

        first = self.conn.execute(
            """SELECT initial_sync_completed_at, initial_sync_empty_verified_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        with mock.patch("penguin_connect._now_iso", return_value="2026-03-05T10:05:00+00:00"):
            penguin_connect._mark_conversation_bootstrapped(self.conn, "amc_test")

        second = self.conn.execute(
            """SELECT initial_sync_completed_at, initial_sync_empty_verified_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        self.assertEqual(first["initial_sync_completed_at"], "2026-03-04T10:05:00+00:00")
        self.assertEqual(first["initial_sync_empty_verified_at"], "2026-03-04T10:05:00+00:00")
        self.assertEqual(second["initial_sync_completed_at"], first["initial_sync_completed_at"])
        self.assertEqual(second["initial_sync_empty_verified_at"], first["initial_sync_empty_verified_at"])

    def test_mark_conversation_full_verify_completed_schedules_next_due_time(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, initial_sync_completed_at, next_full_verify_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:05:00+00:00",
                "2026-03-08T10:05:00+00:00",
            ),
        )

        with mock.patch("penguin_connect._now_iso", return_value="2026-03-08T11:05:00+00:00"):
            penguin_connect._mark_conversation_full_verify_completed(self.conn, "amc_test")

        state = self.conn.execute(
            """SELECT next_full_verify_at, full_verify_completed_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        self.assertTrue(state["next_full_verify_at"])
        self.assertEqual(state["full_verify_completed_at"], "2026-03-08T11:05:00+00:00")

    def test_reconcile_conversation_gmail_read_state_marks_latest_inbound_messages_unread(self):
        self.conn.executemany(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                body_text, message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'imessage', ?, 'imessage_to_email', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    "amc_test",
                    "imessage:old-inbound",
                    "owner+am-test@gmail.com",
                    "Alice",
                    "Family Group",
                    "old inbound",
                    "2026-03-04T08:00:00+00:00",
                    0,
                    json.dumps({"is_from_me": False}),
                    "gm-old-inbound",
                    "thread-1",
                ),
                (
                    "amc_test",
                    "imessage:new-inbound",
                    "owner+am-test@gmail.com",
                    "Alice",
                    "Family Group",
                    "new inbound",
                    "2026-03-04T09:00:00+00:00",
                    1,
                    json.dumps({"is_from_me": False}),
                    "gm-new-inbound",
                    "thread-1",
                ),
                (
                    "amc_test",
                    "imessage:outbound",
                    "owner+am-test@gmail.com",
                    "Me",
                    "Family Group",
                    "sent by me",
                    "2026-03-04T10:00:00+00:00",
                    1,
                    json.dumps({"is_from_me": True}),
                    "gm-outbound",
                    "thread-1",
                ),
            ],
        )
        gmail_service = mock.Mock()

        penguin_connect._reconcile_conversation_gmail_read_state(
            self.conn,
            gmail_service,
            "amc_test",
            unread_count=1,
        )

        modify_calls = gmail_service.users.return_value.messages.return_value.modify.call_args_list
        self.assertEqual(len(modify_calls), 2)
        self.assertEqual(modify_calls[0].kwargs["id"], "gm-new-inbound")
        self.assertEqual(modify_calls[0].kwargs["body"], {"addLabelIds": ["UNREAD"]})
        self.assertEqual(modify_calls[1].kwargs["id"], "gm-old-inbound")
        self.assertEqual(modify_calls[1].kwargs["body"], {"removeLabelIds": ["UNREAD"]})

        rows = self.conn.execute(
            """SELECT provider_message_id, is_read
               FROM penguin_connect_messages
               WHERE conversation_id = ?
               ORDER BY message_timestamp ASC""",
            ("amc_test",),
        ).fetchall()
        self.assertEqual(
            [(row["provider_message_id"], row["is_read"]) for row in rows],
            [
                ("imessage:old-inbound", 1),
                ("imessage:new-inbound", 0),
                ("imessage:outbound", 1),
            ],
        )

    def test_imessage_sync_reconciles_gmail_read_state_without_new_messages(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                body_text, message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'imessage', ?, 'imessage_to_email', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "amc_test",
                "imessage:stale-unread",
                "owner+am-test@gmail.com",
                "Alice",
                "Family Group",
                "already synced",
                "2026-03-04T08:00:00+00:00",
                0,
                json.dumps({"is_from_me": False}),
                "gm-stale-unread",
                "thread-1",
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=[]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=0
        ):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="incremental",
                days=7,
            )

        self.assertEqual(result["imessage_imported"], 0)
        modify = gmail_service.users.return_value.messages.return_value.modify
        modify.assert_called_once_with(
            userId="me",
            id="gm-stale-unread",
            body={"removeLabelIds": ["UNREAD"]},
        )
        row = self.conn.execute(
            """SELECT is_read FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "imessage:stale-unread"),
        ).fetchone()
        self.assertEqual(row["is_read"], 1)

    def test_ensure_full_verify_schedule_backfills_missing_due_time_for_bootstrapped_conversation(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-08T10:05:00+00:00",
            ),
        )

        updated = penguin_connect._ensure_full_verify_schedule(self.conn, "owner@gmail.com")
        state = self.conn.execute(
            """SELECT next_full_verify_at, full_verify_completed_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        self.assertEqual(updated, 1)
        self.assertTrue(state["next_full_verify_at"])
        self.assertIsNone(state["full_verify_completed_at"])

    def test_resolve_display_name_uses_contact_when_chat_name_is_raw_handle(self):
        self.conn.execute(
            """INSERT INTO contacts (first_name, last_name, organization, phone, phone_normalized, email, source_db)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Kam", "(Shine Capital)", "", "+17144741613", "+17144741613", None, "test"),
        )

        name = penguin_connect._resolve_display_name(self.conn, "+17144741613", ["+17144741613"])

        self.assertEqual(name, "Kam (Shine Capital)")

    def test_resolve_display_name_prefers_resolved_group_participants_over_raw_handle_title(self):
        self.conn.executemany(
            """INSERT INTO contacts (first_name, last_name, organization, phone, phone_normalized, email, source_db)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                ("Sai", "Mandhan", "", "+12405976093", "+12405976093", None, "test"),
                ("Nikhil", "(Remedy)", "", "+15126629638", "+15126629638", None, "test"),
            ],
        )

        name = penguin_connect._resolve_display_name(
            self.conn,
            "Sai Mandhan, +15126629638",
            ["+12405976093", "+15126629638"],
            chat_type="group",
        )

        self.assertEqual(name, "Sai Mandhan, Nikhil (Remedy)")

    def test_refresh_contact_display_names_updates_raw_group_title_after_contact_import(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants, alias_email, status)
               VALUES (?, ?, ?, ?, 'group', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_group",
                "chat-group",
                "Sai Mandhan, +15126629638",
                json.dumps(["+12405976093", "+15126629638"]),
                "owner+am-group@gmail.com",
            ),
        )
        self.conn.executemany(
            """INSERT INTO contacts (first_name, last_name, organization, phone, phone_normalized, email, source_db)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                ("Sai", "Mandhan", "", "+12405976093", "+12405976093", None, "test"),
                ("Nikhil", "(Remedy)", "", "+15126629638", "+15126629638", None, "test"),
            ],
        )

        updated = penguin_connect._refresh_contact_display_names(self.conn)
        row = self.conn.execute(
            "SELECT display_name FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_group",),
        ).fetchone()

        self.assertEqual(updated, 1)
        self.assertEqual(row["display_name"], "Sai Mandhan, Nikhil (Remedy)")

    def test_refresh_contacts_waits_for_active_sync_lane(self):
        entered_refresh = threading.Event()
        result_holder = {}
        errors = []
        fake_conn = mock.Mock()
        fake_conn.execute.return_value.fetchone.return_value = (0,)

        def fake_subprocess_run(*_args, **_kwargs):
            entered_refresh.set()
            return mock.Mock(returncode=0, stdout="Imported 0 contacts", stderr="")

        def run_refresh():
            try:
                result_holder["result"] = penguin_connect.refresh_contacts_and_repair_display_names()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with mock.patch("penguin_connect.subprocess.run", side_effect=fake_subprocess_run), mock.patch(
            "db.get_connection",
            return_value=fake_conn,
        ), mock.patch(
            "penguin_connect._refresh_contact_display_names",
            return_value=0,
        ), mock.patch("penguin_connect.log_action"):
            with penguin_connect._incremental_sync_lock:
                thread = threading.Thread(target=run_refresh)
                thread.start()
                time.sleep(0.05)
                self.assertFalse(entered_refresh.is_set())
            self.assertTrue(entered_refresh.wait(1.0))
            thread.join(1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(result_holder["result"]["success"])

    def test_incremental_selection_prefers_recent_activity_across_all_conversations(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_pending",
                "chat-pending",
                "Pending Bootstrap",
                "owner+am-pending@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )

        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-pending",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_pending"])
        self.assertEqual(selection["discovered_conversations"], 2)
        self.assertEqual(selection["selected_conversations"], 1)
        self.assertEqual(selection["pending_bootstrap_conversations"], 1)
        self.assertEqual(selection["hot_conversations"], 1)
        self.assertEqual(selection["hot_imessage_conversations"], 1)
        self.assertEqual(selection["selection_strategy"], "activity_prioritized_round_robin")

    def test_incremental_selection_defaults_to_selecting_all_hot_conversations(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_hot_two",
                "chat-hot-two",
                "Hot Two",
                "owner+am-hot-two@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_hot_two",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )

        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 1,
                    },
                    {
                        "chat_id": "chat-hot-two",
                        "first_message_at": "2026-03-07T07:11:00+00:00",
                        "last_message_at": "2026-03-07T07:16:00+00:00",
                        "message_count": 1,
                    },
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
            )

        self.assertEqual({row["conversation_id"] for row in conversations}, {"amc_test", "amc_hot_two"})
        self.assertEqual(selection["selected_conversations"], 2)
        self.assertEqual(selection["selection_limit"], 2)
        self.assertEqual(selection["hot_conversations"], 2)
        self.assertEqual(selection["hot_imessage_conversations"], 2)
        self.assertEqual(selection["selection_strategy"], "activity_prioritized_round_robin")

    def test_incremental_selection_matches_routed_group_activity_by_service_route(self):
        self.conn.execute(
            """UPDATE penguin_connect_conversations
               SET imessage_chat_id = ?, imessage_chat_identifier = ?, imessage_service_name = ?, source_provider = ?
               WHERE conversation_id = ?""",
            ("RCS;+;chat-123", "chat-123", "RCS", "rcs", "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )

        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "chat_identifier": "chat-123",
                        "service": "RCS",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 1,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test"])
        self.assertEqual(selection["hot_conversations"], 1)
        self.assertEqual(selection["hot_imessage_conversations"], 1)

    def test_incremental_selection_keeps_group_routes_service_specific(self):
        self.conn.execute(
            """UPDATE penguin_connect_conversations
               SET imessage_chat_id = ?, imessage_chat_identifier = ?, imessage_service_name = ?, source_provider = ?
               WHERE conversation_id = ?""",
            ("RCS;+;chat-123", "chat-123", "RCS", "rcs", "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, imessage_chat_identifier,
                imessage_service_name, display_name, chat_type, participants, alias_email, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "sms",
                "amc_sms_group",
                "SMS;+;chat-123",
                "chat-123",
                "SMS",
                "Sibling SMS Group",
                "owner+am-sms-group@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_sms_group",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )

        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "chat_identifier": "chat-123",
                        "service": "RCS",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 1,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test"])
        self.assertEqual(selection["hot_conversations"], 1)

    def test_incremental_selection_includes_due_full_verify_without_starving_hot_work(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_hot",
                "chat-hot",
                "Hot Conversation",
                "owner+am-hot@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at,
                next_full_verify_at, full_verify_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-07T10:05:00+00:00",
                "2026-03-05T10:05:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_hot",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 3, 8, 10, 5, tzinfo=tz or timezone.utc)

        with mock.patch("penguin_connect.datetime", FixedDateTime), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-hot",
                        "first_message_at": "2026-03-08T09:55:00+00:00",
                        "last_message_at": "2026-03-08T10:00:00+00:00",
                        "message_count": 1,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_hot", "amc_test"])
        self.assertEqual(selection["selected_conversations"], 2)
        self.assertEqual(selection["pending_full_verify_conversations"], 1)
        self.assertEqual(selection["scheduled_full_verify_selected"], 1)
        self.assertEqual(selection["verify_all_conversation_ids"], ["amc_test"])
        self.assertEqual(selection["selection_strategy"], "activity_prioritized_with_scheduled_verify")

    def test_startup_catchup_selects_only_pending_bootstrap_conversations(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_bootstrapped",
                "chat-bootstrapped",
                "Bootstrapped",
                "owner+am-bootstrapped@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_bootstrapped",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_pending_cold",
                "chat-pending-cold",
                "Pending Cold",
                "owner+am-pending-cold@gmail.com",
            ),
        )

        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "startup_catchup",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test", "amc_pending_cold"])
        self.assertEqual(selection["discovered_conversations"], 3)
        self.assertEqual(selection["selected_conversations"], 2)
        self.assertEqual(selection["bootstrapped_conversations"], 1)
        self.assertEqual(selection["pending_bootstrap_conversations"], 2)
        self.assertEqual(selection["selection_limit"], 2)
        self.assertEqual(selection["selection_strategy"], "pending_bootstrap_recent_imessage_activity")
        self.assertTrue(selection["selection_cutoff"])

    def test_startup_catchup_selection_honors_env_limit(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_pending_second",
                "chat-pending-second",
                "Pending Second",
                "owner+am-pending-second@gmail.com",
            ),
        )

        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN": "1"},
            clear=False,
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "startup_catchup",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test"])
        self.assertEqual(selection["queued_conversations"], 2)
        self.assertEqual(selection["selected_conversations"], 1)
        self.assertEqual(selection["selection_limit"], 1)

    def test_startup_catchup_prioritizes_pending_routed_group_activity(self):
        self.conn.execute(
            """UPDATE penguin_connect_conversations
               SET imessage_chat_id = ?, imessage_chat_identifier = ?, imessage_service_name = ?, source_provider = ?
               WHERE conversation_id = ?""",
            ("RCS;+;chat-123", "chat-123", "RCS", "rcs", "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_pending_cold",
                "chat-pending-cold",
                "Pending Cold",
                "owner+am-pending-cold@gmail.com",
            ),
        )

        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "chat_identifier": "chat-123",
                        "service": "RCS",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "startup_catchup",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test", "amc_pending_cold"])
        self.assertEqual(selection["selection_strategy"], "pending_bootstrap_recent_imessage_activity")

    def test_startup_catchup_selects_due_full_verify_after_bootstrap(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at,
                next_full_verify_at, full_verify_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-07T10:05:00+00:00",
                "2026-03-05T10:05:00+00:00",
            ),
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 3, 8, 10, 5, tzinfo=tz or timezone.utc)

        with mock.patch("penguin_connect.datetime", FixedDateTime):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "startup_catchup",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test"])
        self.assertEqual(selection["pending_bootstrap_conversations"], 0)
        self.assertEqual(selection["pending_full_verify_conversations"], 1)
        self.assertEqual(selection["selection_strategy"], "scheduled_full_verify_due")
        self.assertEqual(selection["verify_all_conversation_ids"], ["amc_test"])

    def test_incremental_selection_uses_recent_gmail_activity(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_gmail_hot",
                "chat-gmail-hot",
                "Gmail Hot",
                "owner+am-gmail-hot@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_gmail_hot",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )

        gmail_service = mock.Mock()
        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={"available": True, "chats": []},
        ), mock.patch(
            "penguin_connect._list_recent_gmail_alias_activity",
            return_value=(
                {"amc_gmail_hot": {"last_message_at": "2026-03-07T07:15:00+00:00", "message_count": 1}},
                {},
            ),
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
                gmail_service=gmail_service,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_gmail_hot"])
        self.assertEqual(selection["hot_gmail_conversations"], 1)
        self.assertEqual(selection["selection_strategy"], "activity_prioritized_round_robin")

    def test_backfill_selection_matches_routed_group_activity_by_service_route(self):
        self.conn.execute(
            """UPDATE penguin_connect_conversations
               SET imessage_chat_id = ?, imessage_chat_identifier = ?, imessage_service_name = ?, source_provider = ?
               WHERE conversation_id = ?""",
            ("RCS;+;chat-123", "chat-123", "RCS", "rcs", "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_cold",
                "chat-cold",
                "Cold Group",
                "owner+am-cold@gmail.com",
            ),
        )

        with mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "chat_identifier": "chat-123",
                        "service": "RCS",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "backfill",
                days=7,
                hours=None,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test"])
        self.assertEqual(selection["selection_strategy"], "recent_imessage_activity")

    def test_incremental_selection_prioritizes_gmail_hot_over_imessage_hot(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_gmail_hot",
                "chat-gmail-hot",
                "Gmail Hot",
                "owner+am-gmail-hot@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "amc_gmail_hot",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-04T10:06:00+00:00",
                "2026-03-04T10:06:00+00:00",
            ),
        )
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET last_synced_at = ? WHERE conversation_id = ?",
            ("2026-03-04T10:05:00+00:00", "amc_test"),
        )

        gmail_service = mock.Mock()
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_INCREMENTAL_CONVERSATIONS_PER_RUN": "1"},
            clear=False,
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 1,
                    }
                ],
            },
        ), mock.patch(
            "penguin_connect._list_recent_gmail_alias_activity",
            return_value=(
                {"amc_gmail_hot": {"last_message_at": "2026-03-07T07:16:00+00:00", "message_count": 1}},
                {},
            ),
        ):
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
                gmail_service=gmail_service,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_gmail_hot"])
        self.assertEqual(selection["hot_gmail_conversations"], 1)
        self.assertEqual(selection["hot_imessage_conversations"], 1)

    def test_incremental_selection_persists_unselected_gmail_hot_activity_until_synced(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_gmail_hot_b",
                "chat-gmail-hot-b",
                "Gmail Hot B",
                "owner+am-gmail-hot-b@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "amc_gmail_hot_b",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-04T10:06:00+00:00",
                "2026-03-04T10:06:00+00:00",
            ),
        )

        gmail_service = mock.Mock()
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_INCREMENTAL_CONVERSATIONS_PER_RUN": "1"},
            clear=False,
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={"available": True, "chats": []},
        ), mock.patch(
            "penguin_connect._list_recent_gmail_alias_activity",
            return_value=(
                {
                    "amc_test": {"last_message_at": "2026-03-07T07:15:00+00:00", "message_count": 1},
                    "amc_gmail_hot_b": {"last_message_at": "2026-03-07T07:16:00+00:00", "message_count": 1},
                },
                {},
            ),
        ):
            first_conversations, first_selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
                gmail_service=gmail_service,
            )

        self.assertEqual(first_selection["hot_gmail_conversations"], 2)
        first_selected_id = first_conversations[0]["conversation_id"]
        second_expected_id = "amc_gmail_hot_b" if first_selected_id == "amc_test" else "amc_test"
        penguin_connect._record_pending_gmail_activity(
            self.conn,
            {
                "amc_test": {"last_message_at": "2026-03-07T07:15:00+00:00", "message_count": 1},
                "amc_gmail_hot_b": {"last_message_at": "2026-03-07T07:16:00+00:00", "message_count": 1},
            },
        )
        pending_row = self.conn.execute(
            "SELECT pending_gmail_activity_at FROM penguin_connect_sync_state WHERE conversation_id = ?",
            (second_expected_id,),
        ).fetchone()
        self.assertEqual(
            pending_row["pending_gmail_activity_at"],
            "2026-03-07T07:16:00+00:00" if second_expected_id == "amc_gmail_hot_b" else "2026-03-07T07:15:00+00:00",
        )

        penguin_connect._upsert_sync_state(
            self.conn,
            first_selected_id,
            None,
            None,
            "2026-03-07T07:15:00+00:00" if first_selected_id == "amc_test" else "2026-03-07T07:16:00+00:00",
            None,
        )
        penguin_connect._clear_pending_gmail_activity_if_caught_up(self.conn, first_selected_id)

        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_INCREMENTAL_CONVERSATIONS_PER_RUN": "1"},
            clear=False,
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={"available": True, "chats": []},
        ), mock.patch(
            "penguin_connect._list_recent_gmail_alias_activity",
            return_value=({}, {}),
        ):
            second_conversations, second_selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "incremental",
                days=7,
                hours=None,
                gmail_service=gmail_service,
            )

        self.assertEqual([row["conversation_id"] for row in second_conversations], [second_expected_id])
        self.assertEqual(second_selection["hot_gmail_conversations"], 1)

    def test_list_recent_gmail_alias_activity_backstop_recovers_recent_sent_alias_messages(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, last_gmail_history_id)
               VALUES (?, ?)""",
            ("owner@gmail.com", "200"),
        )
        conv = self.conn.execute(
            """SELECT c.*,
                      s.last_imessage_ts,
                      s.last_gmail_ts,
                      s.last_message_ts,
                      s.last_gmail_history_id,
                      s.pending_gmail_activity_at,
                      s.initial_sync_completed_at,
                      s.next_full_verify_at,
                      s.full_verify_completed_at,
                      s.last_synced_at AS sync_state_last_synced_at
               FROM penguin_connect_conversations c
               LEFT JOIN penguin_connect_sync_state s ON s.conversation_id = c.conversation_id
               WHERE c.conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        gmail_service = mock.Mock()
        gmail_service.users.return_value.history.return_value.list.return_value.execute.return_value = {
            "historyId": "201",
            "history": [],
        }
        gmail_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "sent-1"}],
        }
        recent_internal_date = str(int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000))

        def get_message(*, userId, id, format, metadataHeaders):
            response = mock.Mock()
            self.assertEqual(id, "sent-1")
            self.assertEqual(format, "metadata")
            response.execute.return_value = {
                "labelIds": ["SENT"],
                "payload": {
                    "headers": [
                        {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    ]
                },
                "internalDate": recent_internal_date,
            }
            return response

        gmail_service.users.return_value.messages.return_value.get.side_effect = get_message

        recent, meta = penguin_connect._list_recent_gmail_alias_activity(
            self.conn,
            gmail_service,
            "owner@gmail.com",
            [conv],
        )

        self.assertEqual(recent["amc_test"]["message_count"], 1)
        self.assertTrue(meta["gmail_activity_backstop_used"])
        self.assertEqual(meta["gmail_activity_backstop_matches"], 1)
        pending = self.conn.execute(
            "SELECT pending_gmail_activity_at FROM penguin_connect_sync_state WHERE conversation_id = ?",
            ("amc_test",),
        ).fetchone()
        self.assertEqual(
            pending["pending_gmail_activity_at"],
            penguin_connect._iso_from_gmail_internal_date(recent_internal_date),
        )

    def test_record_pending_gmail_activity_logs_new_and_advanced_values(self):
        with mock.patch("penguin_connect.log_action") as mock_log:
            first = penguin_connect._record_pending_gmail_activity(
                self.conn,
                {"amc_test": {"last_message_at": "2026-03-07T07:15:00+00:00", "message_count": 1}},
            )
            second = penguin_connect._record_pending_gmail_activity(
                self.conn,
                {"amc_test": {"last_message_at": "2026-03-07T07:16:00+00:00", "message_count": 2}},
            )

        self.assertEqual(first, {"recorded": 1, "advanced": 0})
        self.assertEqual(second, {"recorded": 1, "advanced": 1})
        self.assertEqual(mock_log.call_count, 2)
        self.assertEqual(mock_log.call_args_list[0].args[0], "gmail_pending_activity_recorded")
        self.assertEqual(mock_log.call_args_list[0].kwargs["pending_gmail_activity_at"], "2026-03-07T07:15:00+00:00")
        self.assertEqual(mock_log.call_args_list[1].kwargs["previous_pending_gmail_activity_at"], "2026-03-07T07:15:00+00:00")
        self.assertEqual(mock_log.call_args_list[1].kwargs["pending_gmail_activity_at"], "2026-03-07T07:16:00+00:00")

    def test_clear_pending_gmail_activity_logs_when_caught_up(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_gmail_ts, pending_gmail_activity_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-07T07:16:00+00:00",
                "2026-03-07T07:15:00+00:00",
            ),
        )

        with mock.patch("penguin_connect.log_action") as mock_log:
            cleared = penguin_connect._clear_pending_gmail_activity_if_caught_up(self.conn, "amc_test")

        self.assertTrue(cleared)
        self.assertEqual(mock_log.call_count, 1)
        self.assertEqual(mock_log.call_args.args[0], "gmail_pending_activity_cleared")
        self.assertEqual(mock_log.call_args.kwargs["conversation_id"], "amc_test")
        self.assertEqual(mock_log.call_args.kwargs["previous_pending_gmail_activity_at"], "2026-03-07T07:15:00+00:00")

    def test_list_conversations_uses_cache_without_discovery(self):
        with mock.patch("penguin_connect.ensure_conversations_discovered") as mock_discover:
            result = penguin_connect.list_conversations(self.conn)

        mock_discover.assert_not_called()
        self.assertTrue(result["connected"])
        self.assertEqual(len(result["conversations"]), 1)
        self.assertEqual(result["conversations"][0]["source_provider"], "imessage")
        self.assertEqual(result["conversations"][0]["source_chat_id"], "chat-123")

    def test_list_conversations_discovers_when_cache_empty(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=0) as mock_discover:
            result = penguin_connect.list_conversations(self.conn)

        mock_discover.assert_called_once_with(self.conn, "owner@gmail.com")
        self.assertTrue(result["connected"])
        self.assertEqual(result["conversations"], [])

    def test_list_conversations_marks_entries_excluded_from_file(self):
        exclusions_path = Path(os.environ["PENGUIN_CONNECT_EXCLUDED_CHATS_FILE"])
        exclusions_path.write_text(
            json.dumps({"excluded_chats": [{"conversation_id": "amc_test", "display_name": "Family Group"}]}),
            encoding="utf-8",
        )

        result = penguin_connect.list_conversations(self.conn)

        self.assertTrue(result["conversations"][0]["excluded"])
        stored = self.conn.execute(
            "SELECT exclude_from_sync FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_test",),
        ).fetchone()
        self.assertEqual(stored["exclude_from_sync"], 1)

    def test_selection_skips_excluded_conversations(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET exclude_from_sync = 1 WHERE conversation_id = ?",
            ("amc_test",),
        )

        conversations, selection = penguin_connect._select_conversations_for_sync(
            self.conn,
            "owner@gmail.com",
            "backfill",
            days=7,
            hours=None,
            verify_all=True,
        )

        self.assertEqual(conversations, [])
        self.assertEqual(selection["discovered_conversations"], 0)
        self.assertEqual(selection["selected_conversations"], 0)

    def test_send_manual_message_blocks_excluded_conversation(self):
        exclusions_path = Path(os.environ["PENGUIN_CONNECT_EXCLUDED_CHATS_FILE"])
        exclusions_path.write_text(
            json.dumps({"excluded_chats": [{"conversation_id": "amc_test", "display_name": "Family Group"}]}),
            encoding="utf-8",
        )

        result = penguin_connect.send_manual_message(
            self.conn,
            conversation_id="amc_test",
            sender_email="owner@gmail.com",
            body_text="hello",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "conversation_excluded")

    def test_discovery_marks_excluded_chats_from_config(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        exclusions_path = Path(os.environ["PENGUIN_CONNECT_EXCLUDED_CHATS_FILE"])
        exclusions_path.write_text(
            json.dumps({"excluded_chats": [{"thread_key": "chat:chat-skip", "display_name": "Skip Me"}]}),
            encoding="utf-8",
        )
        chats = [
            {
                "chat_id": "chat-skip",
                "chat_guid": "chat-skip",
                "chat_identifier": "chat-skip",
                "name": "Skip Me",
                "chat_type": "group",
                "participants": ["+15125550123", "+15125550124"],
                "message_count": 2,
                "last_message_at": "2026-03-08T10:00:00+00:00",
                "last_message_preview": "latest",
                "service": "iMessage",
                "source_provider": "imessage",
            }
        ]

        with mock.patch("penguin_connect.browse_imessage_chats", return_value={"available": True, "chats": chats}):
            discovered = penguin_connect.ensure_conversations_discovered(self.conn, "owner@gmail.com")

        self.assertEqual(discovered, 1)
        row = self.conn.execute(
            """SELECT exclude_from_sync, alias_email
               FROM penguin_connect_conversations
               WHERE gmail_email = ?""",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertEqual(row["exclude_from_sync"], 1)
        self.assertIsNone(row["alias_email"])

    def test_discovery_unifies_apple_messages_dm_routes_by_participant(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        chats = [
            {
                "chat_id": "SMS;-;+15127436385",
                "chat_guid": "SMS;-;+15127436385",
                "chat_identifier": "+15127436385",
                "name": "+15127436385",
                "chat_type": "dm",
                "participants": ["+15127436385"],
                "message_count": 3,
                "last_message_at": "2026-03-08T10:00:00+00:00",
                "last_message_preview": "older sms",
                "service": "SMS",
                "source_provider": "sms",
            },
            {
                "chat_id": "RCS;-;+15127436385",
                "chat_guid": "RCS;-;+15127436385",
                "chat_identifier": "+15127436385",
                "name": "+15127436385",
                "chat_type": "dm",
                "participants": ["+15127436385"],
                "message_count": 4,
                "last_message_at": "2026-03-09T10:00:00+00:00",
                "last_message_preview": "newer rcs",
                "service": "RCS",
                "source_provider": "rcs",
            },
        ]

        with mock.patch("penguin_connect.browse_imessage_chats", return_value={"available": True, "chats": chats}):
            discovered = penguin_connect.ensure_conversations_discovered(self.conn, "owner@gmail.com")

        rows = self.conn.execute(
            """SELECT conversation_id, source_provider, imessage_chat_id, imessage_chat_identifier,
                      imessage_service_name, status
               FROM penguin_connect_conversations"""
        ).fetchall()
        expected_conversation_id = penguin_connect.deterministic_conversation_id(
            "owner@gmail.com",
            "dm:5127436385",
            "apple_messages",
        )

        self.assertEqual(discovered, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conversation_id"], expected_conversation_id)
        self.assertEqual(rows[0]["source_provider"], "apple_messages")
        self.assertEqual(rows[0]["imessage_chat_id"], "RCS;-;+15127436385")
        self.assertEqual(rows[0]["imessage_chat_identifier"], "+15127436385")
        self.assertEqual(rows[0]["imessage_service_name"], "RCS")
        self.assertEqual(rows[0]["status"], "active")

    def test_discovery_keeps_group_chats_separate(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        chats = [
            {
                "chat_id": "iMessage;-;family-group",
                "chat_guid": "iMessage;-;family-group",
                "chat_identifier": "family-group",
                "name": "Family Group",
                "chat_type": "group",
                "participants": ["+15127436385", "+14155550101"],
                "message_count": 3,
                "last_message_at": "2026-03-08T10:00:00+00:00",
                "last_message_preview": "hi",
                "service": "iMessage",
                "source_provider": "imessage",
            },
            {
                "chat_id": "SMS;-;family-group",
                "chat_guid": "SMS;-;family-group",
                "chat_identifier": "family-group",
                "name": "Family Group",
                "chat_type": "group",
                "participants": ["+15127436385", "+14155550101"],
                "message_count": 2,
                "last_message_at": "2026-03-09T10:00:00+00:00",
                "last_message_preview": "hi",
                "service": "SMS",
                "source_provider": "sms",
            },
        ]

        with mock.patch("penguin_connect.browse_imessage_chats", return_value={"available": True, "chats": chats}):
            discovered = penguin_connect.ensure_conversations_discovered(self.conn, "owner@gmail.com")

        rows = self.conn.execute(
            """SELECT source_provider, display_name, imessage_chat_id
               FROM penguin_connect_conversations
               ORDER BY source_provider, imessage_chat_id"""
        ).fetchall()

        self.assertEqual(discovered, 2)
        self.assertEqual([row["source_provider"] for row in rows], ["imessage", "sms"])
        self.assertEqual([row["display_name"] for row in rows], ["Family Group", "Family Group"])

    def test_discovery_uses_explicit_group_room_name_when_meaningful(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        chats = [
            {
                "chat_id": "iMessage;+;project-phoenix",
                "chat_guid": "iMessage;+;project-phoenix",
                "chat_identifier": "chat-project-phoenix",
                "name": "+15127436385, +14155550101",
                "source_display_name": "",
                "room_name": "Project Phoenix",
                "chat_type": "group",
                "participants": ["+15127436385", "+14155550101"],
                "message_count": 3,
                "last_message_at": "2026-03-08T10:00:00+00:00",
                "last_message_preview": "hi",
                "service": "iMessage",
                "source_provider": "imessage",
            },
        ]

        with mock.patch("penguin_connect.browse_imessage_chats", return_value={"available": True, "chats": chats}):
            penguin_connect.ensure_conversations_discovered(self.conn, "owner@gmail.com")

        row = self.conn.execute(
            "SELECT display_name FROM penguin_connect_conversations WHERE gmail_email = ?",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertEqual(row["display_name"], "Project Phoenix")

    def test_discovery_preserves_existing_group_name_when_source_title_missing(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        conversation_id = penguin_connect.deterministic_conversation_id(
            "owner@gmail.com",
            "iMessage;+;chat944619608734041334",
            "imessage",
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, imessage_chat_identifier,
                imessage_service_name, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, ?, ?, 'group', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                conversation_id,
                "iMessage;+;chat944619608734041334",
                "chat944619608734041334",
                "iMessage",
                "Dev <> Slashy",
                json.dumps(["+19544595315", "+15127436385"]),
                "owner+am-devslashy@gmail.com",
            ),
        )
        chats = [
            {
                "chat_id": "iMessage;+;chat944619608734041334",
                "chat_guid": "iMessage;+;chat944619608734041334",
                "chat_identifier": "chat944619608734041334",
                "name": "+19544595315, +15127436385",
                "source_display_name": "",
                "room_name": "",
                "chat_type": "group",
                "participants": ["+19544595315", "+15127436385"],
                "message_count": 6,
                "last_message_at": "2026-03-10T10:00:00+00:00",
                "last_message_preview": "hi",
                "service": "iMessage",
                "source_provider": "imessage",
            },
        ]

        with mock.patch("penguin_connect.browse_imessage_chats", return_value={"available": True, "chats": chats}):
            penguin_connect.ensure_conversations_discovered(self.conn, "owner@gmail.com")

        row = self.conn.execute(
            "SELECT display_name FROM penguin_connect_conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        self.assertEqual(row["display_name"], "Dev <> Slashy")

    def test_discovery_self_heals_existing_row_that_matches_unique_route_key(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, imessage_chat_identifier,
                imessage_service_name, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'apple_messages', ?, ?, ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_old_route_id",
                "RCS;-;+15127436385",
                "+15127436385",
                "RCS",
                "Taylor",
                '["+15127436385"]',
                "owner+am-old-route@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_aliases
               (conversation_id, alias_email, alias_local_part, status)
               VALUES (?, ?, ?, 'active')""",
            ("amc_old_route_id", "owner+am-old-route@gmail.com", "owner+am-old-route"),
        )
        chats = [
            {
                "chat_id": "RCS;-;+15127436385",
                "chat_guid": "RCS;-;+15127436385",
                "chat_identifier": "+15127436385",
                "name": "+15127436385",
                "chat_type": "dm",
                "participants": ["+15127436385"],
                "message_count": 4,
                "last_message_at": "2026-03-09T10:00:00+00:00",
                "last_message_preview": "newer rcs",
                "service": "RCS",
                "source_provider": "rcs",
            },
        ]

        with mock.patch("penguin_connect.browse_imessage_chats", return_value={"available": True, "chats": chats}):
            discovered = penguin_connect.ensure_conversations_discovered(self.conn, "owner@gmail.com")

        expected_conversation_id = penguin_connect.deterministic_conversation_id(
            "owner@gmail.com",
            "dm:5127436385",
            "apple_messages",
        )
        row = self.conn.execute(
            """SELECT conversation_id, source_provider, imessage_chat_id, alias_email
               FROM penguin_connect_conversations
               WHERE gmail_email = ?""",
            ("owner@gmail.com",),
        ).fetchone()
        alias_row = self.conn.execute(
            "SELECT conversation_id, alias_email FROM penguin_connect_aliases WHERE alias_email = ?",
            ("owner+am-old-route@gmail.com",),
        ).fetchone()

        self.assertEqual(discovered, 1)
        self.assertEqual(row["conversation_id"], expected_conversation_id)
        self.assertEqual(row["source_provider"], "apple_messages")
        self.assertEqual(row["imessage_chat_id"], "RCS;-;+15127436385")
        self.assertEqual(alias_row["conversation_id"], expected_conversation_id)

    def test_self_heal_sweep_processes_rows_outside_default_discovery_window(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        legacy_conversation_id = penguin_connect._legacy_conversation_id("owner@gmail.com", "+15127436385")
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, imessage_chat_identifier,
                imessage_service_name, display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'imessage', ?, ?, ?, ?, ?, 'dm', ?, ?, 'active')""",
            (
                "owner@gmail.com",
                legacy_conversation_id,
                "+15127436385",
                "+15127436385",
                "RCS",
                "Taylor",
                '["+15127436385"]',
                "owner+am-legacy@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_aliases
               (conversation_id, alias_email, alias_local_part, status)
               VALUES (?, ?, ?, 'active')""",
            (legacy_conversation_id, "owner+am-legacy@gmail.com", "owner+am-legacy"),
        )
        recent_group = {
            "chat_id": "iMessage;-;family-group",
            "chat_guid": "iMessage;-;family-group",
            "chat_identifier": "family-group",
            "name": "Family Group",
            "chat_type": "group",
            "participants": ["+15127436385", "+14155550101"],
            "message_count": 3,
            "last_message_at": "2026-03-10T10:00:00+00:00",
            "last_message_preview": "hi",
            "service": "iMessage",
            "source_provider": "imessage",
        }
        hidden_dm = {
            "chat_id": "RCS;-;+15127436385",
            "chat_guid": "RCS;-;+15127436385",
            "chat_identifier": "+15127436385",
            "name": "+15127436385",
            "chat_type": "dm",
            "participants": ["+15127436385"],
            "message_count": 8,
            "last_message_at": "2026-03-09T10:00:00+00:00",
            "last_message_preview": "newer rcs",
            "service": "RCS",
            "source_provider": "rcs",
        }

        def fake_browse(search=None, limit=100):
            chats = [recent_group]
            if limit is None:
                chats.append(hidden_dm)
            return {"available": True, "chats": chats}

        with mock.patch("penguin_connect.browse_imessage_chats", side_effect=fake_browse):
            discovered = penguin_connect.ensure_conversations_discovered(self.conn, "owner@gmail.com", max_chats=1)
            stale_row = self.conn.execute(
                "SELECT conversation_id, source_provider FROM penguin_connect_conversations WHERE alias_email = ?",
                ("owner+am-legacy@gmail.com",),
            ).fetchone()
            sweep = penguin_connect.self_heal_conversation_cache(self.conn, "owner@gmail.com")

        expected_conversation_id = penguin_connect.deterministic_conversation_id(
            "owner@gmail.com",
            "dm:5127436385",
            "apple_messages",
        )
        healed_row = self.conn.execute(
            """SELECT conversation_id, source_provider, imessage_chat_id
               FROM penguin_connect_conversations
               WHERE alias_email = ?""",
            ("owner+am-legacy@gmail.com",),
        ).fetchone()
        alias_row = self.conn.execute(
            "SELECT conversation_id FROM penguin_connect_aliases WHERE alias_email = ?",
            ("owner+am-legacy@gmail.com",),
        ).fetchone()

        self.assertEqual(discovered, 1)
        self.assertEqual(stale_row["conversation_id"], legacy_conversation_id)
        self.assertEqual(stale_row["source_provider"], "imessage")
        self.assertTrue(sweep["success"])
        self.assertEqual(sweep["before_count"], 2)
        self.assertEqual(sweep["after_count"], 2)
        self.assertEqual(sweep["swept_conversations"], 2)
        self.assertEqual(sweep["legacy_non_guid_rows_remaining"], 0)
        self.assertEqual(healed_row["conversation_id"], expected_conversation_id)
        self.assertEqual(healed_row["source_provider"], "apple_messages")
        self.assertEqual(healed_row["imessage_chat_id"], "RCS;-;+15127436385")
        self.assertEqual(alias_row["conversation_id"], expected_conversation_id)

    def test_imessage_sync_reads_all_routes_for_unified_apple_messages_dm(self):
        self.conn.execute(
            """UPDATE penguin_connect_conversations
               SET source_provider = ?, chat_type = 'dm', participants = ?, imessage_chat_id = ?,
                   imessage_chat_identifier = ?, imessage_service_name = ?, display_name = ?
               WHERE conversation_id = ?""",
            (
                "apple_messages",
                json.dumps(["+15127436385"]),
                "RCS;-;+15127436385",
                "+15127436385",
                "RCS",
                "Taylor",
                "amc_test",
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.side_effect = [
            {"id": "gm-201", "threadId": "th-routes"},
            {"id": "gm-202", "threadId": "th-routes"},
        ]
        route_messages = {
            "RCS;-;+15127436385": [
                {
                    "chat_id": "RCS;-;+15127436385",
                    "text": "rcs hello",
                    "timestamp": "2026-03-04T09:00:00+00:00",
                    "is_from_me": False,
                    "handle": "+15127436385",
                    "attachments": [],
                    "native_message_id": "201",
                }
            ],
            "SMS;-;+15127436385": [
                {
                    "chat_id": "SMS;-;+15127436385",
                    "text": "sms hello",
                    "timestamp": "2026-03-04T09:01:00+00:00",
                    "is_from_me": False,
                    "handle": "+15127436385",
                    "attachments": [],
                    "native_message_id": "202",
                }
            ],
        }

        def fake_fetch(chat_id, limit=50, since=None):
            return list(route_messages.get(chat_id, []))

        with mock.patch(
            "penguin_connect._list_apple_messages_chat_routes",
            return_value=[
                {"guid": "RCS;-;+15127436385", "chat_id": "RCS;-;+15127436385"},
                {"guid": "SMS;-;+15127436385", "chat_id": "SMS;-;+15127436385"},
            ],
        ), mock.patch("penguin_connect.fetch_imessage_messages", side_effect=fake_fetch), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )

        self.assertEqual(result["imessage_imported"], 2)
        stored = self.conn.execute(
            """SELECT provider_message_id, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ?
               ORDER BY provider_message_id""",
            ("amc_test",),
        ).fetchall()
        self.assertEqual([row["provider_message_id"] for row in stored], ["imessage:201", "imessage:202"])
        metadata_rows = [json.loads(row["metadata"] or "{}") for row in stored]
        self.assertEqual(
            [row["imessage_chat_id"] for row in metadata_rows],
            ["RCS;-;+15127436385", "SMS;-;+15127436385"],
        )

    def test_imessage_import_failure_retried_from_pending_queue(self):
        conv = self._conversation_row()
        msg = {
            "text": "hello from phone",
            "timestamp": "2026-03-04T09:00:00+00:00",
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": None,
            "native_message_id": "42",
        }

        gmail_service = mock.Mock()
        execute_mock = gmail_service.users.return_value.messages.return_value.import_.return_value.execute
        execute_mock.side_effect = [
            Exception("gmail down"),
            {"id": "gm-1", "threadId": "th-1"},
        ]

        with mock.patch("penguin_connect.fetch_imessage_messages", side_effect=[[msg], []]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            first = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )
            row = self.conn.execute(
                "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
                ("amc_test", "imessage:42"),
            ).fetchone()
            metadata = json.loads(row["metadata"] or "{}")
            metadata["next_retry_at"] = "2000-01-01T00:00:00+00:00"
            self.conn.execute(
                "UPDATE penguin_connect_messages SET metadata = ? WHERE conversation_id = ? AND provider_message_id = ?",
                (json.dumps(metadata), "amc_test", "imessage:42"),
            )
            conv = self._conversation_row()
            second = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )

        self.assertEqual(first["imessage_imported"], 1)
        self.assertEqual(first["gmail_imported"], 0)
        self.assertEqual(second["gmail_imported"], 1)
        row = self.conn.execute(
            "SELECT gmail_message_id, gmail_thread_id FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "imessage:42"),
        ).fetchone()
        self.assertEqual(row["gmail_message_id"], "gm-1")
        self.assertEqual(row["gmail_thread_id"], "th-1")

    def test_build_import_email_sets_thread_headers_for_nested_replies(self):
        first_raw = penguin_connect._build_import_email(
            "amc_test",
            "owner+am-test@gmail.com",
            "owner@gmail.com",
            "Family Group",
            {"text": "first", "timestamp": "2026-03-04T09:00:00+00:00", "attachments": []},
            rfc_message_id="<first@example.test>",
        )
        second_raw = penguin_connect._build_import_email(
            "amc_test",
            "owner+am-test@gmail.com",
            "owner@gmail.com",
            "Family Group",
            {"text": "second", "timestamp": "2026-03-04T09:01:00+00:00", "attachments": []},
            rfc_message_id="<second@example.test>",
            in_reply_to="<first@example.test>",
            references=["<root@example.test>", "<first@example.test>"],
        )

        first_msg = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(first_raw))
        second_msg = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(second_raw))

        self.assertEqual(first_msg["Message-ID"], "<first@example.test>")
        self.assertIsNone(first_msg["In-Reply-To"])
        self.assertEqual(second_msg["Message-ID"], "<second@example.test>")
        self.assertEqual(second_msg["In-Reply-To"], "<first@example.test>")
        self.assertEqual(second_msg["References"], "<root@example.test> <first@example.test>")

    def test_build_import_email_includes_gmail_quote_html_for_nested_replies(self):
        raw = penguin_connect._build_import_email(
            "amc_test",
            "owner+am-test@gmail.com",
            "owner@gmail.com",
            "Family Group",
            {"text": "second", "timestamp": "2026-03-04T09:01:00+00:00", "attachments": []},
            rfc_message_id="<second@example.test>",
            in_reply_to="<first@example.test>",
            references=["<first@example.test>"],
            quoted_plain="first",
            quoted_html="<p>first</p>",
            quoted_header="On Mar 4, 2026 at 9:00 AM, Ethan wrote:",
        )

        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        html_part = parsed.get_body(preferencelist=("html",))
        plain_part = parsed.get_body(preferencelist=("plain",))

        self.assertIsNotNone(html_part)
        self.assertIsNotNone(plain_part)
        self.assertIn('class="gmail_quote"', html_part.get_content())
        self.assertIn("On Mar 4, 2026 at 9:00 AM, Ethan wrote:", html_part.get_content())
        self.assertIn("<blockquote", html_part.get_content())
        self.assertIn("On Mar 4, 2026 at 9:00 AM, Ethan wrote:", plain_part.get_content())
        self.assertIn("first", plain_part.get_content())

    def test_build_import_email_sets_provider_header_and_subject_prefix(self):
        raw = penguin_connect._build_import_email(
            "amc_test",
            "owner+am-test@gmail.com",
            "owner@gmail.com",
            "Family Group",
            {"text": "hello", "timestamp": "2026-03-04T09:00:00+00:00", "attachments": []},
            source_provider="telegram",
        )

        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(parsed["Subject"], "Telegram · Family Group")
        self.assertEqual(parsed["X-PenguinConnect-Source-Provider"], "telegram")

    def test_build_import_email_attaches_local_imessage_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = os.path.join(tmp, "photo.png")
            with open(image_path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nfake")
            raw = penguin_connect._build_import_email(
                "amc_test",
                "owner+am-test@gmail.com",
                "owner@gmail.com",
                "Family Group",
                {
                    "text": "photo attached",
                    "timestamp": "2026-03-04T09:00:00+00:00",
                    "attachments": [
                        {"filename": image_path, "mime_type": "image/png", "transfer_name": "family.png"}
                    ],
                },
            )

        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertTrue(parsed.is_multipart())
        parts = [p for p in parsed.iter_attachments()]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_filename(), "family.png")
        self.assertEqual(parts[0].get_content_type(), "image/png")

    def test_build_import_email_sanitizes_attachment_filename_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = os.path.join(tmp, "photo.png")
            with open(image_path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nfake")
            raw = penguin_connect._build_import_email(
                "amc_test",
                "owner+am-test@gmail.com",
                "owner@gmail.com",
                "Family Group",
                {
                    "text": "photo attached",
                    "timestamp": "2026-03-04T09:00:00+00:00",
                    "attachments": [
                        {"filename": image_path, "mime_type": "image/png", "transfer_name": "sticker_1.png\rsticker_2.png"}
                    ],
                },
            )

        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        parts = [p for p in parsed.iter_attachments()]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_filename(), "sticker_1.png_sticker_2.png")

    def test_sync_conversation_marks_email_build_failures_without_raising(self):
        conv = self._conversation_row()
        gmail_service = mock.Mock()

        with mock.patch(
            "penguin_connect.fetch_imessage_messages",
            return_value=[
                {
                    "native_message_id": "2",
                    "timestamp": "2026-03-04T09:01:00+00:00",
                    "text": "second",
                    "is_from_me": False,
                    "attachments": [],
                    "chat_id": "chat-123",
                }
            ],
        ), mock.patch("penguin_connect._get_imessage_unread_count", return_value=0), mock.patch(
            "penguin_connect._build_import_email",
            side_effect=ValueError("bad header"),
        ):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="incremental",
                days=7,
            )

        self.assertEqual(result["gmail_imported"], 0)
        row = self.conn.execute(
            """SELECT metadata FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "imessage:2"),
        ).fetchone()
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["delivery_status"], "pending")
        self.assertIn("email_build_failed:bad header", metadata["last_error"])

    def test_sync_conversation_persists_rendered_nested_email_body_metadata(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, sender_name, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', ?, ?, ?, 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "imessage:1",
                "gm-1",
                "th-1",
                "owner+am-test@gmail.com",
                "Ethan",
                "iMessage · Family Group",
                "first",
                "2026-03-04T09:00:00+00:00",
                json.dumps(
                    {
                        "rfc_message_id": "<first@example.test>",
                        "email_body_plain": "first",
                        "email_body_html": "<p>first</p>",
                        "delivery_status": "delivered",
                    }
                ),
            ),
        )
        self.conn.commit()
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.return_value = {
            "id": "gm-2",
            "threadId": "th-1",
        }

        with mock.patch(
            "penguin_connect.fetch_imessage_messages",
            return_value=[
                {
                    "native_message_id": "2",
                    "timestamp": "2026-03-04T09:01:00+00:00",
                    "text": "second",
                    "is_from_me": False,
                    "attachments": [],
                    "chat_id": "chat-123",
                }
            ],
        ), mock.patch("penguin_connect._get_imessage_unread_count", return_value=0):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="incremental",
                days=7,
            )

        self.assertEqual(result["gmail_imported"], 1)
        row = self.conn.execute(
            """SELECT metadata FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "imessage:2"),
        ).fetchone()
        metadata = json.loads(row["metadata"])
        self.assertIn("On Mar 4, 2026 at 1:00 AM, Ethan wrote:", metadata["email_body_plain"])
        self.assertIn('class="gmail_quote"', metadata["email_body_html"])
        self.assertIn("<p>first</p>", metadata["email_body_html"])

    def test_imessage_sync_uses_contact_name_for_dm_subject_and_sender(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET display_name = ?, chat_type = 'dm', participants = ? WHERE conversation_id = ?",
            ("+17144741613", json.dumps(["+17144741613"]), "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO contacts (first_name, last_name, organization, phone, phone_normalized, email, source_db)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Kam", "(Shine Capital)", "", "+17144741613", "+17144741613", None, "test"),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.return_value = {
            "id": "gm-contact-1",
            "threadId": "th-contact-1",
        }
        msg = {
            "text": "hello from Kam",
            "timestamp": "2026-03-04T09:00:00+00:00",
            "is_from_me": False,
            "handle": "+17144741613",
            "attachments": [],
            "native_message_id": "contact-1",
        }

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=[msg]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )

        row = self.conn.execute(
            "SELECT sender_name, subject FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "imessage:contact-1"),
        ).fetchone()
        self.assertEqual(row["sender_name"], "Kam (Shine Capital)")
        self.assertEqual(row["subject"], "iMessage · Kam (Shine Capital)")

        import_body = gmail_service.users.return_value.messages.return_value.import_.call_args.kwargs["body"]
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(import_body["raw"]))
        self.assertEqual(parsed["From"], '"Kam (Shine Capital)" <owner+am-test@gmail.com>')
        self.assertEqual(parsed["Subject"], "iMessage · Kam (Shine Capital)")

    def test_full_verify_refreshes_existing_imessage_contact_metadata(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET display_name = ?, chat_type = 'dm', participants = ? WHERE conversation_id = ?",
            ("+17144741613", json.dumps(["+17144741613"]), "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, sender_name, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', ?, ?, ?, 'imessage_to_email', ?, ?, ?, ?, ?, ?, ?)""",
            (
                "amc_test",
                "imessage:contact-1",
                "gm-contact-1",
                "th-contact-1",
                "owner+am-test@gmail.com",
                "+17144741613",
                "iMessage · +17144741613",
                "hello from Kam",
                "2026-03-04T09:00:00+00:00",
                1,
                json.dumps({"native_message_id": "contact-1", "is_from_me": False}),
            ),
        )
        self.conn.execute(
            """INSERT INTO contacts (first_name, last_name, organization, phone, phone_normalized, email, source_db)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Kam", "(Shine Capital)", "", "+17144741613", "+17144741613", None, "test"),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        msg = {
            "text": "hello from Kam",
            "timestamp": "2026-03-04T09:00:00+00:00",
            "is_from_me": False,
            "handle": "+17144741613",
            "attachments": [],
            "native_message_id": "contact-1",
        }

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=[msg]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="incremental",
                days=7,
                verify_all=True,
            )

        row = self.conn.execute(
            "SELECT sender_name, subject, gmail_message_id FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "imessage:contact-1"),
        ).fetchone()
        self.assertEqual(row["sender_name"], "Kam (Shine Capital)")
        self.assertEqual(row["subject"], "iMessage · Kam (Shine Capital)")
        self.assertEqual(row["gmail_message_id"], "gm-contact-1")
        gmail_service.users.return_value.messages.return_value.import_.assert_not_called()

    def test_imessage_sync_uses_group_title_for_group_subject(self):
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.return_value = {
            "id": "gm-group-1",
            "threadId": "th-group-1",
        }
        msg = {
            "text": "hello group",
            "timestamp": "2026-03-04T09:00:00+00:00",
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": [],
            "native_message_id": "group-1",
        }

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=[msg]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )

        row = self.conn.execute(
            "SELECT sender_name, subject FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "imessage:group-1"),
        ).fetchone()
        self.assertEqual(row["sender_name"], "+14155550111")
        self.assertEqual(row["subject"], "iMessage · Family Group")

        import_body = gmail_service.users.return_value.messages.return_value.import_.call_args.kwargs["body"]
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(import_body["raw"]))
        self.assertEqual(parsed["Subject"], "iMessage · Family Group")

    def test_resolve_imessage_sender_prefers_contact_over_unresolved_push_name(self):
        self.conn.execute(
            """INSERT INTO contacts (first_name, last_name, organization, phone, phone_normalized, email, source_db)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Kam", "(Shine Capital)", "", "+17144741613", "+17144741613", None, "test"),
        )
        conv = self._conversation_row()
        sender_name, _subject_name = penguin_connect._resolve_imessage_sender_and_subject(
            self.conn,
            conv,
            {"handle": "+17144741613", "push_name": "+17144741613"},
        )
        self.assertEqual(sender_name, "Kam (Shine Capital)")

    def test_resolve_imessage_sender_uses_group_title_for_group_subject(self):
        conv = self._conversation_row()
        _sender_name, subject_name = penguin_connect._resolve_imessage_sender_and_subject(
            self.conn,
            conv,
            {"handle": "+14155550111", "push_name": ""},
        )
        self.assertEqual(subject_name, "Family Group")

    def test_imessage_sync_sets_nested_reply_headers(self):
        conv = self._conversation_row()
        msgs = [
            {
                "text": "hello one",
                "timestamp": "2026-03-04T09:00:00+00:00",
                "is_from_me": False,
                "handle": "+14155550111",
                "attachments": [],
                "native_message_id": "101",
            },
            {
                "text": "hello two",
                "timestamp": "2026-03-04T09:01:00+00:00",
                "is_from_me": False,
                "handle": "+14155550111",
                "attachments": [],
                "native_message_id": "102",
            },
        ]
        gmail_service = mock.Mock()
        import_execute = gmail_service.users.return_value.messages.return_value.import_.return_value.execute
        import_execute.side_effect = [
            {"id": "gm-101", "threadId": "th-nested"},
            {"id": "gm-102", "threadId": "th-nested"},
        ]

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=msgs), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )

        self.assertEqual(result["gmail_imported"], 2)
        call_args = gmail_service.users.return_value.messages.return_value.import_.call_args_list
        first_raw = call_args[0].kwargs["body"]["raw"]
        second_raw = call_args[1].kwargs["body"]["raw"]
        first_msg = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(first_raw))
        second_msg = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(second_raw))
        self.assertIsNotNone(first_msg["Message-ID"])
        self.assertEqual(second_msg["In-Reply-To"], first_msg["Message-ID"])
        self.assertIn(first_msg["Message-ID"], second_msg["References"])

    def test_startup_catchup_imessage_sync_yields_after_five_gmail_imports_for_incremental(self):
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        msgs = [
            {
                "text": f"hello {index}",
                "timestamp": f"2026-03-04T09:0{index}:00+00:00",
                "is_from_me": False,
                "handle": "+14155550111",
                "attachments": [],
                "native_message_id": str(index),
            }
            for index in range(1, 7)
        ]

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=msgs), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=0
        ), mock.patch(
            "penguin_connect._build_import_email", return_value=b"raw"
        ), mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            side_effect=[({"id": f"gm-{index}", "threadId": "th-yield"}, None, "th-yield") for index in range(1, 6)],
        ), mock.patch(
            "penguin_connect._sleep_after_gmail_write"
        ), mock.patch(
            "penguin_connect._ready_incremental_sync_job_waiting", return_value=True
        ) as mock_waiting:
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="startup_catchup",
                days=7,
            )

        self.assertTrue(result["preempted_for_incremental"])
        self.assertFalse(result["bootstrap_ready"])
        self.assertEqual(result["gmail_imported"], 5)
        self.assertEqual(mock_waiting.call_count, 1)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM penguin_connect_messages WHERE conversation_id = ? AND provider = 'imessage'",
            ("amc_test",),
        ).fetchone()[0]
        self.assertEqual(count, 5)

    def test_startup_catchup_partial_bootstrap_resumes_from_native_cursor(self):
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        same_ts = "2026-03-04T09:00:00+00:00"
        first_msg = {
            "text": "first",
            "timestamp": same_ts,
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": [],
            "native_message_id": "1",
        }
        second_msg = {
            "text": "second",
            "timestamp": same_ts,
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": [],
            "native_message_id": "2",
        }
        fetch_calls: list[tuple[object, object]] = []

        def fake_fetch(_chat_id, limit=50, since=None, since_native_message_id=None):
            fetch_calls.append((since, since_native_message_id))
            if since_native_message_id in (None, ""):
                return [first_msg, second_msg]
            if since_native_message_id == "1":
                return [second_msg]
            return []

        import_side_effect = [
            ({"id": "gm-1", "threadId": "th-resume"}, None, "th-resume"),
            ({"id": "gm-2", "threadId": "th-resume"}, None, "th-resume"),
        ]

        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_STARTUP_INCREMENTAL_PREEMPTION_IMPORT_COUNT": "1"},
            clear=False,
        ), mock.patch(
            "penguin_connect.fetch_imessage_messages",
            side_effect=fake_fetch,
        ), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=0
        ), mock.patch(
            "penguin_connect._build_import_email", return_value=b"raw"
        ), mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            side_effect=import_side_effect,
        ), mock.patch(
            "penguin_connect._sleep_after_gmail_write"
        ), mock.patch(
            "penguin_connect._ready_incremental_sync_job_waiting",
            side_effect=[True, False, False],
        ):
            first_result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="startup_catchup",
                days=7,
            )
            second_result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="startup_catchup",
                days=7,
            )

        self.assertTrue(first_result["preempted_for_incremental"])
        self.assertEqual(first_result["gmail_imported"], 1)
        self.assertFalse(second_result["preempted_for_incremental"])
        self.assertEqual(second_result["gmail_imported"], 1)
        self.assertEqual(fetch_calls[1], (same_ts, "1"))
        state = self.conn.execute(
            """SELECT last_imessage_ts, last_imessage_native_message_id
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()
        self.assertEqual(state["last_imessage_ts"], same_ts)
        self.assertEqual(state["last_imessage_native_message_id"], "2")

    def test_imessage_sync_recovers_thread_by_parent_rfc_message_id(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = NULL WHERE conversation_id = ?",
            ("amc_test",),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, body_text, message_timestamp,
                is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'gmail', 'gmail:parent', 'email_to_imessage', ?, ?, ?, 1, ?, ?, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "parent",
                "2026-03-04T08:59:00+00:00",
                json.dumps({"rfc_message_id": "<parent@example.test>", "rfc_references": ["<root@example.test>"]}),
                "gmail-parent-id",
                None,
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        messages_api = gmail_service.users.return_value.messages.return_value
        messages_api.list.return_value.execute.return_value = {"messages": [{"id": "gmail-parent-id", "threadId": "thread-parent"}]}
        messages_api.import_.return_value.execute.return_value = {"id": "gmail-child-id", "threadId": "thread-parent"}
        msg = {
            "text": "child from iMessage",
            "timestamp": "2026-03-04T09:00:00+00:00",
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": [],
            "native_message_id": "777",
        }

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=[msg]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            penguin_connect._sync_conversation_imessage_to_gmail(self.conn, gmail_service, conv, mode="incremental", days=7)

        first_import_body = messages_api.import_.call_args.kwargs["body"]
        self.assertNotIn("threadId", first_import_body)
        self.assertTrue(messages_api.list.called)
        row = self.conn.execute(
            "SELECT gmail_thread_id FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "imessage:777"),
        ).fetchone()
        self.assertEqual(row["gmail_thread_id"], "thread-parent")

    def test_imessage_sync_uses_rfc_threading_without_passing_thread_id(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ? WHERE conversation_id = ?",
            ("thread-stale", "amc_test"),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        messages_api = gmail_service.users.return_value.messages.return_value
        messages_api.import_.return_value.execute.return_value = {"id": "gmail-new-id", "threadId": "thread-fresh"}
        msg = {
            "text": "fallback send",
            "timestamp": "2026-03-04T10:00:00+00:00",
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": [],
            "native_message_id": "888",
        }

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=[msg]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )

        self.assertEqual(result["gmail_imported"], 1)
        calls = messages_api.import_.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertNotIn("threadId", calls[0].kwargs["body"])
        row = self.conn.execute(
            "SELECT gmail_thread_id FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "imessage:888"),
        ).fetchone()
        self.assertEqual(row["gmail_thread_id"], "thread-stale")

    def test_gmail_send_failure_retried_from_pending_queue(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"hello from gmail").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-1",
            "threadId": "thread-1",
            "historyId": "h-1",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "hello from gmail",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Hi"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", side_effect=[[{"id": "gmail-1"}], []]), mock.patch(
            "penguin_connect.send_imessage", side_effect=[(False, "send_failed"), (True, None)]
        ):
            first = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )
            row = self.conn.execute(
                "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
                ("amc_test", "gmail:gmail-1"),
            ).fetchone()
            metadata = json.loads(row["metadata"] or "{}")
            metadata["next_retry_at"] = "2000-01-01T00:00:00+00:00"
            self.conn.execute(
                "UPDATE penguin_connect_messages SET metadata = ? WHERE conversation_id = ? AND provider_message_id = ?",
                (json.dumps(metadata), "amc_test", "gmail:gmail-1"),
            )
            second = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(first["email_to_imessage"], 0)
        self.assertEqual(second["email_to_imessage"], 1)
        row = self.conn.execute(
            "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:gmail-1"),
        ).fetchone()
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["max_retries"], 3)
        self.assertEqual(metadata["send_result"], "imessage_ok")
        self.assertEqual(metadata["retry_count"], 1)

    def test_gmail_retry_queue_sends_error_notice_after_third_failure(self):
        conv = self._conversation_row()
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'gmail', 'gmail:failed-1', 'failed-1', 'thread-failed-1', 'email_to_imessage',
                       ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Status",
                "message body to retry",
                "2026-03-10T10:00:00+00:00",
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "retry_count": 2,
                        "max_retries": 3,
                        "gmail_message_id": "failed-1",
                        "gmail_thread_id": "thread-failed-1",
                        "rfc_message_id": "<failed-1@example.test>",
                        "rfc_references": ["<root@example.test>"],
                    }
                ),
            ),
        )
        self.conn.commit()
        gmail_service = mock.Mock()

        with mock.patch("penguin_connect.send_imessage", return_value=(False, "send_failed")) as mock_send, mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            return_value=({"id": "notice-1", "threadId": "thread-failed-1"}, None, None),
        ) as mock_notice:
            retried = penguin_connect._retry_pending_gmail_to_imessage(self.conn, conv, gmail_service=gmail_service)
        self.conn.rollback()

        self.assertEqual(retried, 0)
        mock_send.assert_called_once()
        mock_notice.assert_called_once()

        raw_notice = mock_notice.call_args.args[1]
        padded_notice = raw_notice + "=" * (-len(raw_notice) % 4)
        notice_email = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(padded_notice))
        notice_body = notice_email.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("PENGUIN_CONNECT", notice_email["From"])
        self.assertIn("Following message ran into errors sending:", notice_body)
        self.assertIn("message body to retry", notice_body)
        self.assertEqual(notice_email["X-PenguinConnect-Bridge"], penguin_connect.DELIVERY_ERROR_HEADER_VALUE)

        row = self.conn.execute(
            "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:failed-1"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata["delivery_status"], "failed_permanent")
        self.assertEqual(metadata["retry_count"], 3)
        self.assertEqual(metadata["error_notice_gmail_message_id"], "notice-1")
        self.assertEqual(metadata["error_notice_gmail_thread_id"], "thread-failed-1")
        self.assertIsNotNone(metadata["error_notice_sent_at"])

    def test_gmail_retry_queue_fails_stale_pending_message_when_source_advanced(self):
        conv = self._conversation_row()
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-10T10:30:00+00:00",
                "2026-03-10T10:30:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'gmail', 'gmail:stale-1', 'stale-1', 'thread-stale-1', 'email_to_imessage',
                       ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Status",
                "late email reply",
                "2026-03-10T10:00:00+00:00",
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "retry_count": 0,
                        "max_retries": 3,
                        "gmail_message_id": "stale-1",
                        "gmail_thread_id": "thread-stale-1",
                        "rfc_message_id": "<stale-1@example.test>",
                    }
                ),
            ),
        )
        self.conn.commit()
        gmail_service = mock.Mock()

        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send, mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            return_value=({"id": "notice-stale-1", "threadId": "thread-stale-1"}, None, None),
        ) as mock_notice:
            retried = penguin_connect._retry_pending_gmail_to_imessage(self.conn, conv, gmail_service=gmail_service)
        self.conn.rollback()

        self.assertEqual(retried, 0)
        mock_send.assert_not_called()
        mock_notice.assert_called_once()
        row = self.conn.execute(
            "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:stale-1"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata["delivery_status"], "failed_permanent")
        self.assertEqual(metadata["reason"], "newer_source_message_synced")
        self.assertEqual(metadata["stale_gmail_message_timestamp"], "2026-03-10T10:00:00+00:00")
        self.assertEqual(metadata["newer_source_message_timestamp"], "2026-03-10T10:30:00+00:00")
        self.assertEqual(metadata["error_notice_gmail_message_id"], "notice-stale-1")
        self.assertEqual(metadata["error_notice_gmail_thread_id"], "thread-stale-1")

    def test_gmail_sync_persists_rfc_thread_headers(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"hello from gmail").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-rfc-1",
            "threadId": "thread-rfc-1",
            "historyId": "h-rfc-1",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "hello from gmail",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Threaded"},
                    {"name": "Message-ID", "value": "<mail-1@example.test>"},
                    {"name": "In-Reply-To", "value": "<mail-0@example.test>"},
                    {"name": "References", "value": "<mail-root@example.test> <mail-0@example.test>"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-rfc-1"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ):
            penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        row = self.conn.execute(
            "SELECT sender_name, metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:gmail-rfc-1"),
        ).fetchone()
        self.assertEqual(row["sender_name"], "Owner")
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata.get("rfc_message_id"), "<mail-1@example.test>")
        self.assertEqual(metadata.get("rfc_in_reply_to"), "<mail-0@example.test>")
        self.assertEqual(
            metadata.get("rfc_references"),
            ["<mail-root@example.test>", "<mail-0@example.test>"],
        )

    def test_gmail_sync_fails_stale_email_when_newer_source_message_exists(self):
        conv = self._conversation_row()
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-10T10:30:00+00:00",
                "2026-03-10T10:30:00+00:00",
            ),
        )
        payload_data = base64.urlsafe_b64encode(b"reply while server was down").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-stale-sync-1",
            "threadId": "thread-stale-sync-1",
            "historyId": "h-stale-sync-1",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1741600800000",
            "snippet": "reply while server was down",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Status"},
                    {"name": "Message-ID", "value": "<stale-sync-1@example.test>"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "filename": "", "body": {"data": payload_data}},
                    {
                        "mimeType": "image/png",
                        "filename": "photo.png",
                        "body": {"attachmentId": "a-stale-sync-1", "size": 12},
                    },
                ],
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg
        self.conn.commit()

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-stale-sync-1"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send, mock.patch(
            "penguin_connect._stage_gmail_attachments_for_imessage"
        ) as mock_stage, mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            return_value=({"id": "notice-stale-sync-1", "threadId": "thread-stale-sync-1"}, None, None),
        ) as mock_notice:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )
        self.conn.rollback()

        self.assertEqual(result["email_to_imessage"], 0)
        mock_send.assert_not_called()
        mock_stage.assert_not_called()
        mock_notice.assert_called_once()
        stored = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-stale-sync-1"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(stored["body_text"], "reply while server was down")
        self.assertEqual(metadata["delivery_status"], "failed_permanent")
        self.assertEqual(metadata["reason"], "newer_source_message_synced")
        self.assertEqual(metadata["newer_source_message_timestamp"], "2026-03-10T10:30:00+00:00")
        self.assertEqual(
            metadata["stale_gmail_message_timestamp"],
            penguin_connect._iso_from_gmail_internal_date("1741600800000"),
        )
        self.assertEqual(metadata["attachments"][0]["filename"], "photo.png")
        self.assertEqual(metadata["attachments_forwarded"], [])
        self.assertEqual(metadata["attachments_skipped"], [])

    def test_gmail_attachments_forward_binary_to_imessage(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"see attachment").decode("utf-8").rstrip("=")
        attachment_data = base64.urlsafe_b64encode(b"\x89PNG\r\n\x1a\nimg").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-attach-bin-1",
            "threadId": "thread-attach-bin-1",
            "historyId": "h-attach-bin-1",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Photo"},
                    {"name": "Message-ID", "value": "<attach-bin-1@example.test>"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "filename": "", "body": {"data": payload_data}},
                    {
                        "mimeType": "image/png",
                        "filename": "photo.png",
                        "body": {"attachmentId": "a-bin-1", "size": 12},
                    },
                ],
            },
        }
        gmail_service = mock.Mock()
        messages_api = gmail_service.users.return_value.messages.return_value
        messages_api.get.return_value.execute.return_value = full_msg
        messages_api.attachments.return_value.get.return_value.execute.return_value = {"data": attachment_data}

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-attach-bin-1"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 1)
        self.assertEqual(mock_send.call_args.args[1], "see attachment")
        self.assertEqual(len(mock_send.call_args.kwargs.get("attachment_paths") or []), 1)
        row = self.conn.execute(
            "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:gmail-attach-bin-1"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(len(metadata.get("attachments_forwarded") or []), 1)

    def test_gmail_attachment_only_message_forwards_placeholder_to_imessage(self):
        conv = self._conversation_row()
        full_msg = {
            "id": "gmail-attach-1",
            "threadId": "thread-attach-1",
            "historyId": "h-attach-1",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Photo"},
                    {"name": "Message-ID", "value": "<attach-1@example.test>"},
                ],
                "parts": [
                    {
                        "mimeType": "image/jpeg",
                        "filename": "photo.jpg",
                        "body": {"attachmentId": "a-1", "size": 1234},
                    }
                ],
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-attach-1"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 1)
        sent_text = mock_send.call_args.args[1]
        self.assertIn("[Email attachment]", sent_text)
        self.assertIn("photo.jpg", sent_text)
        row = self.conn.execute(
            "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:gmail-attach-1"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata.get("attachments")[0]["filename"], "photo.jpg")

    def test_gmail_split_threads_reconcile_to_canonical_thread(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ? WHERE conversation_id = ?",
            ("thread-main", "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'gmail', 'gmail:root', 'email_to_imessage', ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Alice",
                "Root message",
                "2026-03-04T08:00:00+00:00",
                json.dumps({"rfc_message_id": "<root@example.test>", "send_result": "imessage_ok", "delivery_status": "delivered"}),
                "gmail-root",
                "thread-main",
            ),
        )
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"split thread reply").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-split-1",
            "threadId": "thread-split",
            "historyId": "h-split-1",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700003600000",
            "snippet": "split thread reply",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Family Group"},
                    {"name": "Message-ID", "value": "<split-1@example.test>"},
                    {"name": "In-Reply-To", "value": "<root@example.test>"},
                    {"name": "References", "value": "<root@example.test>"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-split-1"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ):
            penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )
            repaired = penguin_connect._repair_split_gmail_messages(self.conn, gmail_service, "amc_test", "thread-main")

        self.assertEqual(repaired, 0)
        conv_after = self._conversation_row()
        self.assertEqual(conv_after["gmail_thread_id"], "thread-main")
        row = self.conn.execute(
            "SELECT gmail_thread_id, metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:gmail-split-1"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(row["gmail_thread_id"], "thread-split")
        self.assertEqual(metadata.get("gmail_observed_thread_id"), "thread-split")
        self.assertEqual(metadata.get("canonical_gmail_thread_id"), "thread-main")
        self.assertTrue(metadata.get("thread_reconciled"))
        gmail_service.users.return_value.messages.return_value.insert.assert_not_called()
        gmail_service.users.return_value.messages.return_value.trash.assert_not_called()

    def test_bridge_owned_thread_repair_updates_exact_message_only(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ? WHERE conversation_id = ?",
            ("thread-main", "amc_test"),
        )
        root_message_id = penguin_connect._build_bridge_rfc_message_id("amc_test", "imessage:root")
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'gmail', 'gmail:root', 'email_to_imessage', ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Alice",
                "Root message",
                "2026-03-04T08:00:00+00:00",
                json.dumps({"rfc_message_id": root_message_id, "send_result": "imessage_ok", "delivery_status": "delivered"}),
                "gmail-root",
                "thread-main",
            ),
        )
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"split thread reply").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-split-safe",
            "threadId": "thread-split",
            "historyId": "h-split-safe",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700003600000",
            "snippet": "split thread reply",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Family Group"},
                    {"name": "Message-ID", "value": "<split-safe@example.test>"},
                    {"name": "In-Reply-To", "value": root_message_id},
                    {"name": "References", "value": root_message_id},
                ],
                "body": {"data": payload_data},
            },
        }
        original_email = EmailMessage()
        original_email["From"] = "Owner <owner@gmail.com>"
        original_email["To"] = "owner+am-test@gmail.com"
        original_email["Subject"] = "Family Group"
        original_email["Message-ID"] = "<split-safe@example.test>"
        original_email["In-Reply-To"] = root_message_id
        original_email["References"] = root_message_id
        original_email.set_content("split thread reply")
        original_raw = base64.urlsafe_b64encode(original_email.as_bytes()).decode("utf-8")
        gmail_service = mock.Mock()
        users_api = gmail_service.users.return_value
        messages_api = users_api.messages.return_value
        inserted_execute = messages_api.insert.return_value.execute
        inserted_execute.return_value = {"id": "gmail-repaired", "threadId": "thread-main"}
        messages_api.trash.return_value.execute.return_value = {"id": "gmail-split-safe"}

        def get_execute_side_effect(*args, **kwargs):
            fmt = kwargs.get("format")
            response = full_msg if fmt == "full" else {"id": "gmail-split-safe", "raw": original_raw, "labelIds": ["SENT", "INBOX", "UNREAD"]}
            exec_mock = mock.Mock()
            exec_mock.execute.return_value = response
            return exec_mock

        messages_api.get.side_effect = get_execute_side_effect

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-split-safe"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ):
            penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )
            repaired = penguin_connect._repair_split_gmail_messages(self.conn, gmail_service, "amc_test", "thread-main")

        self.assertEqual(repaired, 1)
        users_api.threads.assert_not_called()
        messages_api.delete.assert_not_called()
        messages_api.batchDelete.assert_not_called()
        messages_api.batchModify.assert_not_called()
        messages_api.insert.assert_called_once()
        messages_api.trash.assert_called_once_with(userId="me", id="gmail-split-safe")
        row = self.conn.execute(
            "SELECT provider_message_id, gmail_message_id, gmail_thread_id, metadata FROM penguin_connect_messages WHERE conversation_id = ? AND gmail_message_id = ?",
            ("amc_test", "gmail-repaired"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(row["provider_message_id"], "gmail:gmail-repaired")
        self.assertEqual(row["gmail_thread_id"], "thread-main")
        self.assertEqual(metadata.get("gmail_original_message_id"), "gmail-split-safe")
        self.assertEqual(metadata.get("gmail_original_thread_id"), "thread-split")
        self.assertEqual(metadata.get("gmail_repair_state"), "completed")

    def test_imessage_sync_prefers_canonical_thread_over_newer_split_thread(self):
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ? WHERE conversation_id = ?",
            ("thread-main", "amc_test"),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'gmail', 'gmail:main-parent', 'email_to_imessage', ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Alice",
                "Canonical parent",
                "2026-03-04T08:00:00+00:00",
                json.dumps({"rfc_message_id": "<main-parent@example.test>"}),
                "gmail-main-parent",
                "thread-main",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'gmail', 'gmail:split-latest', 'email_to_imessage', ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Alice",
                "Split latest",
                "2026-03-04T09:00:00+00:00",
                json.dumps({"rfc_message_id": "<split-latest@example.test>"}),
                "gmail-split-latest",
                "thread-split",
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.return_value = {
            "id": "gmail-new",
            "threadId": "thread-main",
        }
        msg = {
            "text": "join back",
            "timestamp": "2026-03-04T10:00:00+00:00",
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": [],
            "native_message_id": "999",
        }

        with mock.patch("penguin_connect.fetch_imessage_messages", return_value=[msg]), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn, gmail_service, conv, mode="incremental", days=7
            )

        import_body = gmail_service.users.return_value.messages.return_value.import_.call_args.kwargs["body"]
        raw = import_body["raw"]
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertNotIn("threadId", import_body)
        self.assertEqual(parsed["In-Reply-To"], "<main-parent@example.test>")
        self.assertNotEqual(parsed["In-Reply-To"], "<split-latest@example.test>")

    def test_gmail_nested_reply_only_sends_latest_text_to_imessage(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'gmail', 'gmail:root', 'email_to_imessage', ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Alice",
                "Root context",
                "2026-03-04T08:00:00+00:00",
                json.dumps({"rfc_message_id": "<root@example.test>", "send_result": "imessage_ok", "delivery_status": "delivered"}),
                "gmail-root",
                "thread-main",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text,
                message_timestamp, is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'imessage', 'imessage:child', 'imessage_to_email', ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                "amc_test",
                "owner+am-test@gmail.com",
                "Bridge",
                "Bridge context",
                "2026-03-04T08:30:00+00:00",
                json.dumps(
                    {
                        "rfc_message_id": "<bridge@example.test>",
                        "rfc_in_reply_to": "<root@example.test>",
                        "rfc_references": ["<root@example.test>"],
                    }
                ),
                "gmail-bridge",
                "thread-main",
            ),
        )
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"Latest nested reply").decode("utf-8").rstrip("=")
        leaf_internal_date = str(int(datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc).timestamp() * 1000))
        full_msg = {
            "id": "gmail-leaf",
            "threadId": "thread-main",
            "historyId": "h-leaf",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": leaf_internal_date,
            "snippet": "Latest nested reply",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Nested"},
                    {"name": "Message-ID", "value": "<leaf@example.test>"},
                    {"name": "In-Reply-To", "value": "<bridge@example.test>"},
                    {"name": "References", "value": "<root@example.test> <bridge@example.test>"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-leaf"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send:
            penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        sent_text = mock_send.call_args.args[1]
        self.assertIn("Latest nested reply", sent_text)
        self.assertNotIn("Quoted context:", sent_text)
        self.assertEqual(sent_text, "Latest nested reply")

    def test_gmail_plain_text_reply_strips_quoted_chain_before_send(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(
            b"Latest reply\n\nSent from my iPhone\n\nOn Tue, Mar 10, 2026 at 10:00 AM Alice <alice@example.com> wrote:\n> Older line"
        ).decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-quoted",
            "threadId": "thread-quoted",
            "historyId": "h-quoted",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700007200000",
            "snippet": "Latest reply",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Nested"},
                    {"name": "Message-ID", "value": "<quoted@example.test>"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-quoted"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send:
            penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        sent_text = mock_send.call_args.args[1]
        self.assertEqual(sent_text, "Latest reply")
        stored = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-quoted"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(stored["body_text"], "Latest reply")
        self.assertIn("On Tue, Mar 10, 2026 at 10:00 AM", metadata["source_body_text_raw"])
        self.assertTrue(metadata["gmail_quoted_content_removed"])
        self.assertTrue(metadata["gmail_signature_removed"])

    def test_gmail_snippet_only_body_is_ignored_when_parser_is_not_confident(self):
        conv = self._conversation_row()
        full_msg = {
            "id": "gmail-snippet-only",
            "threadId": "thread-snippet-only",
            "historyId": "h-snippet-only",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700007200000",
            "snippet": "Quick status update",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Status"},
                    {"name": "Message-ID", "value": "<snippet-only@example.test>"},
                ],
                "parts": [],
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-snippet-only"}]), mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            return_value=({"id": "notice-1", "threadId": "thread-snippet-only"}, None, "thread-snippet-only"),
        ) as mock_notice, mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 0)
        mock_send.assert_not_called()
        stored = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-snippet-only"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(stored["body_text"], "Quick status update")
        self.assertEqual(metadata["reason"], "ambiguous_email_body")
        self.assertEqual(metadata["gmail_body_source"], "snippet")
        self.assertFalse(metadata["gmail_body_safe_for_send"])
        self.assertIn("snippet_only", metadata["gmail_body_safety_flags"])

    def test_gmail_snippet_only_body_sends_rejection_notice(self):
        conv = self._conversation_row()
        full_msg = {
            "id": "gmail-snippet-reject",
            "threadId": "thread-snippet-reject",
            "historyId": "h-snippet-reject",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700007200000",
            "snippet": "Quick status update",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Status"},
                    {"name": "Message-ID", "value": "<snippet-reject@example.test>"},
                ],
                "parts": [],
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-snippet-reject"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send, mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            return_value=({"id": "reject-notice-1", "threadId": "thread-snippet-reject"}, None, None),
        ) as mock_notice:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 0)
        mock_send.assert_not_called()
        mock_notice.assert_called_once()

        raw_notice = mock_notice.call_args.args[1]
        padded_notice = raw_notice + "=" * (-len(raw_notice) % 4)
        notice_email = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(padded_notice))
        notice_body = notice_email.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("PenguinConnect rejected this email reply", notice_body)
        self.assertIn("Quick status update", notice_body)
        self.assertEqual(notice_email["X-PenguinConnect-Bridge"], penguin_connect.DELIVERY_REJECTION_HEADER_VALUE)

        stored = self.conn.execute(
            """SELECT metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-snippet-reject"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(metadata["reason"], "ambiguous_email_body")
        self.assertEqual(metadata["rejection_notice_gmail_message_id"], "reject-notice-1")
        self.assertEqual(metadata["rejection_notice_gmail_thread_id"], "thread-snippet-reject")
        self.assertIsNotNone(metadata["rejection_notice_sent_at"])
        self.assertIsNotNone(metadata["rejection_notice_sent_at"])
        self.assertEqual(metadata["rejection_notice_gmail_message_id"], "reject-notice-1")
        mock_notice.assert_called_once()
        raw_email = mock_notice.call_args.args[1]
        parsed_notice = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(raw_email + ("=" * (-len(raw_email) % 4)))
        )
        self.assertEqual(parsed_notice[penguin_connect.PENGUINCONNECT_HEADER], penguin_connect.DELIVERY_REJECTION_HEADER_VALUE)
        self.assertIn("could not be confirmed as net-new text only", parsed_notice.get_content())

    def test_gmail_wrapped_reply_header_is_stripped_before_send(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(
            (
                "Test email\r\n\r\nOn Fri, Mar 13, 2026 at 1:38 PM Dhruv Roonga <\r\n"
                "hgaddipati1118+am-e3526656f885@gmail.com> wrote:\r\n\r\n"
                "> why did we have the on... stuff thats bad."
            ).encode("utf-8")
        ).decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-wrapped-quoted",
            "threadId": "thread-wrapped-quoted",
            "historyId": "h-wrapped-quoted",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700007200000",
            "snippet": "Test email",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Status"},
                    {"name": "Message-ID", "value": "<wrapped-quoted@example.test>"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": payload_data},
                    }
                ],
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-wrapped-quoted"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send:
            penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        sent_text = mock_send.call_args.args[1]
        self.assertEqual(sent_text, "Test email")
        stored = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-wrapped-quoted"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(stored["body_text"], "Test email")
        self.assertIn("On Fri, Mar 13, 2026 at 1:38 PM", metadata["source_body_text_raw"])
        self.assertTrue(metadata["gmail_quoted_content_removed"])
        self.assertTrue(metadata["gmail_body_safe_for_send"])

    def test_gmail_prefers_html_quote_stripping_over_plain_text_part(self):
        conv = self._conversation_row()
        plain_payload_data = base64.urlsafe_b64encode(
            (
                "Test email\r\n\r\nOn Fri, Mar 13, 2026 at 1:38 PM Dhruv Roonga <\r\n"
                "hgaddipati1118+am-e3526656f885@gmail.com> wrote:\r\n\r\n"
                "> why did we have the on... stuff thats bad."
            ).encode("utf-8")
        ).decode("utf-8").rstrip("=")
        html_payload_data = base64.urlsafe_b64encode(
            (
                "<div>Test email</div>"
                '<div class="gmail_quote">'
                '<div class="gmail_attr">On Fri, Mar 13, 2026 at 1:38 PM Dhruv Roonga wrote:</div>'
                "<blockquote>why did we have the on... stuff thats bad.</blockquote>"
                "</div>"
            ).encode("utf-8")
        ).decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-html-preferred",
            "threadId": "thread-html-preferred",
            "historyId": "h-html-preferred",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700007200000",
            "snippet": "Test email",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Status"},
                    {"name": "Message-ID", "value": "<html-preferred@example.test>"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": plain_payload_data},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": html_payload_data},
                    },
                ],
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-html-preferred"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send:
            penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        sent_text = mock_send.call_args.args[1]
        self.assertEqual(sent_text, "Test email")
        stored = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-html-preferred"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(stored["body_text"], "Test email")
        self.assertEqual(metadata["gmail_body_source"], "html")
        self.assertTrue(metadata["gmail_quoted_content_removed"])
        self.assertTrue(metadata["gmail_body_safe_for_send"])

    def test_gmail_draft_to_alias_is_ignored_and_never_sent_to_imessage(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"draft that should never send").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-draft-1",
            "threadId": "thread-draft-1",
            "historyId": "h-draft-1",
            "labelIds": ["DRAFT"],
            "internalDate": "1700007200000",
            "snippet": "draft that should never send",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "Subject", "value": "Draft"},
                    {"name": "Message-ID", "value": "<draft-1@example.test>"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-draft-1"}]), mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery"
        ) as mock_notice, mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 0)
        mock_send.assert_not_called()
        mock_notice.assert_not_called()
        stored = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-draft-1"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(stored["body_text"], "draft that should never send")
        self.assertEqual(metadata["reason"], "gmail_draft_message")
        self.assertEqual(metadata["delivery_status"], "ignored")
        self.assertEqual(metadata["labels"], ["DRAFT"])

    def test_gmail_duplicate_sent_messages_in_same_thread_only_send_once(self):
        conv = self._conversation_row()

        def build_full_msg(message_id: str, internal_date: str) -> dict:
            payload_data = base64.urlsafe_b64encode(b"same reply body").decode("utf-8").rstrip("=")
            return {
                "id": message_id,
                "threadId": "thread-dup-1",
                "historyId": f"h-{message_id}",
                "labelIds": ["SENT", "INBOX", "UNREAD"],
                "internalDate": internal_date,
                "snippet": "same reply body",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "Owner <owner@gmail.com>"},
                        {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                        {"name": "Subject", "value": "Duplicate"},
                        {"name": "Message-ID", "value": f"<{message_id}@example.test>"},
                    ],
                    "body": {"data": payload_data},
                },
            }

        full_messages = {
            "gmail-dup-1": build_full_msg("gmail-dup-1", "1700007200000"),
            "gmail-dup-2": build_full_msg("gmail-dup-2", "1700007202000"),
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.side_effect = (
            lambda **kwargs: mock.Mock(execute=mock.Mock(return_value=full_messages[kwargs["id"]]))
        )

        with mock.patch(
            "penguin_connect._list_gmail_messages_to_alias",
            return_value=[{"id": "gmail-dup-1"}, {"id": "gmail-dup-2"}],
        ), mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 1)
        mock_send.assert_called_once()

        first = self.conn.execute(
            """SELECT metadata FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-dup-1"),
        ).fetchone()
        second = self.conn.execute(
            """SELECT metadata FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-dup-2"),
        ).fetchone()
        first_metadata = json.loads(first["metadata"] or "{}")
        second_metadata = json.loads(second["metadata"] or "{}")
        self.assertEqual(first_metadata["delivery_status"], "delivered")
        self.assertEqual(second_metadata["delivery_status"], "ignored")
        self.assertEqual(second_metadata["reason"], "duplicate_recent_gmail_message")
        self.assertEqual(second_metadata["duplicate_of_provider_message_id"], "gmail:gmail-dup-1")

    def test_cleanup_stale_alias_drafts_deletes_old_split_drafts_only(self):
        conv = self._conversation_row()
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, sender_name, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', ?, ?, ?, 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "imessage:root",
                "gmail-root",
                "thread-main",
                "owner+am-test@gmail.com",
                "Taylor",
                "iMessage · Family Group",
                "hello",
                "2026-03-10T10:00:00+00:00",
                json.dumps({"rfc_message_id": "<am.root@penguinconnect.local>"}),
            ),
        )
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ? WHERE conversation_id = ?",
            ("thread-main", "amc_test"),
        )
        now_dt = datetime.now(timezone.utc)
        old_ms = str(int((now_dt - timedelta(minutes=90)).timestamp() * 1000))
        recent_ms = str(int((now_dt - timedelta(minutes=5)).timestamp() * 1000))

        gmail_service = mock.Mock()

        def list_messages(**params):
            self.assertIn("in:drafts", params["q"])
            response = mock.Mock()
            response.execute.return_value = {
                "messages": [
                    {"id": "draft-old"},
                    {"id": "draft-main"},
                    {"id": "draft-recent"},
                ]
            }
            return response

        def get_message(*, userId, id, format, metadataHeaders):
            response = mock.Mock()
            if id == "draft-old":
                payload = {
                    "labelIds": ["DRAFT"],
                    "threadId": "thread-split-old",
                    "internalDate": old_ms,
                    "payload": {"headers": [{"name": "To", "value": "Owner <owner+am-test@gmail.com>"}]},
                }
            elif id == "draft-main":
                payload = {
                    "labelIds": ["DRAFT"],
                    "threadId": "thread-main",
                    "internalDate": old_ms,
                    "payload": {"headers": [{"name": "To", "value": "Owner <owner+am-test@gmail.com>"}]},
                }
            else:
                payload = {
                    "labelIds": ["DRAFT"],
                    "threadId": "thread-split-recent",
                    "internalDate": recent_ms,
                    "payload": {"headers": [{"name": "To", "value": "Owner <owner+am-test@gmail.com>"}]},
                }
            response.execute.return_value = payload
            return response

        def list_drafts(**_params):
            response = mock.Mock()
            response.execute.return_value = {
                "drafts": [
                    {"id": "draft-resource-old", "message": {"id": "draft-old"}},
                    {"id": "draft-resource-main", "message": {"id": "draft-main"}},
                    {"id": "draft-resource-recent", "message": {"id": "draft-recent"}},
                ]
            }
            return response

        gmail_service.users.return_value.messages.return_value.list.side_effect = list_messages
        gmail_service.users.return_value.messages.return_value.get.side_effect = get_message
        gmail_service.users.return_value.drafts.return_value.list.side_effect = list_drafts

        with mock.patch("penguin_connect.log_action"), mock.patch(
            "penguin_connect._delete_gmail_draft",
            return_value=None,
        ) as mock_delete:
            deleted = penguin_connect._cleanup_stale_alias_drafts(self.conn, gmail_service, conv, "thread-main")

        self.assertEqual(deleted, 1)
        mock_delete.assert_called_once_with(gmail_service, "draft-resource-old")

    def test_gmail_sync_cleans_stale_alias_drafts_without_new_sent_messages(self):
        conv = self._conversation_row()
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, sender_name, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', ?, ?, ?, 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "imessage:root",
                "gmail-root",
                "thread-main",
                "owner+am-test@gmail.com",
                "Taylor",
                "iMessage · Family Group",
                "hello",
                "2026-03-10T10:00:00+00:00",
                json.dumps({"rfc_message_id": "<am.root@penguinconnect.local>"}),
            ),
        )
        self.conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ? WHERE conversation_id = ?",
            ("thread-main", "amc_test"),
        )
        gmail_service = mock.Mock()

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[]), mock.patch(
            "penguin_connect._cleanup_stale_alias_drafts",
            return_value=4,
        ) as mock_cleanup:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 0)
        self.assertEqual(result["alias_drafts_deleted"], 4)
        mock_cleanup.assert_called_once_with(self.conn, gmail_service, conv, "thread-main")

    def test_gmail_message_without_exact_alias_recipient_is_ignored(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"wrong alias target").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-wrong-alias-1",
            "threadId": "thread-wrong-alias-1",
            "historyId": "h-wrong-alias-1",
            "labelIds": ["SENT"],
            "internalDate": "1700007200000",
            "snippet": "wrong alias target",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-other@gmail.com>"},
                    {"name": "Subject", "value": "Wrong alias"},
                    {"name": "Message-ID", "value": "<wrong-alias-1@example.test>"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", return_value=[{"id": "gmail-wrong-alias-1"}]), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send:
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                gmail_email="owner@gmail.com",
                allowed_senders=["owner@gmail.com"],
                days=7,
            )

        self.assertEqual(result["email_to_imessage"], 0)
        mock_send.assert_not_called()
        stored = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-wrong-alias-1"),
        ).fetchone()
        metadata = json.loads(stored["metadata"] or "{}")
        self.assertEqual(stored["body_text"], "wrong alias target")
        self.assertEqual(metadata["reason"], "alias_recipient_mismatch")
        self.assertEqual(metadata["delivery_status"], "ignored")

    def test_retry_policy_backoff_and_cap(self):
        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_RETRY_BASE_SECONDS": "2",
                "PENGUIN_CONNECT_RETRY_MAX_BACKOFF_SECONDS": "5",
                "PENGUIN_CONNECT_MAX_RETRIES": "3",
            },
            clear=False,
        ):
            now_dt = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
            metadata = {"retry_count": 0, "max_retries": 3, "delivery_status": "pending"}

            first = penguin_connect._mark_delivery_failure(metadata, "send_result", "e1", now_dt)
            self.assertEqual(first["retry_count"], 1)
            self.assertEqual(first["delivery_status"], "pending")
            self.assertEqual(first["next_retry_at"], (now_dt + timedelta(seconds=2)).isoformat())

            second = penguin_connect._mark_delivery_failure(first, "send_result", "e2", now_dt)
            self.assertEqual(second["retry_count"], 2)
            self.assertEqual(second["next_retry_at"], (now_dt + timedelta(seconds=4)).isoformat())

            third = penguin_connect._mark_delivery_failure(second, "send_result", "e3", now_dt)
            self.assertEqual(third["retry_count"], 3)
            self.assertEqual(third["delivery_status"], "failed_permanent")
            self.assertIsNone(third["next_retry_at"])

    def test_get_sync_metrics_reports_queue_and_failures(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', 'im1', 'imessage_to_email', 'owner+am-test@gmail.com', ?, 1, ?)""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "retry_count": 1,
                        "max_retries": 3,
                        "first_failed_at": "2026-03-04T10:01:00+00:00",
                        "last_error": "gmail_import_failed",
                    }
                ),
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, message_timestamp, is_read, metadata)
               VALUES (?, 'gmail', 'gm1', 'email_to_imessage', 'owner@gmail.com', ?, 1, ?)""",
            (
                "amc_test",
                "2026-03-04T11:00:00+00:00",
                json.dumps(
                    {
                        "delivery_status": "failed_permanent",
                        "retry_count": 3,
                        "max_retries": 3,
                        "last_error": "imessage_failed",
                    }
                ),
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, message_timestamp, is_read, metadata)
               VALUES (?, 'gmail', 'gm2', 'email_to_imessage', 'owner@gmail.com', ?, 1, ?)""",
            (
                "amc_test",
                "2026-03-04T11:05:00+00:00",
                json.dumps({"blocked": True, "delivery_status": "blocked"}),
            ),
        )
        metrics = penguin_connect.get_sync_metrics(self.conn)
        imsg = metrics["directions"]["imessage_to_gmail"]
        gsync = metrics["directions"]["gmail_to_imessage"]
        queue = metrics["durable_queue"]

        self.assertEqual(imsg["retry_queue_count"], 1)
        self.assertEqual(imsg["failed_with_error_count"], 1)
        self.assertIsNotNone(imsg["oldest_pending_at"])
        self.assertEqual(gsync["failed_permanent_count"], 1)
        self.assertEqual(gsync["blocked_count"], 1)
        self.assertEqual(metrics["totals"]["retry_queue_count"], 1)
        self.assertEqual(metrics["totals"]["failed_permanent_count"], 1)
        self.assertIn("queued", queue)
        self.assertIn("leased", queue)

    def test_get_sync_metrics_handles_invalid_metadata_json(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', 'im2', 'imessage_to_email', 'owner+am-test@gmail.com', ?, 1, ?)""",
            ("amc_test", "2026-03-04T12:00:00+00:00", "{not-json"),
        )

        metrics = penguin_connect.get_sync_metrics(self.conn)

        self.assertEqual(metrics["directions"]["imessage_to_gmail"]["retry_queue_count"], 1)
        self.assertEqual(metrics["totals"]["retry_queue_count"], 1)

    def test_refresh_send_as_aliases_uses_fresh_cached_values(self):
        self.conn.execute(
            """UPDATE penguin_connect_accounts
               SET primary_send_as = ?, send_as_aliases = ?, updated_at = ?
               WHERE gmail_email = ?""",
            (
                "ops@company.com",
                json.dumps(["owner@gmail.com", "ops@company.com"]),
                datetime.now(timezone.utc).isoformat(),
                "owner@gmail.com",
            ),
        )
        gmail_service = mock.Mock()

        aliases, primary = penguin_connect._refresh_send_as_aliases(self.conn, gmail_service, "owner@gmail.com")

        self.assertEqual(aliases, ["ops@company.com", "owner@gmail.com"])
        self.assertEqual(primary, "ops@company.com")
        gmail_service.users.assert_not_called()

    def test_refresh_send_as_aliases_falls_back_to_cached_values_on_rate_limit(self):
        self.conn.execute(
            """UPDATE penguin_connect_accounts
               SET primary_send_as = ?, send_as_aliases = ?, updated_at = ?
               WHERE gmail_email = ?""",
            (
                "ops@company.com",
                json.dumps(["owner@gmail.com", "ops@company.com"]),
                (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "owner@gmail.com",
            ),
        )
        gmail_service = mock.Mock()
        gmail_service.users.return_value.settings.return_value.sendAs.return_value.list.return_value.execute.side_effect = (
            penguin_connect._GmailRetryableError(120, 429, "userRateLimitExceeded")
        )

        aliases, primary = penguin_connect._refresh_send_as_aliases(self.conn, gmail_service, "owner@gmail.com")

        self.assertEqual(aliases, ["ops@company.com", "owner@gmail.com"])
        self.assertEqual(primary, "ops@company.com")

    def test_run_incremental_sync_maps_imessage_db_unreadable(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        try:
            with mock.patch("db.get_connection", return_value=conn), mock.patch(
                "penguin_connect.sync_conversations",
                side_effect=sqlite3.OperationalError("unable to open database file"),
            ):
                result = penguin_connect.run_incremental_sync()
        finally:
            conn.close()

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "imessage_db_unreadable")

    def test_imessage_retry_queue_respects_next_retry_and_caps(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', 'im-retry', 'imessage_to_email', ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "owner+am-test@gmail.com",
                "Tester",
                "pending message",
                "2026-03-04T10:00:00+00:00",
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "retry_count": 1,
                        "max_retries": 3,
                        "next_retry_at": "2999-01-01T00:00:00+00:00",
                    }
                ),
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()

        imported, _ = penguin_connect._retry_pending_imessage_to_gmail(self.conn, gmail_service, conv)
        self.assertEqual(imported, 0)
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.assert_not_called()

        self.conn.execute(
            "UPDATE penguin_connect_messages SET metadata = ? WHERE conversation_id = ? AND provider_message_id = ?",
            (
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "retry_count": 3,
                        "max_retries": 3,
                    }
                ),
                "amc_test",
                "im-retry",
            ),
        )
        imported, _ = penguin_connect._retry_pending_imessage_to_gmail(self.conn, gmail_service, conv)
        self.assertEqual(imported, 0)
        row = self.conn.execute(
            "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "im-retry"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata["delivery_status"], "failed_permanent")

    def test_imessage_retry_queue_applies_gmail_write_pause(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', 'im-pause', 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "owner+am-test@gmail.com",
                "Tester",
                "iMessage · Family Group",
                "pending pause message",
                "2026-03-04T10:30:00+00:00",
                json.dumps({"delivery_status": "pending", "retry_count": 0, "max_retries": 3}),
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.import_.return_value.execute.return_value = {
            "id": "gm-pause-1",
            "threadId": "th-pause-1",
        }

        with mock.patch("penguin_connect._sleep_after_gmail_write") as mock_pause:
            imported, _ = penguin_connect._retry_pending_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                gmail_write_pause_seconds=0.4,
            )

        self.assertEqual(imported, 1)
        mock_pause.assert_called_once_with(0.4)

    def test_gmail_retry_queue_respects_next_retry(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'gmail', 'gm-retry', 'email_to_imessage', ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "retry me",
                "2026-03-04T11:00:00+00:00",
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "retry_count": 1,
                        "max_retries": 3,
                        "next_retry_at": "2999-01-01T00:00:00+00:00",
                    }
                ),
            ),
        )
        conv = self._conversation_row()

        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send:
            retried = penguin_connect._retry_pending_gmail_to_imessage(self.conn, conv)

        self.assertEqual(retried, 0)
        mock_send.assert_not_called()

    def test_retry_queues_do_not_starve_on_terminal_rows(self):
        conv = self._conversation_row()
        for i in range(600):
            self.conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, body_text, message_timestamp, is_read, metadata)
                   VALUES (?, 'gmail', ?, 'email_to_imessage', ?, ?, ?, 1, ?)""",
                (
                    "amc_test",
                    f"gmail:done-{i}",
                    "owner@gmail.com",
                    "already delivered",
                    f"2026-03-04T09:{i % 60:02d}:00+00:00",
                    json.dumps({"delivery_status": "delivered", "send_result": "imessage_ok"}),
                ),
            )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'gmail', 'gmail:pending-late', 'email_to_imessage', ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "needs retry",
                "2026-03-04T23:59:00+00:00",
                json.dumps({"delivery_status": "pending", "retry_count": 0, "max_retries": 3}),
            ),
        )

        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send:
            retried = penguin_connect._retry_pending_gmail_to_imessage(self.conn, conv)

        self.assertEqual(retried, 1)
        mock_send.assert_called_once()

    def test_sync_conversations_is_single_flight_within_same_lane(self):
        active = 0
        peak = 0
        state_lock = threading.Lock()
        results = []

        def fake_sync(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        with mock.patch("penguin_connect._sync_conversations_unlocked", side_effect=fake_sync):
            t1 = threading.Thread(target=lambda: results.append(penguin_connect.sync_conversations(None, mode="incremental")))
            t2 = threading.Thread(target=lambda: results.append(penguin_connect.sync_conversations(None, mode="incremental")))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(peak, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["success"] for r in results))

    def test_sync_conversations_allows_incremental_and_backfill_in_parallel(self):
        active = 0
        peak = 0
        state_lock = threading.Lock()
        results = []

        def fake_sync(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        with mock.patch("penguin_connect._sync_conversations_unlocked", side_effect=fake_sync):
            t1 = threading.Thread(target=lambda: results.append(penguin_connect.sync_conversations(None, mode="incremental")))
            t2 = threading.Thread(target=lambda: results.append(penguin_connect.sync_conversations(None, mode="backfill")))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(peak, 2)
        self.assertEqual(len(results), 2)
        self.assertCountEqual([result["mode"] for result in results], ["incremental", "backfill"])

    def test_sync_conversations_skips_conversation_locked_by_other_lane(self):
        conv = self._conversation_row()
        acquired, existing = penguin_connect._try_acquire_conversation_sync(
            conv["conversation_id"],
            run_id="backfill-run",
            mode="backfill",
        )
        self.assertTrue(acquired)
        self.assertIsNone(existing)

        with mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 1, "before_count": 1, "after_count": 1},
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._list_recent_gmail_alias_activity",
            return_value=({}, {"available": False, "reason": "mocked"}),
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail"
        ) as mock_imsg, mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage"
        ) as mock_gmail, mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        penguin_connect._release_conversation_sync(conv["conversation_id"], run_id="backfill-run")
        self.assertTrue(stats["success"])
        self.assertEqual(stats["skipped_locked_conversations"], 1)
        self.assertEqual(stats["selected_conversations"], 1)
        mock_imsg.assert_not_called()
        mock_gmail.assert_not_called()

    def test_run_incremental_sync_does_not_skip_without_initial_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db_path = db.DB_PATH
            old_data_dir = db.DATA_DIR
            db.DATA_DIR = Path(tmp)
            db.DB_PATH = Path(tmp) / "cache.db"
            try:
                db.init_db()
                with mock.patch(
                    "penguin_connect.sync_conversations",
                    return_value={"success": True, "mode": "incremental", "selected_conversations": 0},
                ) as mock_sync:
                    result = penguin_connect.run_incremental_sync()
            finally:
                db.DB_PATH = old_db_path
                db.DATA_DIR = old_data_dir

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "incremental")
        mock_sync.assert_called_once()

    def test_enqueue_sync_job_dedupes_incremental_jobs(self):
        first = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="incremental",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        second = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="incremental",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )

        self.assertTrue(first["enqueued"])
        self.assertFalse(second["enqueued"])
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(penguin_connect._pending_sync_jobs_count(self.conn), 1)

    def test_sync_job_worker_retries_then_succeeds(self):
        penguin_connect.enqueue_sync_job(
            self.conn,
            mode="incremental",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_SYNC_JOB_RETRY_BASE_SECONDS": "1",
                "PENGUIN_CONNECT_SYNC_JOB_RETRY_MAX_BACKOFF_SECONDS": "5",
                "PENGUIN_CONNECT_SYNC_JOB_MAX_ATTEMPTS": "4",
            },
            clear=False,
        ), mock.patch(
            "penguin_connect.sync_conversations",
            side_effect=[
                {"success": False, "error": "database_busy_retry"},
                {"success": True, "mode": "incremental", "selected_conversations": 0},
            ],
        ):
            first = penguin_connect.run_sync_job_worker_once(self.conn, owner="test-worker")
            self.assertFalse(first["success"])
            self.assertTrue(first["queue_job_retry_scheduled"])
            self.assertEqual(first["queue_job_status"], "queued")

            self.conn.execute(
                "UPDATE penguin_connect_jobs SET next_run_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", first["queue_job_id"]),
            )

            second = penguin_connect.run_sync_job_worker_once(self.conn, owner="test-worker")
            self.assertTrue(second["success"])
            self.assertEqual(second["queue_job_status"], "succeeded")

        row = self.conn.execute(
            "SELECT status, attempt_count FROM penguin_connect_jobs WHERE id = ?",
            (first["queue_job_id"],),
        ).fetchone()
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["attempt_count"], 1)

    def test_sync_job_worker_with_incremental_dedupe_does_not_lease_startup_job(self):
        startup = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="startup_catchup",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        incremental = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="incremental",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        self.conn.commit()

        seen_modes = []

        def fake_sync(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            seen_modes.append(mode)
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        with mock.patch("penguin_connect.sync_conversations", side_effect=fake_sync):
            result = penguin_connect.run_sync_job_worker_once(
                self.conn,
                owner="watcher",
                dedupe_key="sync:incremental",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["queue_job_id"], incremental["job_id"])
        self.assertEqual(seen_modes, ["incremental"])

        startup_row = self.conn.execute(
            "SELECT status, lease_owner, started_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
            (startup["job_id"],),
        ).fetchone()
        incremental_row = self.conn.execute(
            "SELECT status, lease_owner, started_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
            (incremental["job_id"],),
        ).fetchone()
        self.assertEqual(startup_row["status"], "queued")
        self.assertIsNone(startup_row["lease_owner"])
        self.assertIsNone(startup_row["started_at"])
        self.assertIsNone(startup_row["finished_at"])
        self.assertEqual(incremental_row["status"], "succeeded")

    def test_sync_job_worker_with_startup_dedupe_does_not_lease_incremental_job(self):
        incremental = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="incremental",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        startup = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="startup_catchup",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        self.conn.commit()

        seen_modes = []

        def fake_sync(_conn, mode="incremental", days=7, hours=None, verify_all=False):
            seen_modes.append(mode)
            return {"success": True, "mode": mode, "days": days, "hours": hours, "verify_all": verify_all}

        with mock.patch("penguin_connect.sync_conversations", side_effect=fake_sync):
            result = penguin_connect.run_sync_job_worker_once(
                self.conn,
                owner="startup",
                dedupe_key="sync:startup_catchup",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "startup_catchup")
        self.assertEqual(result["queue_job_id"], startup["job_id"])
        self.assertEqual(seen_modes, ["startup_catchup"])

        incremental_row = self.conn.execute(
            "SELECT status, lease_owner, started_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
            (incremental["job_id"],),
        ).fetchone()
        startup_row = self.conn.execute(
            "SELECT status, lease_owner, started_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
            (startup["job_id"],),
        ).fetchone()
        self.assertEqual(incremental_row["status"], "queued")
        self.assertIsNone(incremental_row["lease_owner"])
        self.assertIsNone(incremental_row["started_at"])
        self.assertIsNone(incremental_row["finished_at"])
        self.assertEqual(startup_row["status"], "succeeded")

    def test_sync_job_worker_requeues_gmail_rate_limit_without_counting_failure(self):
        queued = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="incremental",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        self.conn.commit()

        with mock.patch(
            "penguin_connect.sync_conversations",
            return_value={
                "success": True,
                "mode": "incremental",
                "skipped": True,
                "reason": "gmail_rate_limited",
                "retry_after_seconds": 120,
            },
        ):
            result = penguin_connect.run_sync_job_worker_once(self.conn, owner="test-worker")

        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "gmail_rate_limited")
        self.assertEqual(result["queue_job_status"], "queued")
        self.assertTrue(result["queue_job_retry_scheduled"])
        self.assertEqual(result["queue_job_retry_after_seconds"], 120)
        row = self.conn.execute(
            "SELECT status, attempt_count, last_error, next_run_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
            (queued["job_id"],),
        ).fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["attempt_count"], 0)
        self.assertEqual(row["last_error"], "gmail_rate_limited")
        self.assertIsNotNone(row["next_run_at"])
        self.assertIsNone(row["finished_at"])

    def test_sync_job_worker_requeues_backfill_daily_cap_skip_without_counting_failure(self):
        queued = penguin_connect.enqueue_sync_job(
            self.conn,
            mode="startup_catchup",
            days=7,
            hours=None,
            verify_all=False,
            dedupe=True,
        )
        self.conn.commit()

        with mock.patch(
            "penguin_connect.sync_conversations",
            return_value={
                "success": True,
                "mode": "startup_catchup",
                "skipped": True,
                "reason": "backfill_daily_cap_reached",
                "retry_after_seconds": 3600,
            },
        ):
            result = penguin_connect.run_sync_job_worker_once(self.conn, owner="test-worker")

        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "backfill_daily_cap_reached")
        self.assertEqual(result["queue_job_status"], "queued")
        self.assertEqual(result["queue_job_retry_after_seconds"], 3600)
        row = self.conn.execute(
            "SELECT status, attempt_count, last_error, next_run_at, finished_at FROM penguin_connect_jobs WHERE id = ?",
            (queued["job_id"],),
        ).fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["attempt_count"], 0)
        self.assertEqual(row["last_error"], "backfill_daily_cap_reached")
        self.assertIsNotNone(row["next_run_at"])
        self.assertIsNone(row["finished_at"])

    def test_incremental_sync_processes_one_recent_conversation_per_run(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
               alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_ready",
                "chat-ready",
                "Ready Conversation",
                "owner+am-ready@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at,
                next_full_verify_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_ready",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-20T10:05:00+00:00",
            ),
        )

        call_order = []

        def fake_imessage_sync(_conn, _gmail_service, conv, mode, days, hours=None, cutoff_iso=None, verify_all=False):
            call_order.append(("imessage", conv["conversation_id"]))
            return {"imessage_imported": 0, "gmail_imported": 0}

        def fake_gmail_sync(
            _conn,
            _gmail_service,
            conv,
            gmail_email,
            allowed_senders,
            days,
            hours=None,
            cutoff_iso=None,
            verify_all=False,
        ):
            call_order.append(("gmail", conv["conversation_id"]))
            return {"email_to_imessage": 0, "blocked_sender_count": 0}

        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=2), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-ready",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ), mock.patch(
            "penguin_connect._list_recent_gmail_alias_activity",
            return_value=({}, {}),
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            side_effect=fake_imessage_sync,
        ), mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage",
            side_effect=fake_gmail_sync,
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        self.assertTrue(stats["success"])
        self.assertEqual(stats["selected_conversations"], 1)
        self.assertEqual(stats["pending_bootstrap_conversations"], 1)
        self.assertEqual(stats["hot_conversations"], 1)
        self.assertEqual(stats["selection_strategy"], "activity_prioritized_round_robin")
        self.assertEqual(
            call_order,
            [
                ("imessage", "amc_ready"),
                ("gmail", "amc_ready"),
            ],
        )

    def test_incremental_sync_skips_when_gmail_rate_limited(self):
        future_pause = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_rate_limited_until, gmail_rate_limit_streak)
               VALUES (?, ?, ?)""",
            ("owner@gmail.com", future_pause, 2),
        )

        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=1), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch("penguin_connect._refresh_send_as_aliases") as mock_refresh:
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        self.assertTrue(stats["success"])
        self.assertTrue(stats["skipped"])
        self.assertEqual(stats["reason"], "gmail_rate_limited")
        self.assertGreaterEqual(stats["retry_after_seconds"], 1)
        self.assertEqual(stats["rate_limit_streak"], 2)
        mock_refresh.assert_not_called()

    def test_startup_catchup_skips_when_backfill_daily_cap_reached(self):
        base_dt = datetime.now(timezone.utc) - timedelta(hours=4)
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_backfill_daily_import_count, gmail_backfill_daily_window_started_at)
               VALUES (?, ?, ?)""",
            ("owner@gmail.com", 500, base_dt.isoformat()),
        )

        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_BACKFILL_DAILY_GMAIL_IMPORT_CAP": "500"},
            clear=False,
        ), mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True},
        ), mock.patch(
            "penguin_connect._build_gmail_service",
            return_value=(mock.Mock(), None),
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
        ) as mock_refresh:
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="startup_catchup", days=7)

        self.assertTrue(stats["success"])
        self.assertTrue(stats["skipped"])
        self.assertEqual(stats["reason"], "backfill_daily_cap_reached")
        self.assertGreaterEqual(stats["retry_after_seconds"], (19 * 60 * 60) + (59 * 60))
        mock_refresh.assert_not_called()

    def test_startup_catchup_skips_when_backfill_rate_limit_guard_threshold_hit(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, gmail_rate_limit_streak)
               VALUES (?, ?)""",
            ("owner@gmail.com", 9),
        )

        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_BACKFILL_RATE_LIMIT_GUARD_STREAK": "8",
                "PENGUIN_CONNECT_BACKFILL_RATE_LIMIT_GUARD_PAUSE_SECONDS": "3600",
            },
            clear=False,
        ), mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True},
        ), mock.patch(
            "penguin_connect._build_gmail_service",
            return_value=(mock.Mock(), None),
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
        ) as mock_refresh:
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="startup_catchup", days=7)

        self.assertTrue(stats["success"])
        self.assertTrue(stats["skipped"])
        self.assertEqual(stats["reason"], "backfill_rate_limit_guarded")
        self.assertEqual(stats["retry_after_seconds"], 3600)
        self.assertEqual(stats["rate_limit_streak"], 9)
        self.assertEqual(stats["guard_streak_threshold"], 8)
        mock_refresh.assert_not_called()

    def test_gmail_execute_retries_retryable_errors(self):
        class FakeResp:
            status = 429
            headers = {"Retry-After": "1"}

        class FakeError(Exception):
            def __init__(self):
                super().__init__("rateLimitExceeded")
                self.resp = FakeResp()
                self.reason = "rateLimitExceeded"

        attempts = {"count": 0}

        def flaky():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise FakeError()
            return "ok"

        with mock.patch("penguin_connect.time.sleep") as mock_sleep:
            result = penguin_connect._gmail_execute(flaky)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_list_recent_gmail_alias_activity_skips_missing_history_messages(self):
        class FakeResp:
            status = 404
            headers = {}

        class FakeNotFound(Exception):
            def __init__(self):
                super().__init__("Requested entity was not found.")
                self.resp = FakeResp()
                self.reason = "notFound"

        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, last_gmail_history_id)
               VALUES (?, ?)""",
            ("owner@gmail.com", "100"),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.history.return_value.list.return_value.execute.return_value = {
            "historyId": "101",
            "history": [
                {
                    "id": "101",
                    "messagesAdded": [
                        {"message": {"id": "missing"}},
                        {"message": {"id": "present"}},
                    ],
                }
            ],
        }

        def get_message(*, userId, id, format, metadataHeaders):
            response = mock.Mock()
            if id == "missing":
                response.execute.side_effect = FakeNotFound()
            else:
                response.execute.return_value = {
                    "labelIds": ["SENT"],
                    "payload": {
                        "headers": [
                            {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                        ]
                    },
                    "internalDate": "1700000000000",
                }
            return response

        gmail_service.users.return_value.messages.return_value.get.side_effect = get_message

        with mock.patch("penguin_connect.log_action") as mock_log:
            recent, meta = penguin_connect._list_recent_gmail_alias_activity(
                self.conn,
                gmail_service,
                "owner@gmail.com",
                [conv],
            )

        self.assertEqual(recent["amc_test"]["message_count"], 1)
        self.assertEqual(meta["last_gmail_history_id"], "101")
        poll_state = self.conn.execute(
            "SELECT last_gmail_history_id FROM penguin_connect_poll_state WHERE gmail_email = ?",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertEqual(poll_state["last_gmail_history_id"], "101")
        mock_log.assert_any_call(
            "gmail_history_message_missing",
            gmail_email="owner@gmail.com",
            gmail_message_id="missing",
            gmail_history_id="101",
        )

    def test_list_recent_gmail_alias_activity_ignores_draft_messages(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_poll_state
               (gmail_email, last_gmail_history_id)
               VALUES (?, ?)""",
            ("owner@gmail.com", "100"),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        gmail_service.users.return_value.history.return_value.list.return_value.execute.return_value = {
            "historyId": "101",
            "history": [
                {
                    "id": "101",
                    "messagesAdded": [
                        {"message": {"id": "draft"}},
                        {"message": {"id": "sent"}},
                    ],
                }
            ],
        }

        def get_message(*, userId, id, format, metadataHeaders):
            response = mock.Mock()
            response.execute.return_value = {
                "labelIds": ["DRAFT"] if id == "draft" else ["SENT"],
                "payload": {
                    "headers": [
                        {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    ]
                },
                "internalDate": "1700000000000" if id == "draft" else "1700003600000",
            }
            return response

        gmail_service.users.return_value.messages.return_value.get.side_effect = get_message

        recent, meta = penguin_connect._list_recent_gmail_alias_activity(
            self.conn,
            gmail_service,
            "owner@gmail.com",
            [conv],
        )

        self.assertEqual(recent["amc_test"]["message_count"], 1)
        self.assertEqual(recent["amc_test"]["last_message_at"], penguin_connect._iso_from_gmail_internal_date("1700003600000"))
        self.assertEqual(meta["last_gmail_history_id"], "101")

    def test_extract_alias_recipients_ignores_blank_header_slots(self):
        recipients = penguin_connect._extract_alias_recipients(
            {
                "to": "Owner <owner+am-test@gmail.com>",
                "cc": "",
                "delivered-to": "",
                "x-original-to": "",
                "x-forwarded-to": "",
            }
        )

        self.assertEqual(recipients, ["owner+am-test@gmail.com"])

    def test_backfill_marks_conversation_bootstrapped_after_success(self):
        with mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 1, "before_count": 1, "after_count": 1},
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:55:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            return_value={"imessage_imported": 0, "gmail_imported": 0, "bootstrap_ready": True},
        ), mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage",
            return_value={"email_to_imessage": 0, "blocked_sender_count": 0},
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="backfill", days=7, hours=5)

        state = self.conn.execute(
            """SELECT initial_sync_completed_at, next_full_verify_at, full_verify_completed_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        self.assertTrue(stats["success"])
        self.assertEqual(stats["self_heal_sweep"]["swept_conversations"], 1)
        self.assertIsNotNone(state)
        self.assertTrue(state["initial_sync_completed_at"])
        self.assertTrue(state["next_full_verify_at"])
        self.assertIsNone(state["full_verify_completed_at"])

    def test_backfill_keeps_bootstrap_pending_without_materialized_history(self):
        with mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 1, "before_count": 1, "after_count": 1},
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:55:00+00:00",
                        "message_count": 2,
                    }
                ],
            },
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            return_value={"imessage_imported": 0, "gmail_imported": 0, "bootstrap_ready": False},
        ), mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage",
            return_value={"email_to_imessage": 0, "blocked_sender_count": 0},
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="backfill", days=7, hours=5)

        state = self.conn.execute(
            "SELECT initial_sync_completed_at FROM penguin_connect_sync_state WHERE conversation_id = ?",
            ("amc_test",),
        ).fetchone()

        self.assertTrue(stats["success"])
        self.assertIsNone(state)

    def test_startup_catchup_run_yields_to_incremental_after_preempted_conversation(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_second",
                "chat-456",
                "Second Group",
                "owner+am-second@gmail.com",
            ),
        )
        selected = self.conn.execute(
            """SELECT c.*,
                      s.last_imessage_ts,
                      s.last_gmail_ts,
                      s.last_message_ts,
                      s.last_gmail_history_id,
                      s.pending_gmail_activity_at,
                      s.initial_sync_completed_at,
                      s.next_full_verify_at,
                      s.full_verify_completed_at,
                      s.last_synced_at AS sync_state_last_synced_at
               FROM penguin_connect_conversations c
               LEFT JOIN penguin_connect_sync_state s ON s.conversation_id = c.conversation_id
               WHERE c.gmail_email = ?
               ORDER BY c.conversation_id ASC""",
            ("owner@gmail.com",),
        ).fetchall()

        with mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 2, "before_count": 2, "after_count": 2},
        ), mock.patch(
            "penguin_connect.refresh_conversation_exclusions",
            return_value={"updated": False},
        ), mock.patch(
            "penguin_connect._ensure_full_verify_schedule",
            return_value=0,
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._select_conversations_for_sync",
            return_value=(
                selected,
                {
                    "discovered_conversations": len(selected),
                    "selected_conversations": len(selected),
                    "selection_strategy": "pending_bootstrap_round_robin",
                },
            ),
        ), mock.patch(
            "penguin_connect._retry_pending_imessage_to_gmail_globally",
            return_value={"retried_conversations": 0, "imported_messages": 0, "skipped_locked_conversations": 0},
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            side_effect=[
                {"imessage_imported": 5, "gmail_imported": 5, "bootstrap_ready": False, "preempted_for_incremental": True}
            ],
        ) as mock_imsg, mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage",
            return_value={"email_to_imessage": 0, "alias_drafts_deleted": 0, "blocked_sender_count": 0},
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ), mock.patch(
            "penguin_connect._record_gmail_sync_success"
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="startup_catchup", days=7, hours=5)

        self.assertTrue(stats["success"])
        self.assertTrue(stats["preempted_for_incremental"])
        self.assertEqual(stats["preempted_after_conversations"], 1)
        self.assertEqual(stats["processed_conversations"], 1)
        self.assertEqual(mock_imsg.call_count, 1)

    def test_startup_catchup_marks_due_full_verify_complete_after_success(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at,
                next_full_verify_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
                "2026-03-07T10:05:00+00:00",
            ),
        )

        imessage_calls = []
        gmail_calls = []

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 3, 8, 10, 5, tzinfo=tz or timezone.utc)

        def fake_imessage_sync(_conn, _gmail_service, conv, mode, days, hours=None, cutoff_iso=None, verify_all=False):
            imessage_calls.append((conv["conversation_id"], verify_all))
            return {"imessage_imported": 0, "gmail_imported": 0}

        def fake_gmail_sync(
            _conn,
            _gmail_service,
            conv,
            gmail_email,
            allowed_senders,
            days,
            hours=None,
            cutoff_iso=None,
            verify_all=False,
        ):
            gmail_calls.append((conv["conversation_id"], verify_all, gmail_email, tuple(allowed_senders)))
            return {"email_to_imessage": 0, "blocked_sender_count": 0}

        with mock.patch("penguin_connect.datetime", FixedDateTime), mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 1, "before_count": 1, "after_count": 1},
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            side_effect=fake_imessage_sync,
        ), mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage",
            side_effect=fake_gmail_sync,
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="startup_catchup", days=7)

        state = self.conn.execute(
            """SELECT next_full_verify_at, full_verify_completed_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        self.assertTrue(stats["success"])
        self.assertEqual(stats["selection_strategy"], "scheduled_full_verify_due")
        self.assertEqual(stats["full_verify_completed"], 1)
        self.assertEqual(imessage_calls, [("amc_test", True)])
        self.assertEqual(gmail_calls, [("amc_test", True, "owner@gmail.com", ("owner@gmail.com",))])
        self.assertTrue(state["next_full_verify_at"])
        self.assertTrue(state["full_verify_completed_at"])

    def test_startup_catchup_backfills_missing_full_verify_schedule_before_selection(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-08T10:00:00+00:00",
                "2026-03-08T10:00:00+00:00",
                "2026-03-08T10:05:00+00:00",
            ),
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 3, 8, 10, 5, tzinfo=tz or timezone.utc)

        with mock.patch("penguin_connect.datetime", FixedDateTime), mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 1, "before_count": 1, "after_count": 1},
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="startup_catchup", days=7)

        state = self.conn.execute(
            """SELECT next_full_verify_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            ("amc_test",),
        ).fetchone()

        self.assertTrue(stats["success"])
        self.assertEqual(stats["selection_strategy"], "startup_idle")
        self.assertEqual(stats["selected_conversations"], 0)
        self.assertEqual(stats["full_verify_schedule_backfilled"], 1)
        self.assertTrue(state["next_full_verify_at"])

    def test_backfill_sync_selects_recent_conversations_oldest_first(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_older",
                "chat-older",
                "Older Recent",
                "owner+am-older@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_stale",
                "chat-stale",
                "Stale",
                "owner+am-stale@gmail.com",
            ),
        )

        call_order = []

        def fake_imessage_sync(_conn, _gmail_service, conv, mode, days, hours=None, cutoff_iso=None, verify_all=False):
            call_order.append(("imessage", conv["conversation_id"], mode, hours, cutoff_iso))
            return {"imessage_imported": 0, "gmail_imported": 0}

        def fake_gmail_sync(
            _conn,
            _gmail_service,
            conv,
            gmail_email,
            allowed_senders,
            days,
            hours=None,
            cutoff_iso=None,
            verify_all=False,
        ):
            call_order.append(("gmail", conv["conversation_id"], gmail_email, tuple(allowed_senders), hours, cutoff_iso))
            return {"email_to_imessage": 0, "blocked_sender_count": 0}

        with mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 3, "before_count": 3, "after_count": 3},
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-older",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:55:00+00:00",
                        "message_count": 2,
                    },
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T08:20:00+00:00",
                        "last_message_at": "2026-03-07T08:50:00+00:00",
                        "message_count": 4,
                    },
                ],
            },
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            side_effect=fake_imessage_sync,
        ), mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage",
            side_effect=fake_gmail_sync,
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=0,
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="backfill", days=7, hours=5)

        self.assertTrue(stats["success"])
        self.assertEqual(stats["self_heal_sweep"]["swept_conversations"], 3)
        self.assertEqual(stats["discovered_conversations"], 3)
        self.assertEqual(stats["selected_conversations"], 2)
        self.assertEqual(stats["selection_strategy"], "recent_imessage_activity")
        self.assertEqual(stats["selection_cutoff"], call_order[0][4])
        self.assertEqual(stats["selection_cutoff"], call_order[1][5])
        self.assertEqual(
            [entry[:2] for entry in call_order],
            [
                ("imessage", "amc_older"),
                ("gmail", "amc_older"),
                ("imessage", "amc_test"),
                ("gmail", "amc_test"),
            ],
        )

    def test_backfill_verify_all_selects_all_active_conversations(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_second_verify",
                "chat-second-verify",
                "Second Verify",
                "owner+am-second-verify@gmail.com",
            ),
        )

        with mock.patch("penguin_connect.list_recent_imessage_chat_activity") as mock_recent:
            conversations, selection = penguin_connect._select_conversations_for_sync(
                self.conn,
                "owner@gmail.com",
                "backfill",
                days=7,
                hours=5,
                verify_all=True,
            )

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_second_verify", "amc_test"])
        self.assertEqual(selection["selected_conversations"], 2)
        self.assertEqual(selection["selection_strategy"], "verify_all_active")
        mock_recent.assert_not_called()

    def test_imessage_backfill_verify_all_starts_from_origin(self):
        conv = self._conversation_row()
        gmail_service = mock.Mock()

        older_msg = {
            "text": "older phone message",
            "timestamp": "2026-03-01T09:00:00+00:00",
            "is_from_me": False,
            "handle": "+14155550111",
            "attachments": None,
            "native_message_id": "old-1",
        }

        fetch_calls = []

        def fake_fetch(chat_id, limit=50, since=None):
            fetch_calls.append((chat_id, limit, since))
            if len(fetch_calls) == 1:
                return [older_msg]
            return []

        import_execute = gmail_service.users.return_value.messages.return_value.import_.return_value.execute
        import_execute.return_value = {"id": "gm-old-1", "threadId": "th-old-1"}

        with mock.patch("penguin_connect.fetch_imessage_messages", side_effect=fake_fetch), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="backfill",
                days=7,
                verify_all=True,
            )

        self.assertEqual(result["imessage_imported"], 1)
        self.assertEqual(fetch_calls[0][2], penguin_connect.FULL_IMESSAGE_SYNC_SINCE)

    def test_imessage_initial_bootstrap_starts_from_origin_even_with_partial_state(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-11T15:34:17.621095+00:00",
                "2026-03-11T15:34:17.621095+00:00",
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        fetch_calls = []

        def fake_fetch(chat_id, limit=50, since=None):
            fetch_calls.append((chat_id, limit, since))
            return []

        with mock.patch("penguin_connect.fetch_imessage_messages", side_effect=fake_fetch), mock.patch(
            "penguin_connect._get_imessage_unread_count", return_value=None
        ):
            result = penguin_connect._sync_conversation_imessage_to_gmail(
                self.conn,
                gmail_service,
                conv,
                mode="startup_catchup",
                days=7,
                cutoff_iso="2026-03-11T15:34:17.621095+00:00",
            )

        self.assertEqual(result["imessage_imported"], 0)
        self.assertEqual(fetch_calls[0][2], penguin_connect.FULL_IMESSAGE_SYNC_SINCE)

    def test_gmail_backfill_verify_all_starts_from_origin(self):
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        captured = {}

        def fake_list(_gmail_service, alias_email, after_iso):
            captured["alias_email"] = alias_email
            captured["after_iso"] = after_iso
            return []

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", side_effect=fake_list):
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                "owner@gmail.com",
                ["owner@gmail.com"],
                days=7,
                verify_all=True,
            )

        self.assertEqual(result["email_to_imessage"], 0)
        self.assertEqual(captured["alias_email"], "owner+am-test@gmail.com")
        self.assertEqual(captured["after_iso"], penguin_connect.FULL_GMAIL_SYNC_SINCE)

    def test_gmail_initial_bootstrap_starts_from_origin_even_with_partial_state(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, last_synced_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_test",
                "2026-03-11T15:34:17.621095+00:00",
                "2026-03-11T15:34:17.621095+00:00",
            ),
        )
        conv = self._conversation_row()
        gmail_service = mock.Mock()
        captured = {}

        def fake_list(_gmail_service, alias_email, after_iso):
            captured["alias_email"] = alias_email
            captured["after_iso"] = after_iso
            return []

        with mock.patch("penguin_connect._list_gmail_messages_to_alias", side_effect=fake_list):
            result = penguin_connect._sync_conversation_gmail_to_imessage(
                self.conn,
                gmail_service,
                conv,
                "owner@gmail.com",
                ["owner@gmail.com"],
                days=7,
                cutoff_iso="2026-03-11T15:34:17.621095+00:00",
            )

        self.assertEqual(result["email_to_imessage"], 0)
        self.assertEqual(captured["alias_email"], "owner+am-test@gmail.com")
        self.assertEqual(captured["after_iso"], penguin_connect.FULL_GMAIL_SYNC_SINCE)

    def test_sync_runtime_tracks_progress_and_continues_after_conversation_error(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_second",
                "chat-second",
                "Second Conversation",
                "owner+am-second@gmail.com",
            ),
        )

        class CommitTrackingConn:
            def __init__(self, conn):
                self._conn = conn
                self.commit_calls = 0

            def commit(self):
                self.commit_calls += 1
                return self._conn.commit()

            def rollback(self):
                return self._conn.rollback()

            def __getattr__(self, name):
                return getattr(self._conn, name)

        tracked_conn = CommitTrackingConn(self.conn)

        def fake_imessage_sync(_conn, _gmail_service, conv, mode, days, hours=None, cutoff_iso=None, verify_all=False):
            runtime = penguin_connect.get_runtime_sync_status()
            if conv["conversation_id"] == "amc_test":
                self.assertTrue(runtime["running"])
                self.assertEqual(runtime["processed_conversations"], 0)
                self.assertEqual(runtime["current_conversation_id"], "amc_test")
                raise RuntimeError("boom")

            self.assertTrue(runtime["running"])
            self.assertEqual(runtime["processed_conversations"], 1)
            self.assertEqual(runtime["current_conversation_id"], "amc_second")
            self.assertEqual(runtime["selection_strategy"], "recent_imessage_activity")
            self.assertEqual(runtime["selection_cutoff"], cutoff_iso)
            return {"imessage_imported": 2, "gmail_imported": 1}

        with mock.patch(
            "penguin_connect.self_heal_conversation_cache",
            return_value={"success": True, "swept_conversations": 2, "before_count": 2, "after_count": 2},
        ), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect.list_recent_imessage_chat_activity",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-123",
                        "first_message_at": "2026-03-07T07:10:00+00:00",
                        "last_message_at": "2026-03-07T07:15:00+00:00",
                        "message_count": 2,
                    },
                    {
                        "chat_id": "chat-second",
                        "first_message_at": "2026-03-07T07:20:00+00:00",
                        "last_message_at": "2026-03-07T07:25:00+00:00",
                        "message_count": 1,
                    },
                ],
            },
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            side_effect=fake_imessage_sync,
        ), mock.patch(
            "penguin_connect._sync_conversation_gmail_to_imessage",
            return_value={"email_to_imessage": 3, "blocked_sender_count": 0},
        ), mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            return_value=1,
        ):
            stats = penguin_connect._sync_conversations_unlocked(tracked_conn, mode="backfill", days=7, hours=5)

        runtime = penguin_connect.get_runtime_sync_status()
        self.assertTrue(stats["success"])
        self.assertEqual(stats["failed_conversations"], 1)
        self.assertEqual(stats["conversation_errors"][0]["conversation_id"], "amc_test")
        self.assertEqual(stats["imessage_imported"], 2)
        self.assertEqual(stats["gmail_imported"], 1)
        self.assertEqual(stats["email_to_imessage"], 3)
        self.assertEqual(stats["gmail_thread_repairs"], 1)
        self.assertEqual(tracked_conn.commit_calls, 4)
        self.assertFalse(runtime["running"])
        self.assertEqual(runtime["processed_conversations"], 2)
        self.assertEqual(runtime["selected_conversations"], 2)
        self.assertEqual(runtime["current_conversation_id"], None)
        self.assertEqual(runtime["last_result"]["failed_conversations"], 1)
        self.assertIsNotNone(runtime["last_completed_at"])

    def test_successful_gmail_send_row_survives_later_conversation_failure(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"durable send").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-durable-1",
            "threadId": "thread-durable-1",
            "historyId": "h-durable-1",
            "labelIds": ["SENT", "INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "durable send",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
                    {"name": "To", "value": "Owner <owner+am-test@gmail.com>"},
                    {"name": "Subject", "value": "Durable"},
                    {"name": "Message-ID", "value": "<durable-1@example.test>"},
                ],
                "body": {"data": payload_data},
            },
        }
        gmail_service = mock.Mock()
        gmail_service.users.return_value.messages.return_value.get.return_value.execute.return_value = full_msg

        with mock.patch(
            "penguin_connect.ensure_conversations_discovered",
            return_value=0,
        ), mock.patch(
            "penguin_connect._build_gmail_service",
            return_value=(gmail_service, None),
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._select_conversations_for_sync",
            return_value=(
                [conv],
                {
                    "discovered_conversations": 1,
                    "selected_conversations": 1,
                    "selection_strategy": "test_selection",
                },
            ),
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            return_value={"imessage_imported": 0, "gmail_imported": 0},
        ), mock.patch(
            "penguin_connect._list_gmail_messages_to_alias",
            return_value=[{"id": "gmail-durable-1"}],
        ), mock.patch(
            "penguin_connect.send_imessage",
            return_value=(True, None),
        ) as mock_send, mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            side_effect=RuntimeError("after_send_failure"),
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        self.assertTrue(stats["success"])
        self.assertEqual(stats["failed_conversations"], 1)
        self.assertEqual(stats["conversation_errors"][0]["conversation_id"], "amc_test")
        mock_send.assert_called_once()
        row = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:gmail-durable-1"),
        ).fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(row["body_text"], "durable send")
        self.assertEqual(metadata["delivery_status"], "delivered")
        self.assertEqual(metadata["send_result"], "imessage_ok")

    def test_successful_gmail_retry_row_survives_later_conversation_failure(self):
        conv = self._conversation_row()
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, gmail_message_id, gmail_thread_id, direction,
                sender_email, subject, body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'gmail', 'gmail:retry-durable-1', 'retry-durable-1', 'thread-retry-durable-1',
                       'email_to_imessage', ?, ?, ?, ?, 1, ?)""",
            (
                "amc_test",
                "owner@gmail.com",
                "Retry Durable",
                "retry durable send",
                "2026-03-10T10:00:00+00:00",
                json.dumps(
                    {
                        "delivery_status": "pending",
                        "retry_count": 1,
                        "max_retries": 3,
                        "gmail_message_id": "retry-durable-1",
                        "gmail_thread_id": "thread-retry-durable-1",
                    }
                ),
            ),
        )
        self.conn.commit()
        gmail_service = mock.Mock()

        with mock.patch(
            "penguin_connect.ensure_conversations_discovered",
            return_value=0,
        ), mock.patch(
            "penguin_connect._build_gmail_service",
            return_value=(gmail_service, None),
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._select_conversations_for_sync",
            return_value=(
                [conv],
                {
                    "discovered_conversations": 1,
                    "selected_conversations": 1,
                    "selection_strategy": "test_selection",
                },
            ),
        ), mock.patch(
            "penguin_connect._sync_conversation_imessage_to_gmail",
            return_value={"imessage_imported": 0, "gmail_imported": 0},
        ), mock.patch(
            "penguin_connect._list_gmail_messages_to_alias",
            return_value=[],
        ), mock.patch(
            "penguin_connect.send_imessage",
            return_value=(True, None),
        ) as mock_send, mock.patch(
            "penguin_connect._repair_split_gmail_messages",
            side_effect=RuntimeError("after_retry_failure"),
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        self.assertTrue(stats["success"])
        self.assertEqual(stats["failed_conversations"], 1)
        self.assertEqual(stats["conversation_errors"][0]["conversation_id"], "amc_test")
        mock_send.assert_called_once()
        row = self.conn.execute(
            """SELECT body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_test", "gmail:retry-durable-1"),
        ).fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(row["body_text"], "retry durable send")
        self.assertEqual(metadata["delivery_status"], "delivered")
        self.assertEqual(metadata["send_result"], "imessage_ok")

    def test_sync_unlocked_globally_retries_pending_imessage_imports_without_selection(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'group', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_pending",
                "chat-pending",
                "Pending Group",
                "owner+am-pending@gmail.com",
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                body_text, message_timestamp, is_read, metadata)
               VALUES (?, 'imessage', 'im-pending-1', 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
            (
                "amc_pending",
                "owner+am-pending@gmail.com",
                "Tester",
                "iMessage · Pending Group",
                "pending retry body",
                "2026-03-10T10:00:00+00:00",
                json.dumps({"delivery_status": "pending", "retry_count": 0, "max_retries": 3}),
            ),
        )
        self.conn.commit()
        gmail_service = mock.Mock()

        with mock.patch(
            "penguin_connect.ensure_conversations_discovered",
            return_value=0,
        ), mock.patch(
            "penguin_connect._build_gmail_service",
            return_value=(gmail_service, None),
        ), mock.patch(
            "penguin_connect._refresh_send_as_aliases",
            return_value=(["owner@gmail.com"], "owner@gmail.com"),
        ), mock.patch(
            "penguin_connect._select_conversations_for_sync",
            return_value=(
                [],
                {
                    "discovered_conversations": 2,
                    "selected_conversations": 0,
                    "selection_strategy": "test_selection",
                },
            ),
        ), mock.patch(
            "penguin_connect._import_message_to_gmail_with_thread_recovery",
            return_value=({"id": "gm-retried-1", "threadId": "th-retried-1"}, None, "th-retried-1"),
        ), mock.patch(
            "penguin_connect._sleep_after_gmail_write",
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        self.assertTrue(stats["success"])
        self.assertEqual(stats["selected_conversations"], 0)
        self.assertEqual(stats["global_imessage_retry_conversations"], 1)
        self.assertEqual(stats["global_imessage_retry_imported"], 1)
        self.assertEqual(stats["gmail_imported"], 1)

        row = self.conn.execute(
            """SELECT gmail_message_id, gmail_thread_id, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND provider_message_id = ?""",
            ("amc_pending", "im-pending-1"),
        ).fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(row["gmail_message_id"], "gm-retried-1")
        self.assertEqual(row["gmail_thread_id"], "th-retried-1")
        self.assertEqual(metadata["delivery_status"], "delivered")

        conv = self.conn.execute(
            "SELECT gmail_thread_id, last_synced_at FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_pending",),
        ).fetchone()
        self.assertEqual(conv["gmail_thread_id"], "th-retried-1")
        self.assertIsNotNone(conv["last_synced_at"])

    def test_global_imessage_retry_rate_limit_sets_shared_pause(self):
        gmail_service = mock.Mock()

        with mock.patch(
            "penguin_connect.ensure_conversations_discovered",
            return_value=1,
        ), mock.patch(
            "penguin_connect._build_gmail_service",
            return_value=(gmail_service, None),
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
        ), mock.patch(
            "penguin_connect._retry_pending_imessage_to_gmail_globally",
            side_effect=penguin_connect._GmailRetryableError(480, 429, "userRateLimitExceeded"),
        ):
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        self.assertTrue(stats["success"])
        self.assertTrue(stats["skipped"])
        self.assertEqual(stats["reason"], "gmail_rate_limited")
        self.assertEqual(stats["retry_after_seconds"], 480)
        poll_state = self.conn.execute(
            "SELECT gmail_rate_limited_until, gmail_rate_limit_streak FROM penguin_connect_poll_state WHERE gmail_email = ?",
            ("owner@gmail.com",),
        ).fetchone()
        self.assertIsNotNone(poll_state)
        self.assertIsNotNone(poll_state["gmail_rate_limited_until"])
        self.assertEqual(poll_state["gmail_rate_limit_streak"], 1)

    def test_fetch_imessage_messages_applies_limit_with_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "chat.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(
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
                    text TEXT,
                    date INTEGER,
                    is_from_me INTEGER,
                    service TEXT,
                    handle_id INTEGER,
                    attributedBody BLOB
                );
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
                CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
                CREATE TABLE attachment (
                    ROWID INTEGER PRIMARY KEY,
                    filename TEXT,
                    mime_type TEXT,
                    total_bytes INTEGER,
                    transfer_name TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name) VALUES (1, ?, ?, '', 'iMessage')",
                ("iMessage;-;chat-1", "chat-1"),
            )

            base_ns = int((timedelta(days=2).total_seconds()) * 1_000_000_000)
            for i in range(10):
                rowid = i + 1
                conn.execute(
                    "INSERT INTO message(ROWID, text, date, is_from_me, service, handle_id, attributedBody) VALUES (?, ?, ?, 0, 'iMessage', NULL, NULL)",
                    (rowid, f"m{rowid}", base_ns + (rowid * 1_000_000_000)),
                )
                conn.execute(
                    "INSERT INTO chat_message_join(chat_id, message_id) VALUES (1, ?)",
                    (rowid,),
                )
            conn.commit()
            conn.close()

            old_path = browse_sources.IMESSAGE_DB
            browse_sources.IMESSAGE_DB = db_path
            try:
                since = (browse_sources.APPLE_EPOCH + timedelta(days=1)).isoformat()
                first_batch = browse_sources.fetch_imessage_messages("iMessage;-;chat-1", limit=3, since=since)
                second_since = max(r["timestamp"] for r in first_batch)
                second_batch = browse_sources.fetch_imessage_messages("iMessage;-;chat-1", limit=3, since=second_since)
            finally:
                browse_sources.IMESSAGE_DB = old_path

        self.assertEqual([r["text"] for r in first_batch], ["m1", "m2", "m3"])
        self.assertEqual([r["text"] for r in second_batch], ["m4", "m5", "m6"])

    def test_fetch_imessage_messages_fails_closed_for_ambiguous_identifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "chat.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(
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
                    text TEXT,
                    date INTEGER,
                    is_from_me INTEGER,
                    service TEXT,
                    handle_id INTEGER,
                    attributedBody BLOB
                );
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
                CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
                CREATE TABLE attachment (
                    ROWID INTEGER PRIMARY KEY,
                    filename TEXT,
                    mime_type TEXT,
                    total_bytes INTEGER,
                    transfer_name TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name) VALUES (1, ?, ?, '', 'iMessage')",
                ("iMessage;-;chat-shared", "chat-shared"),
            )
            conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, display_name, service_name) VALUES (2, ?, ?, '', 'SMS')",
                ("SMS;-;chat-shared", "chat-shared"),
            )
            conn.execute(
                "INSERT INTO message(ROWID, text, date, is_from_me, service, handle_id, attributedBody) VALUES (1, 'hello', 1000, 0, 'SMS', NULL, NULL)"
            )
            conn.execute("INSERT INTO chat_message_join(chat_id, message_id) VALUES (2, 1)")
            conn.commit()
            conn.close()

            old_path = browse_sources.IMESSAGE_DB
            browse_sources.IMESSAGE_DB = db_path
            try:
                ambiguous = browse_sources.fetch_imessage_messages("chat-shared", limit=10)
                exact = browse_sources.fetch_imessage_messages("SMS;-;chat-shared", limit=10)
            finally:
                browse_sources.IMESSAGE_DB = old_path

        self.assertEqual(ambiguous, [])
        self.assertEqual([row["text"] for row in exact], ["hello"])

    def test_list_recent_imessage_chat_activity_orders_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "chat.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE chat (
                    ROWID INTEGER PRIMARY KEY,
                    guid TEXT,
                    chat_identifier TEXT,
                    service_name TEXT,
                    is_archived INTEGER DEFAULT 0
                );
                CREATE TABLE message (
                    ROWID INTEGER PRIMARY KEY,
                    text TEXT,
                    date INTEGER,
                    is_from_me INTEGER,
                    service TEXT,
                    handle_id INTEGER,
                    attributedBody BLOB
                );
                CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
                CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
                CREATE TABLE attachment (
                    ROWID INTEGER PRIMARY KEY,
                    filename TEXT,
                    mime_type TEXT,
                    total_bytes INTEGER,
                    transfer_name TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, service_name) VALUES (1, ?, ?, 'iMessage')",
                ("iMessage;-;chat-early", "chat-early"),
            )
            conn.execute(
                "INSERT INTO chat(ROWID, guid, chat_identifier, service_name) VALUES (2, ?, ?, 'SMS')",
                ("SMS;-;chat-late", "chat-late"),
            )

            base_ns = int((timedelta(days=2).total_seconds()) * 1_000_000_000)
            rows = [
                (1, "early-1", base_ns + 10_000_000_000, 1),
                (2, "late-1", base_ns + 40_000_000_000, 2),
                (3, "early-2", base_ns + 20_000_000_000, 1),
            ]
            for rowid, text, date_ns, chat_rowid in rows:
                conn.execute(
                    "INSERT INTO message(ROWID, text, date, is_from_me, service, handle_id, attributedBody) VALUES (?, ?, ?, 0, 'iMessage', NULL, NULL)",
                    (rowid, text, date_ns),
                )
                conn.execute(
                    "INSERT INTO chat_message_join(chat_id, message_id) VALUES (?, ?)",
                    (chat_rowid, rowid),
                )
            conn.commit()
            conn.close()

            old_path = browse_sources.IMESSAGE_DB
            browse_sources.IMESSAGE_DB = db_path
            try:
                since = (browse_sources.APPLE_EPOCH + timedelta(days=1)).isoformat()
                recent = browse_sources.list_recent_imessage_chat_activity(since, limit=10)
            finally:
                browse_sources.IMESSAGE_DB = old_path

        self.assertTrue(recent["available"])
        self.assertEqual([row["chat_id"] for row in recent["chats"]], ["iMessage;-;chat-early", "SMS;-;chat-late"])
        self.assertEqual(recent["chats"][0]["message_count"], 2)


if __name__ == "__main__":
    unittest.main()
