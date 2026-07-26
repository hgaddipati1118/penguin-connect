"""Integration tests for Telegram channel in the PenguinConnect bridge."""

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest import mock

import penguin_connect
from db import SCHEMA


class TelegramBridgeIntegrationTests(unittest.TestCase):
    """Tests that verify Telegram wiring in the main bridge logic."""

    def setUp(self):
        with penguin_connect._sync_runtime_lock:
            penguin_connect._sync_runtime = penguin_connect._new_sync_runtime_state()
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_SIGNATURE_MARKERS_FILE": str(Path(self.tmpdir.name) / "missing.json"),
                "PENGUIN_CONNECT_EXCLUDED_CHATS_FILE": str(Path(self.tmpdir.name) / "missing.json"),
            },
            clear=False,
        )
        self.env_patcher.start()
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
            ("owner@gmail.com", "penguinconnect-test", '["owner@gmail.com"]'),
        )

    def tearDown(self):
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.send_imessage_patcher.stop()
        self.conn.close()
        self.env_patcher.stop()
        self.tmpdir.cleanup()

    def _insert_telegram_conversation(self, chat_id="12345", display_name="Alice Smith", chat_type="dm"):
        conversation_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", chat_id, "telegram")
        alias_email = f"owner+am-{conversation_id[:8]}@gmail.com"
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, source_chat_id,
                display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'telegram', ?, ?, ?, ?, '[]', ?, 'active')""",
            ("owner@gmail.com", conversation_id, chat_id, display_name, chat_type, alias_email),
        )
        self.conn.commit()
        return conversation_id

    def _conversation_row(self, conversation_id):
        return self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

    # ------------------------------------------------------------------
    # Conversation identity
    # ------------------------------------------------------------------
    def test_deterministic_conversation_id_for_telegram(self):
        cid1 = penguin_connect.deterministic_conversation_id("owner@gmail.com", "12345", "telegram")
        cid2 = penguin_connect.deterministic_conversation_id("owner@gmail.com", "12345", "telegram")
        cid_imessage = penguin_connect.deterministic_conversation_id("owner@gmail.com", "12345", "imessage")
        self.assertEqual(cid1, cid2)
        self.assertNotEqual(cid1, cid_imessage)
        self.assertTrue(cid1.startswith("amc_"))

    # ------------------------------------------------------------------
    # Provider message ID
    # ------------------------------------------------------------------
    def test_provider_message_id_telegram_uses_native_id(self):
        msg = {"native_message_id": "42", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hi"}
        result = penguin_connect._provider_message_id("telegram", msg)
        self.assertEqual(result, "telegram:42")

    def test_provider_message_id_telegram_hash_fallback(self):
        msg = {"native_message_id": "", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hi"}
        result = penguin_connect._provider_message_id("telegram", msg)
        self.assertTrue(result.startswith("telegram:"))
        self.assertGreater(len(result), len("telegram:"))

    def test_provider_message_id_telegram_is_deterministic(self):
        msg = {"native_message_id": "", "timestamp": "2026-03-15T09:00:00", "is_from_me": True, "text": "test"}
        r1 = penguin_connect._provider_message_id("telegram", msg)
        r2 = penguin_connect._provider_message_id("telegram", msg)
        self.assertEqual(r1, r2)

    # ------------------------------------------------------------------
    # Source provider normalization
    # ------------------------------------------------------------------
    def test_normalize_source_provider_telegram(self):
        self.assertEqual(penguin_connect._normalize_source_provider("telegram"), "telegram")

    def test_source_provider_label(self):
        self.assertEqual(penguin_connect._source_provider_label("telegram"), "Telegram")

    # ------------------------------------------------------------------
    # Provider subject line
    # ------------------------------------------------------------------
    def test_provider_subject_telegram(self):
        self.assertEqual(
            penguin_connect._provider_subject("telegram", "Alice Smith"),
            "Telegram · Alice Smith",
        )

    def test_provider_subject_telegram_empty_display_name(self):
        self.assertEqual(
            penguin_connect._provider_subject("telegram", ""),
            "Telegram · Conversation",
        )

    # ------------------------------------------------------------------
    # Email import with Telegram provider
    # ------------------------------------------------------------------
    def test_build_import_email_sets_telegram_provider_header(self):
        raw = penguin_connect._build_import_email(
            "amc_test",
            "owner+am-test@gmail.com",
            "owner@gmail.com",
            "Alice Smith",
            {"text": "hello from telegram", "timestamp": "2026-03-15T09:00:00+00:00", "attachments": []},
            source_provider="telegram",
        )
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(parsed["Subject"], "Telegram · Alice Smith")
        self.assertIn(parsed["X-PenguinConnect-Source-Provider"].lower(), ("telegram",))

    def test_build_import_email_telegram_group(self):
        raw = penguin_connect._build_import_email(
            "amc_test",
            "owner+am-test@gmail.com",
            "owner@gmail.com",
            "Family Group",
            {"text": "group message", "timestamp": "2026-03-15T09:00:00+00:00", "attachments": []},
            source_provider="telegram",
        )
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(parsed["Subject"], "Telegram · Family Group")

    # ------------------------------------------------------------------
    # Send dispatch
    # ------------------------------------------------------------------
    def test_send_to_source_dispatches_to_telegram_adapter(self):
        conv_id = self._insert_telegram_conversation()
        conv = self._conversation_row(conv_id)

        mock_adapter = mock.Mock()
        mock_adapter.send_message.return_value = (True, None)

        with mock.patch("penguin_connect._source_adapter_for_provider", return_value=mock_adapter):
            with mock.patch("penguin_connect.log_action"):
                ok, error = penguin_connect._send_to_source_conversation(conv, "Hello from Gmail!")

        self.assertTrue(ok)
        self.assertIsNone(error)
        mock_adapter.send_message.assert_called_once()
        call_args = mock_adapter.send_message.call_args
        self.assertIn("Hello from Gmail!", str(call_args))

    def test_send_to_source_telegram_failure_returns_error(self):
        conv_id = self._insert_telegram_conversation()
        conv = self._conversation_row(conv_id)

        mock_adapter = mock.Mock()
        mock_adapter.send_message.return_value = (False, "telegram_not_authenticated")

        with mock.patch("penguin_connect._source_adapter_for_provider", return_value=mock_adapter):
            with mock.patch("penguin_connect.log_action"):
                ok, error = penguin_connect._send_to_source_conversation(conv, "test")

        self.assertFalse(ok)
        self.assertEqual(error, "telegram_not_authenticated")

    # ------------------------------------------------------------------
    # Fetch dispatch
    # ------------------------------------------------------------------
    def test_fetch_source_messages_dispatches_to_telegram(self):
        conv_id = self._insert_telegram_conversation()
        conv = self._conversation_row(conv_id)

        mock_messages = [
            {"text": "hello", "timestamp": "2026-03-15T09:00:00+00:00", "is_from_me": False,
             "handle": "12345", "push_name": "Alice", "native_message_id": "1"},
        ]
        with mock.patch.object(penguin_connect, "_TELEGRAM_CHANNEL") as mock_channel:
            mock_channel.fetch_messages.return_value = mock_messages
            result = penguin_connect._fetch_source_messages_for_conversation(
                conv, limit=50, since=None,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "hello")

    def test_fetch_source_messages_telegram_returns_empty_when_no_channel(self):
        conv_id = self._insert_telegram_conversation()
        conv = self._conversation_row(conv_id)

        with mock.patch.object(penguin_connect, "_TELEGRAM_CHANNEL", None):
            result = penguin_connect._fetch_source_messages_for_conversation(
                conv, limit=50, since=None,
            )
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # Activity keys
    # ------------------------------------------------------------------
    def test_activity_keys_telegram_returns_chat_id_only(self):
        conv_id = self._insert_telegram_conversation()
        conv = self._conversation_row(conv_id)
        keys = penguin_connect._conversation_imessage_activity_keys(conv)
        self.assertEqual(keys, {"12345"})

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def test_ensure_telegram_conversations_discovered(self):
        mock_chats = {
            "available": True,
            "chats": [
                {
                    "chat_id": "99999",
                    "name": "Bob",
                    "chat_type": "dm",
                    "participants": ["Bob"],
                },
                {
                    "chat_id": "88888",
                    "name": "Dev Group",
                    "chat_type": "group",
                    "participants": [],
                },
            ],
        }
        mock_channel = mock.Mock()
        mock_channel.list_conversations.return_value = mock_chats

        with mock.patch.object(penguin_connect, "_TELEGRAM_CHANNEL", mock_channel):
            with mock.patch("penguin_connect.log_action"):
                count = penguin_connect.ensure_telegram_conversations_discovered(
                    self.conn, "owner@gmail.com"
                )

        self.assertEqual(count, 2)
        rows = self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE source_provider = 'telegram'"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        providers = {r["source_provider"] for r in rows}
        self.assertEqual(providers, {"telegram"})
        names = {r["display_name"] for r in rows}
        self.assertIn("Bob", names)
        self.assertIn("Dev Group", names)

    def test_ensure_telegram_conversations_discovered_skips_when_no_channel(self):
        with mock.patch.object(penguin_connect, "_TELEGRAM_CHANNEL", None):
            count = penguin_connect.ensure_telegram_conversations_discovered(
                self.conn, "owner@gmail.com"
            )
        self.assertEqual(count, 0)

    def test_ensure_telegram_conversations_discovered_handles_unavailable(self):
        mock_channel = mock.Mock()
        mock_channel.list_conversations.return_value = {"available": False, "reason": "not authenticated"}

        with mock.patch.object(penguin_connect, "_TELEGRAM_CHANNEL", mock_channel):
            with mock.patch("penguin_connect.log_action"):
                count = penguin_connect.ensure_telegram_conversations_discovered(
                    self.conn, "owner@gmail.com"
                )
        self.assertEqual(count, 0)

    # ------------------------------------------------------------------
    # Unread count dispatch
    # ------------------------------------------------------------------
    def test_unread_count_skipped_for_telegram(self):
        """Telegram conversations should not call the Apple Messages unread count function."""
        conv_id = self._insert_telegram_conversation()
        conv = self._conversation_row(conv_id)
        source_provider = penguin_connect._conversation_source_provider(conv)
        # Verify the condition used in the sync loop
        self.assertNotEqual(source_provider, "imessage")


class WhatsAppBridgeIntegrationTests(unittest.TestCase):
    """Tests that verify WhatsApp wiring in the main bridge logic."""

    def setUp(self):
        with penguin_connect._sync_runtime_lock:
            penguin_connect._sync_runtime = penguin_connect._new_sync_runtime_state()
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_SIGNATURE_MARKERS_FILE": str(Path(self.tmpdir.name) / "missing.json"),
                "PENGUIN_CONNECT_EXCLUDED_CHATS_FILE": str(Path(self.tmpdir.name) / "missing.json"),
            },
            clear=False,
        )
        self.env_patcher.start()
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
            ("owner@gmail.com", "penguinconnect-test", '["owner@gmail.com"]'),
        )

    def tearDown(self):
        with penguin_connect._conversation_sync_state_lock:
            penguin_connect._active_conversation_syncs.clear()
        self.send_imessage_patcher.stop()
        self.conn.close()
        self.env_patcher.stop()
        self.tmpdir.cleanup()

    def _insert_whatsapp_conversation(self, chat_id="14155551234@s.whatsapp.net", display_name="Alice", chat_type="dm"):
        conversation_id = penguin_connect.deterministic_conversation_id("owner@gmail.com", chat_id, "whatsapp")
        alias_email = f"owner+am-{conversation_id[:8]}@gmail.com"
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, source_chat_id,
                display_name, chat_type, participants, alias_email, status)
               VALUES (?, 'whatsapp', ?, ?, ?, ?, '[]', ?, 'active')""",
            ("owner@gmail.com", conversation_id, chat_id, display_name, chat_type, alias_email),
        )
        self.conn.commit()
        return conversation_id

    def _conversation_row(self, conversation_id):
        return self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

    # ------------------------------------------------------------------
    # Send dispatch
    # ------------------------------------------------------------------
    def test_send_to_source_dispatches_to_whatsapp_adapter(self):
        conv_id = self._insert_whatsapp_conversation()
        conv = self._conversation_row(conv_id)

        mock_adapter = mock.Mock()
        mock_adapter.send_message.return_value = (True, None)

        with mock.patch("penguin_connect._source_adapter_for_provider", return_value=mock_adapter):
            with mock.patch("penguin_connect.log_action"):
                ok, error = penguin_connect._send_to_source_conversation(conv, "Reply from Gmail!")

        self.assertTrue(ok)
        mock_adapter.send_message.assert_called_once()

    def test_send_to_source_whatsapp_failure(self):
        conv_id = self._insert_whatsapp_conversation()
        conv = self._conversation_row(conv_id)

        mock_adapter = mock.Mock()
        mock_adapter.send_message.return_value = (False, "whatsapp_send_failed: timeout")

        with mock.patch("penguin_connect._source_adapter_for_provider", return_value=mock_adapter):
            with mock.patch("penguin_connect.log_action"):
                ok, error = penguin_connect._send_to_source_conversation(conv, "test")

        self.assertFalse(ok)
        self.assertIn("whatsapp_send_failed", error)

    # ------------------------------------------------------------------
    # Fetch dispatch
    # ------------------------------------------------------------------
    def test_fetch_source_messages_dispatches_to_whatsapp(self):
        conv_id = self._insert_whatsapp_conversation()
        conv = self._conversation_row(conv_id)

        mock_messages = [
            {"text": "Hi!", "timestamp": "2026-03-15T09:00:00", "is_from_me": False,
             "handle": "14155551234@s.whatsapp.net", "push_name": "Alice", "native_message_id": "msg1"},
        ]
        with mock.patch.object(penguin_connect, "_WHATSAPP_CHANNEL") as mock_channel:
            mock_channel.fetch_messages.return_value = mock_messages
            result = penguin_connect._fetch_source_messages_for_conversation(
                conv, limit=50, since=None,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Hi!")

    # ------------------------------------------------------------------
    # Email import with WhatsApp provider
    # ------------------------------------------------------------------
    def test_build_import_email_sets_whatsapp_provider(self):
        raw = penguin_connect._build_import_email(
            "amc_test",
            "owner+am-test@gmail.com",
            "owner@gmail.com",
            "Alice",
            {"text": "hello from whatsapp", "timestamp": "2026-03-15T09:00:00+00:00", "attachments": []},
            source_provider="whatsapp",
        )
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(parsed["Subject"], "WhatsApp · Alice")
        self.assertIn(parsed["X-PenguinConnect-Source-Provider"].lower(), ("whatsapp",))

    # ------------------------------------------------------------------
    # Activity keys
    # ------------------------------------------------------------------
    def test_activity_keys_whatsapp_returns_chat_id_only(self):
        conv_id = self._insert_whatsapp_conversation()
        conv = self._conversation_row(conv_id)
        keys = penguin_connect._conversation_imessage_activity_keys(conv)
        self.assertEqual(keys, {"14155551234@s.whatsapp.net"})

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def test_ensure_whatsapp_conversations_discovered(self):
        mock_chats = {
            "available": True,
            "chats": [
                {
                    "chat_id": "14155559999@s.whatsapp.net",
                    "name": "Bob",
                    "chat_type": "dm",
                    "participants": ["14155559999"],
                    "participant_names": {"14155559999": "Bob Example"},
                    "latest_message": {
                        "native_message_id": "wa-latest-1",
                        "timestamp": "2026-03-15T12:00:00+00:00",
                        "text": "Latest WhatsApp preview",
                        "is_from_me": False,
                        "handle": "14155559999@s.whatsapp.net",
                        "push_name": "Bob",
                        "attachments": [],
                    },
                },
            ],
        }
        mock_channel = mock.Mock()
        mock_channel.list_conversations.return_value = mock_chats

        with mock.patch.object(penguin_connect, "_WHATSAPP_CHANNEL", mock_channel):
            with mock.patch("penguin_connect.log_action"):
                count = penguin_connect.ensure_whatsapp_conversations_discovered(
                    self.conn, "owner@gmail.com"
                )

        self.assertEqual(count, 1)
        rows = self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE source_provider = 'whatsapp'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_name"], "Bob")
        self.assertEqual(
            json.loads(rows[0]["participant_names"]),
            {"14155559999": "Bob Example"},
        )
        listed = penguin_connect.list_conversations(
            self.conn,
            discover_sources=False,
            hydrate_previews=False,
        )
        listed_row = next(
            row for row in listed["conversations"]
            if row["conversation_id"] == rows[0]["conversation_id"]
        )
        self.assertEqual(
            listed_row["participant_names"],
            {"14155559999": "Bob Example"},
        )
        mock_channel.list_conversations.assert_called_once_with(limit=None)
        preview = self.conn.execute(
            """SELECT provider, body_text
               FROM penguin_connect_messages
               WHERE conversation_id = ?""",
            (rows[0]["conversation_id"],),
        ).fetchone()
        self.assertEqual(preview["provider"], "whatsapp")
        self.assertEqual(preview["body_text"], "Latest WhatsApp preview")

    def test_ensure_whatsapp_conversations_discovered_skips_when_no_channel(self):
        with mock.patch.object(penguin_connect, "_WHATSAPP_CHANNEL", None):
            count = penguin_connect.ensure_whatsapp_conversations_discovered(
                self.conn, "owner@gmail.com"
            )
        self.assertEqual(count, 0)

    # ------------------------------------------------------------------
    # Provider message ID
    # ------------------------------------------------------------------
    def test_provider_message_id_whatsapp_uses_native_id(self):
        msg = {"native_message_id": "abc123", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hi"}
        result = penguin_connect._provider_message_id("whatsapp", msg)
        self.assertEqual(result, "whatsapp:abc123")


if __name__ == "__main__":
    unittest.main()
