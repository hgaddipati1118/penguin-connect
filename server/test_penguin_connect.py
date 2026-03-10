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
        self.conn.close()

    def _conversation_row(self):
        return self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_test",),
        ).fetchone()

    def test_conversation_id_is_deterministic(self):
        one = penguin_connect.deterministic_conversation_id("Owner@Gmail.com", "chat-A")
        two = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-A")
        three = penguin_connect.deterministic_conversation_id("owner@gmail.com", "chat-B")
        self.assertEqual(one, two)
        self.assertNotEqual(one, three)

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
        penguin_connect._upsert_sync_state(self.conn, cid, newer, newer, "123")
        penguin_connect._upsert_sync_state(self.conn, cid, older, older, None)

        state = self.conn.execute(
            "SELECT last_imessage_ts, last_gmail_ts, last_message_ts, last_gmail_history_id FROM penguin_connect_sync_state WHERE conversation_id = ?",
            (cid,),
        ).fetchone()
        self.assertEqual(state["last_imessage_ts"], newer)
        self.assertEqual(state["last_gmail_ts"], newer)
        self.assertEqual(state["last_message_ts"], newer)
        self.assertEqual(state["last_gmail_history_id"], "123")

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

    def test_resolve_display_name_uses_contact_when_chat_name_is_raw_handle(self):
        self.conn.execute(
            """INSERT INTO contacts (first_name, last_name, organization, phone, phone_normalized, email, source_db)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Kam", "(Shine Capital)", "", "+17144741613", "+17144741613", None, "test"),
        )

        name = penguin_connect._resolve_display_name(self.conn, "+17144741613", ["+17144741613"])

        self.assertEqual(name, "Kam (Shine Capital)")

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

        self.assertEqual([row["conversation_id"] for row in conversations], ["amc_test"])
        self.assertEqual(selection["discovered_conversations"], 2)
        self.assertEqual(selection["selected_conversations"], 1)
        self.assertEqual(selection["bootstrapped_conversations"], 1)
        self.assertEqual(selection["pending_bootstrap_conversations"], 1)
        self.assertEqual(selection["selection_strategy"], "pending_bootstrap_recent_imessage_activity")
        self.assertTrue(selection["selection_cutoff"])

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

    def test_list_conversations_uses_cache_without_discovery(self):
        with mock.patch("penguin_connect.ensure_conversations_discovered") as mock_discover:
            result = penguin_connect.list_conversations(self.conn)

        mock_discover.assert_not_called()
        self.assertTrue(result["connected"])
        self.assertEqual(len(result["conversations"]), 1)

    def test_list_conversations_discovers_when_cache_empty(self):
        self.conn.execute("DELETE FROM penguin_connect_conversations WHERE gmail_email = ?", ("owner@gmail.com",))
        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=0) as mock_discover:
            result = penguin_connect.list_conversations(self.conn)

        mock_discover.assert_called_once_with(self.conn, "owner@gmail.com")
        self.assertTrue(result["connected"])
        self.assertEqual(result["conversations"], [])

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
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "hello from gmail",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
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
        self.assertEqual(metadata["send_result"], "imessage_ok")
        self.assertEqual(metadata["retry_count"], 1)

    def test_gmail_sync_persists_rfc_thread_headers(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"hello from gmail").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-rfc-1",
            "threadId": "thread-rfc-1",
            "historyId": "h-rfc-1",
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "hello from gmail",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
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
            "SELECT metadata FROM penguin_connect_messages WHERE conversation_id = ? AND provider_message_id = ?",
            ("amc_test", "gmail:gmail-rfc-1"),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        self.assertEqual(metadata.get("rfc_message_id"), "<mail-1@example.test>")
        self.assertEqual(metadata.get("rfc_in_reply_to"), "<mail-0@example.test>")
        self.assertEqual(
            metadata.get("rfc_references"),
            ["<mail-root@example.test>", "<mail-0@example.test>"],
        )

    def test_gmail_attachments_forward_binary_to_imessage(self):
        conv = self._conversation_row()
        payload_data = base64.urlsafe_b64encode(b"see attachment").decode("utf-8").rstrip("=")
        attachment_data = base64.urlsafe_b64encode(b"\x89PNG\r\n\x1a\nimg").decode("utf-8").rstrip("=")
        full_msg = {
            "id": "gmail-attach-bin-1",
            "threadId": "thread-attach-bin-1",
            "historyId": "h-attach-bin-1",
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
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
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1700000000000",
            "snippet": "",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
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
                json.dumps({"rfc_message_id": "<root@example.test>"}),
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
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1700003600000",
            "snippet": "split thread reply",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
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
                json.dumps({"rfc_message_id": root_message_id}),
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
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1700003600000",
            "snippet": "split thread reply",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
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
            response = full_msg if fmt == "full" else {"id": "gmail-split-safe", "raw": original_raw, "labelIds": ["INBOX", "UNREAD"]}
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

    def test_gmail_nested_reply_adds_quoted_context_to_imessage(self):
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
                json.dumps({"rfc_message_id": "<root@example.test>"}),
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
        full_msg = {
            "id": "gmail-leaf",
            "threadId": "thread-main",
            "historyId": "h-leaf",
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1700007200000",
            "snippet": "Latest nested reply",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Owner <owner@gmail.com>"},
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
        self.assertIn("Quoted context:", sent_text)
        self.assertIn("> Alice: Root context", sent_text)
        self.assertIn("> Bridge: Bridge context", sent_text)

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

    def test_sync_conversations_is_single_flight(self):
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
            t1 = threading.Thread(target=lambda: results.append(penguin_connect.sync_conversations(None)))
            t2 = threading.Thread(target=lambda: results.append(penguin_connect.sync_conversations(None)))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(peak, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["success"] for r in results))

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
               (conversation_id, last_imessage_ts, last_gmail_ts, initial_sync_completed_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                "amc_ready",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:00:00+00:00",
                "2026-03-04T10:05:00+00:00",
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
               (gmail_email, gmail_rate_limited_until)
               VALUES (?, ?)""",
            ("owner@gmail.com", future_pause),
        )

        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=1), mock.patch(
            "penguin_connect._build_gmail_service", return_value=(mock.Mock(), None)
        ), mock.patch("penguin_connect._refresh_send_as_aliases") as mock_refresh:
            stats = penguin_connect._sync_conversations_unlocked(self.conn, mode="incremental", days=7)

        self.assertTrue(stats["success"])
        self.assertTrue(stats["skipped"])
        self.assertEqual(stats["reason"], "gmail_rate_limited")
        self.assertGreaterEqual(stats["retry_after_seconds"], 1)
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

    def test_backfill_marks_conversation_bootstrapped_after_success(self):
        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=1), mock.patch(
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
            return_value={"imessage_imported": 0, "gmail_imported": 0},
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
        self.assertIsNotNone(state)
        self.assertTrue(state["initial_sync_completed_at"])

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

        with mock.patch("penguin_connect.ensure_conversations_discovered", return_value=3), mock.patch(
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
        self.assertEqual(tracked_conn.commit_calls, 1)
        self.assertFalse(runtime["running"])
        self.assertEqual(runtime["processed_conversations"], 2)
        self.assertEqual(runtime["selected_conversations"], 2)
        self.assertEqual(runtime["current_conversation_id"], None)
        self.assertEqual(runtime["last_result"]["failed_conversations"], 1)
        self.assertIsNotNone(runtime["last_completed_at"])

    def test_fetch_imessage_messages_applies_limit_with_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "chat.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT);
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
            conn.execute("INSERT INTO chat(ROWID, chat_identifier) VALUES (1, ?)", ("chat-1",))

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
                first_batch = browse_sources.fetch_imessage_messages("chat-1", limit=3, since=since)
                second_since = max(r["timestamp"] for r in first_batch)
                second_batch = browse_sources.fetch_imessage_messages("chat-1", limit=3, since=second_since)
            finally:
                browse_sources.IMESSAGE_DB = old_path

        self.assertEqual([r["text"] for r in first_batch], ["m1", "m2", "m3"])
        self.assertEqual([r["text"] for r in second_batch], ["m4", "m5", "m6"])

    def test_list_recent_imessage_chat_activity_orders_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "chat.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT);
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
            conn.execute("INSERT INTO chat(ROWID, chat_identifier) VALUES (1, ?)", ("chat-early",))
            conn.execute("INSERT INTO chat(ROWID, chat_identifier) VALUES (2, ?)", ("chat-late",))

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
        self.assertEqual([row["chat_id"] for row in recent["chats"]], ["chat-early", "chat-late"])
        self.assertEqual(recent["chats"][0]["message_count"], 2)


if __name__ == "__main__":
    unittest.main()
