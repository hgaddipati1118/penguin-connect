"""Unit tests for the WhatsApp channel adapter."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from channels import whatsapp
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

    def test_api_url_uses_documented_environment_variable(self):
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_WHATSAPP_API_URL": "http://127.0.0.1:9191/api"},
        ):
            self.assertEqual(
                whatsapp._whatsapp_api_url(),
                "http://127.0.0.1:9191/api",
            )


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

    def test_list_conversations_none_limit_means_all_available_chats(self):
        result = self.adapter.list_conversations(limit=None)
        self.assertTrue(result["available"])
        self.assertEqual(len(result["chats"]), 2)

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

    def test_list_conversations_resolves_lid_to_contact_name_and_phone(self):
        lid = "137276097073316"
        phone = "14047294874"
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO chats VALUES (?, ?, ?)", (f"{lid}@lid", "", "2026-03-15T12:00:00"))
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("lid-msg", f"{lid}@lid", f"{lid}@lid", "Hello from a LID", "2026-03-15T12:00:00", 0, None, None),
        )
        conn.commit()
        conn.close()
        with tempfile.NamedTemporaryFile(suffix=".db") as metadata_file:
            metadata = sqlite3.connect(metadata_file.name)
            metadata.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT UNIQUE NOT NULL)")
            metadata.execute(
                """CREATE TABLE whatsmeow_contacts (
                    our_jid TEXT, their_jid TEXT, first_name TEXT, full_name TEXT,
                    push_name TEXT, business_name TEXT, redacted_phone TEXT
                )"""
            )
            metadata.execute("INSERT INTO whatsmeow_lid_map VALUES (?, ?)", (lid, phone))
            metadata.execute(
                "INSERT INTO whatsmeow_contacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("me@s.whatsapp.net", f"{phone}@s.whatsapp.net", "", "Dhruv Example", "Dhruv", None, None),
            )
            metadata.commit()
            metadata.close()
            with mock.patch.dict(
                os.environ,
                {"PENGUIN_CONNECT_WHATSAPP_METADATA_DB_PATH": metadata_file.name},
            ):
                result = self.adapter.list_conversations()

        chat = next(item for item in result["chats"] if item["chat_id"] == f"{lid}@lid")
        self.assertEqual(chat["name"], "Dhruv Example")
        self.assertEqual(chat["participants"], [phone])

    def test_list_conversations_resolves_bare_lid_group_participants(self):
        lid = "999000111222333"
        phone = "15550101999"
        group_jid = "120363047891234567@g.us"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bare-lid-group-msg",
                group_jid,
                lid,
                "Hello from a bare LID",
                "2026-03-15T12:00:00",
                0,
                None,
                None,
            ),
        )
        conn.execute(
            "UPDATE chats SET last_message_time = ? WHERE jid = ?",
            ("2026-03-15T12:00:00", group_jid),
        )
        conn.commit()
        conn.close()
        with tempfile.NamedTemporaryFile(suffix=".db") as metadata_file:
            metadata = sqlite3.connect(metadata_file.name)
            metadata.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT UNIQUE NOT NULL)")
            metadata.execute(
                """CREATE TABLE whatsmeow_contacts (
                    our_jid TEXT, their_jid TEXT, first_name TEXT, full_name TEXT,
                    push_name TEXT, business_name TEXT, redacted_phone TEXT
                )"""
            )
            metadata.execute("INSERT INTO whatsmeow_lid_map VALUES (?, ?)", (lid, phone))
            metadata.execute(
                "INSERT INTO whatsmeow_contacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("me@s.whatsapp.net", f"{lid}@lid", "", "", "Taylor Example", None, None),
            )
            metadata.commit()
            metadata.close()
            with mock.patch.dict(
                os.environ,
                {"PENGUIN_CONNECT_WHATSAPP_METADATA_DB_PATH": metadata_file.name},
            ):
                result = self.adapter.list_conversations()

        chat = next(item for item in result["chats"] if item["chat_id"] == group_jid)
        self.assertIn(phone, chat["participants"])
        self.assertNotIn(lid, chat["participants"])
        self.assertEqual(chat["participant_names"][phone], "Taylor Example")
        self.assertEqual(chat["latest_message"]["push_name"], "Taylor Example")

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

    def test_fetch_messages_pages_backwards_without_repeating_boundary(self):
        older = self.adapter.fetch_messages(
            "14155551234@s.whatsapp.net",
            limit=2,
            before="2026-03-15T09:02:00",
            before_native_message_id="msg4",
        )
        self.assertEqual([message["native_message_id"] for message in older], ["msg2", "msg1"])

    def test_fetch_messages_does_not_download_media_during_cache_reads(self):
        with mock.patch.object(self.adapter, "_download_media") as download:
            messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)

        self.assertTrue(any(message.get("attachments") for message in messages))
        download.assert_not_called()

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

    def test_fetch_messages_includes_native_reply_context_when_bridge_supports_it(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("ALTER TABLE messages ADD COLUMN reply_to_message_id TEXT")
        conn.execute("ALTER TABLE messages ADD COLUMN reply_to_sender TEXT")
        conn.execute("ALTER TABLE messages ADD COLUMN reply_to_text TEXT")
        conn.execute(
            """UPDATE messages
               SET reply_to_message_id = ?, reply_to_sender = ?, reply_to_text = ?
               WHERE id = ?""",
            (
                "msg1",
                "14155551234@s.whatsapp.net",
                "Hello from Alice",
                "msg2",
            ),
        )
        conn.commit()
        conn.close()

        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)
        reply = next(message for message in messages if message["native_message_id"] == "msg2")

        self.assertEqual(reply["reply_to_message_id"], "msg1")
        self.assertEqual(reply["reply_to_sender"], "14155551234")
        self.assertEqual(reply["reply_to_text"], "Hello from Alice")

    def test_fetch_messages_includes_native_mutation_metadata_when_bridge_supports_it(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("ALTER TABLE messages ADD COLUMN reactions_json TEXT DEFAULT '[]'")
        conn.execute("ALTER TABLE messages ADD COLUMN edited_at TEXT")
        conn.execute(
            """UPDATE messages
               SET reactions_json = ?, edited_at = ?
               WHERE id = ?""",
            (
                json.dumps([
                    {"actor": "participant-a", "emoji": "😂", "is_from_me": False},
                    {"actor": "participant-b", "emoji": "😂", "is_from_me": False},
                    {"actor": "me", "emoji": "❤️", "is_from_me": True},
                ]),
                "2026-03-15T09:03:00Z",
                "msg2",
            ),
        )
        conn.commit()
        conn.close()

        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)
        edited = next(message for message in messages if message["native_message_id"] == "msg2")

        self.assertEqual(
            edited["provider_reactions"],
            [
                {"name": "😂", "emoji": "😂", "count": 2, "reacted_by_me": False},
                {"name": "❤️", "emoji": "❤️", "count": 1, "reacted_by_me": True},
            ],
        )
        self.assertTrue(edited["provider_edited"])
        self.assertEqual(edited["provider_edited_at"], "2026-03-15T09:03:00Z")
        self.assertTrue(edited["provider_can_delete"])
        self.assertFalse(edited["provider_can_edit"])

    def test_fetch_messages_tolerates_legacy_bridge_without_reply_columns(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)

        self.assertTrue(messages)
        self.assertTrue(all("reply_to_message_id" not in message for message in messages))

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
        self.assertEqual(att["whatsapp_chat_jid"], "14155551234@s.whatsapp.net")
        self.assertEqual(att["whatsapp_message_id"], "msg4")

    def test_fetch_messages_resolves_push_name(self):
        messages = self.adapter.fetch_messages("14155551234@s.whatsapp.net", limit=50)
        incoming = [m for m in messages if not m["is_from_me"] and m["text"]]
        self.assertEqual(incoming[0]["push_name"], "Alice")

    def test_fetch_messages_resolves_bare_lid_sender(self):
        lid = "999000111222333"
        phone = "15550101999"
        group_jid = "120363047891234567@g.us"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bare-lid-fetch-msg",
                group_jid,
                lid,
                "Hello from a bare LID",
                "2026-03-15T12:00:00",
                0,
                None,
                None,
            ),
        )
        conn.commit()
        conn.close()
        with tempfile.NamedTemporaryFile(suffix=".db") as metadata_file:
            metadata = sqlite3.connect(metadata_file.name)
            metadata.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT UNIQUE NOT NULL)")
            metadata.execute(
                """CREATE TABLE whatsmeow_contacts (
                    our_jid TEXT, their_jid TEXT, first_name TEXT, full_name TEXT,
                    push_name TEXT, business_name TEXT, redacted_phone TEXT
                )"""
            )
            metadata.execute("INSERT INTO whatsmeow_lid_map VALUES (?, ?)", (lid, phone))
            metadata.execute(
                "INSERT INTO whatsmeow_contacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("me@s.whatsapp.net", f"{lid}@lid", "", "", "Taylor Example", None, None),
            )
            metadata.commit()
            metadata.close()
            with mock.patch.dict(
                os.environ,
                {"PENGUIN_CONNECT_WHATSAPP_METADATA_DB_PATH": metadata_file.name},
            ):
                messages = self.adapter.fetch_messages(group_jid, limit=50)

        message = next(item for item in messages if item["native_message_id"] == "bare-lid-fetch-msg")
        self.assertEqual(message["push_name"], "Taylor Example")
        self.assertEqual(message["resolved_phone"], phone)

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

    def test_send_message_posts_native_reply_context_to_bridge(self):
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "message": "sent"}
        capability_response = mock.Mock()
        capability_response.status_code = 200
        capability_response.json.return_value = {"native_replies": True}
        with mock.patch(
            "channels.whatsapp.httpx.get",
            return_value=capability_response,
        ), mock.patch("channels.whatsapp.httpx.post", return_value=mock_response) as mock_post:
            ok, err = self.adapter.send_message(
                "14155551234@s.whatsapp.net",
                "Native reply",
                reply_to_message_id="msg1",
            )

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "recipient": "14155551234@s.whatsapp.net",
                "message": "Native reply",
                "reply_to_message_id": "msg1",
                "reply_to_sender": "14155551234@s.whatsapp.net",
                "reply_to_text": "Hello from Alice",
            },
        )

    def test_send_message_fails_closed_when_bridge_cannot_confirm_native_replies(self):
        capability_response = mock.Mock()
        capability_response.status_code = 404
        with mock.patch(
            "channels.whatsapp.httpx.get",
            return_value=capability_response,
        ), mock.patch("channels.whatsapp.httpx.post") as mock_post:
            ok, err = self.adapter.send_message(
                "14155551234@s.whatsapp.net",
                "Must remain nested",
                reply_to_message_id="msg1",
            )

        self.assertFalse(ok)
        self.assertEqual(err, "whatsapp_native_replies_unavailable")
        mock_post.assert_not_called()

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

    def test_native_mutations_post_exact_targets_to_bridge(self):
        capability_response = mock.Mock(status_code=200)
        capability_response.json.return_value = {
            "native_reactions": True,
            "native_edits": True,
            "native_deletes": True,
        }
        mutation_response = mock.Mock(status_code=200)
        mutation_response.json.return_value = {"success": True}
        with mock.patch(
            "channels.whatsapp.httpx.get",
            return_value=capability_response,
        ), mock.patch(
            "channels.whatsapp.httpx.post",
            return_value=mutation_response,
        ) as mock_post:
            self.assertEqual(
                self.adapter.set_reaction(
                    "14155551234@s.whatsapp.net",
                    "whatsapp:msg2",
                    "❤️",
                    remove=True,
                ),
                (True, None),
            )
            self.assertEqual(
                self.adapter.edit_message(
                    "14155551234@s.whatsapp.net",
                    "whatsapp:msg2",
                    "Updated",
                ),
                (True, None),
            )
            self.assertEqual(
                self.adapter.delete_message(
                    "14155551234@s.whatsapp.net",
                    "whatsapp:msg2",
                ),
                (True, None),
            )

        self.assertEqual(
            [call.args[0] for call in mock_post.call_args_list],
            [
                "http://localhost:8080/api/react",
                "http://localhost:8080/api/edit",
                "http://localhost:8080/api/delete",
            ],
        )
        self.assertEqual(
            mock_post.call_args_list[0].kwargs["json"],
            {
                "chat_jid": "14155551234@s.whatsapp.net",
                "message_id": "msg2",
                "emoji": "❤️",
                "remove": True,
            },
        )
        self.assertEqual(
            mock_post.call_args_list[1].kwargs["json"],
            {
                "chat_jid": "14155551234@s.whatsapp.net",
                "message_id": "msg2",
                "message": "Updated",
            },
        )

    def test_native_mutations_fail_closed_without_capability_or_exact_target(self):
        capability_response = mock.Mock(status_code=200)
        capability_response.json.return_value = {"native_reactions": False}
        with mock.patch(
            "channels.whatsapp.httpx.get",
            return_value=capability_response,
        ), mock.patch("channels.whatsapp.httpx.post") as mock_post:
            self.assertEqual(
                self.adapter.set_reaction(
                    "14155551234@s.whatsapp.net",
                    "msg2",
                    "❤️",
                ),
                (False, "whatsapp_native_reactions_unavailable"),
            )
            self.assertEqual(
                self.adapter.delete_message("not-an-exact-jid", "msg2"),
                (False, "whatsapp_message_target_required"),
            )
        mock_post.assert_not_called()

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
