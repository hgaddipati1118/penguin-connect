"""Unit tests for the WhatsApp channel adapter."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from channels.whatsapp import WhatsAppChannelAdapter, _jid_to_phone, _is_group_jid


class WhatsAppHelperTests(unittest.TestCase):
    def test_jid_to_phone_strips_suffix(self):
        self.assertEqual(_jid_to_phone("14155551234@s.whatsapp.net"), "14155551234")

    def test_jid_to_phone_returns_bare_number(self):
        self.assertEqual(_jid_to_phone("14155551234"), "14155551234")

    def test_is_group_jid_detects_group(self):
        self.assertTrue(_is_group_jid("120363047891234567@g.us"))

    def test_is_group_jid_detects_individual(self):
        self.assertFalse(_is_group_jid("14155551234@s.whatsapp.net"))


class WhatsAppAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.env_patch = mock.patch.dict(os.environ, {"PENGUIN_CONNECT_WHATSAPP_DB_PATH": self.db_path})
        self.env_patch.start()

        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS chats (
            jid TEXT PRIMARY KEY,
            name TEXT,
            last_message_time TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id TEXT,
            chat_jid TEXT,
            sender TEXT,
            content TEXT,
            timestamp TEXT,
            is_from_me INTEGER DEFAULT 0,
            media_type TEXT,
            filename TEXT,
            PRIMARY KEY (id, chat_jid)
        )""")
        conn.execute(
            "INSERT INTO chats VALUES (?, ?, ?)",
            ("14155551234@s.whatsapp.net", "Alice", "2026-03-15T10:00:00"),
        )
        conn.execute(
            "INSERT INTO chats VALUES (?, ?, ?)",
            ("120363047891234567@g.us", "Family Group", "2026-03-15T11:00:00"),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("msg1", "14155551234@s.whatsapp.net", "14155551234@s.whatsapp.net", "Hello from Alice", "2026-03-15T09:00:00", 0, None, None),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("msg2", "14155551234@s.whatsapp.net", "me", "Hi Alice!", "2026-03-15T09:01:00", 1, None, None),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("msg3", "120363047891234567@g.us", "14155559999@s.whatsapp.net", "Group message", "2026-03-15T10:00:00", 0, None, None),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("msg4", "14155551234@s.whatsapp.net", "14155551234@s.whatsapp.net", None, "2026-03-15T09:02:00", 0, "image/jpeg", "photo.jpg"),
        )
        conn.commit()
        conn.close()
        self.adapter = WhatsAppChannelAdapter()

    def tearDown(self):
        self.env_patch.stop()
        os.unlink(self.db_path)

    def test_provider_attributes(self):
        self.assertEqual(self.adapter.provider, "whatsapp")
        self.assertEqual(self.adapter.provider_label, "WhatsApp")

    def test_list_conversations_returns_all_chats(self):
        result = self.adapter.list_conversations()
        self.assertTrue(result["available"])
        chats = result["chats"]
        self.assertEqual(len(chats), 2)
        jids = {c["chat_id"] for c in chats}
        self.assertIn("14155551234@s.whatsapp.net", jids)
        self.assertIn("120363047891234567@g.us", jids)

    def test_list_conversations_dm_vs_group(self):
        result = self.adapter.list_conversations()
        chats_by_jid = {c["chat_id"]: c for c in result["chats"]}
        self.assertEqual(chats_by_jid["14155551234@s.whatsapp.net"]["chat_type"], "dm")
        self.assertEqual(chats_by_jid["120363047891234567@g.us"]["chat_type"], "group")

    def test_list_conversations_uses_chat_name(self):
        result = self.adapter.list_conversations()
        chats_by_jid = {c["chat_id"]: c for c in result["chats"]}
        self.assertEqual(chats_by_jid["14155551234@s.whatsapp.net"]["name"], "Alice")
        self.assertEqual(chats_by_jid["120363047891234567@g.us"]["name"], "Family Group")

    def test_list_conversations_source_provider(self):
        result = self.adapter.list_conversations()
        for chat in result["chats"]:
            self.assertEqual(chat["source_provider"], "whatsapp")
            self.assertEqual(chat["service"], "WhatsApp")

    def test_list_conversations_search_filter(self):
        result = self.adapter.list_conversations(search="alice")
        self.assertTrue(result["available"])
        self.assertEqual(len(result["chats"]), 1)
        self.assertEqual(result["chats"][0]["name"], "Alice")

    def test_list_conversations_no_db(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_WHATSAPP_DB_PATH": "/nonexistent/db.sqlite"}):
            result = self.adapter.list_conversations()
        self.assertFalse(result["available"])

    def test_list_recent_activity_finds_active_chats(self):
        result = self.adapter.list_recent_activity("2026-03-15T08:00:00")
        self.assertTrue(result["available"])
        self.assertEqual(len(result["chats"]), 2)

    def test_list_recent_activity_filters_by_since(self):
        result = self.adapter.list_recent_activity("2026-03-15T09:30:00")
        self.assertTrue(result["available"])
        jids = {c["chat_id"] for c in result["chats"]}
        self.assertIn("120363047891234567@g.us", jids)

    def test_list_recent_activity_no_db(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_WHATSAPP_DB_PATH": "/nonexistent/db.sqlite"}):
            result = self.adapter.list_recent_activity("2026-03-15T00:00:00")
        self.assertFalse(result["available"])

    def test_fetch_messages_returns_all_for_chat(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)
        self.assertEqual(len(messages), 3)

    def test_fetch_messages_since_filters(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50, since="2026-03-15T09:00:30")
        self.assertEqual(len(messages), 2)
        texts = {m["text"] for m in messages if m["text"]}
        self.assertIn("Hi Alice!", texts)

    def test_fetch_messages_has_required_keys(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=1)
        msg = messages[0]
        self.assertIn("text", msg)
        self.assertIn("timestamp", msg)
        self.assertIn("is_from_me", msg)
        self.assertIn("handle", msg)
        self.assertIn("native_message_id", msg)
        self.assertIn("service", msg)
        self.assertEqual(msg["service"], "WhatsApp")

    def test_fetch_messages_is_from_me(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)
        from_me = [m for m in messages if m["is_from_me"]]
        not_from_me = [m for m in messages if not m["is_from_me"]]
        self.assertEqual(len(from_me), 1)
        self.assertEqual(from_me[0]["text"], "Hi Alice!")
        self.assertGreater(len(not_from_me), 0)

    def test_fetch_messages_includes_attachments(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)
        attachment_msgs = [m for m in messages if m.get("attachments")]
        self.assertEqual(len(attachment_msgs), 1)
        att = attachment_msgs[0]["attachments"][0]
        self.assertEqual(att["mime_type"], "image/jpeg")
        self.assertEqual(att["filename"], "photo.jpg")

    def test_fetch_messages_resolves_push_name(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)
        incoming = [m for m in messages if not m["is_from_me"] and m["text"]]
        self.assertEqual(incoming[0]["push_name"], "Alice")

    def test_fetch_messages_no_db(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_WHATSAPP_DB_PATH": "/nonexistent/db.sqlite"}):
            result = self.adapter.fetch_messages("14155551234@s.whatsapp.net")
        self.assertEqual(result, [])

    def test_fetch_messages_empty_chat(self):
        messages = self.adapter.fetch_messages("nonexistent@s.whatsapp.net")
        self.assertEqual(messages, [])

    def test_send_message_posts_to_bridge(self):
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "message": "sent"}
        with mock.patch("channels.whatsapp.httpx.post", return_value=mock_response) as mock_post:
            ok, err = self.adapter.send_message("14155551234@s.whatsapp.net", "Hello!")
        self.assertTrue(ok)
        self.assertIsNone(err)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn("Hello!", str(call_kwargs))

    def test_send_message_empty_rejected(self):
        ok, err = self.adapter.send_message("14155551234@s.whatsapp.net", "")
        self.assertFalse(ok)
        self.assertEqual(err, "empty_message")

    def test_send_message_handles_failure(self):
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        with mock.patch("channels.whatsapp.httpx.post", return_value=mock_response):
            ok, err = self.adapter.send_message("14155551234@s.whatsapp.net", "Hello!")
        self.assertFalse(ok)
        self.assertIn("whatsapp_send_failed", err)

    def test_send_message_handles_network_error(self):
        with mock.patch("channels.whatsapp.httpx.post", side_effect=Exception("Connection refused")):
            ok, err = self.adapter.send_message("14155551234@s.whatsapp.net", "Hello!")
        self.assertFalse(ok)
        self.assertIn("Connection refused", err)

    def test_get_unread_count_returns_none(self):
        self.assertIsNone(self.adapter.get_unread_count("14155551234@s.whatsapp.net"))

    def test_resolve_sender_and_subject_incoming(self):
        conn = mock.Mock()
        conv = {"display_name": "Alice", "chat_type": "dm"}
        msg = {"handle": "14155551234@s.whatsapp.net", "push_name": "Alice", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn,
            conv,
            msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: False,
        )
        self.assertEqual(sender, "Alice")
        self.assertEqual(subject, "Alice")

    def test_resolve_sender_and_subject_from_me(self):
        conn = mock.Mock()
        conv = {"display_name": "Alice", "chat_type": "dm"}
        msg = {"handle": "me", "push_name": "", "is_from_me": True}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn,
            conv,
            msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: False,
        )
        self.assertEqual(sender, "Me")

    def test_resolve_sender_and_subject_uses_contact_lookup(self):
        conn = mock.Mock()
        conv = {"display_name": "", "chat_type": "dm"}
        msg = {"handle": "14155551234@s.whatsapp.net", "push_name": "WA Name", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn,
            conv,
            msg,
            lookup_contact_name=lambda c, h: "Alice Smith" if h == "14155551234" else None,
            looks_like_unresolved_handle=lambda h: h == "",
        )
        self.assertEqual(sender, "Alice Smith")
        self.assertEqual(subject, "Alice Smith")

    def test_resolve_sender_and_subject_group(self):
        conn = mock.Mock()
        conv = {"display_name": "Family Group", "chat_type": "group"}
        msg = {"handle": "14155559999@s.whatsapp.net", "push_name": "Bob", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn,
            conv,
            msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: False,
        )
        self.assertEqual(sender, "Bob")
        self.assertEqual(subject, "Family Group")


class WhatsAppProviderMessageIdTests(unittest.TestCase):
    def test_provider_message_id_uses_whatsapp_prefix(self):
        import penguin_connect

        msg = {"native_message_id": "abc123", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hello"}
        result = penguin_connect._provider_message_id("whatsapp", msg)
        self.assertTrue(result.startswith("whatsapp:"))
        self.assertIn("abc123", result)

    def test_provider_message_id_imessage_fallback(self):
        import penguin_connect

        msg = {"native_message_id": "123", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hello"}
        result = penguin_connect._provider_message_id("imessage", msg)
        self.assertTrue(result.startswith("imessage:"))

    def test_provider_message_id_whatsapp_hash_fallback(self):
        import penguin_connect

        msg = {"native_message_id": "", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hello"}
        result = penguin_connect._provider_message_id("whatsapp", msg)
        self.assertTrue(result.startswith("whatsapp:"))
        self.assertGreater(len(result), len("whatsapp:"))


if __name__ == "__main__":
    unittest.main()
