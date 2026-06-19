import json
import base64
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
from db import SCHEMA

class AppHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "cache.db"

        conn = self._get_connection()
        try:
            conn.executescript(SCHEMA)
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
            conn.executemany(
                """INSERT INTO contacts
                   (first_name, last_name, organization, phone, phone_normalized, email, source_db)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        "Taylor",
                        "Example",
                        "",
                        "+1 (512) 743-6385",
                        "15127436385",
                        "",
                        "synthetic",
                    ),
                    (
                        "",
                        "",
                        "Example Ops",
                        "",
                        "",
                        "ops@example.test",
                        "synthetic",
                    ),
                ],
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, chat_type, participants,
                    alias_email, status)
                   VALUES (?, ?, ?, ?, 'dm', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_test",
                    "chat-123",
                    "Taylor",
                    '["+15127436385"]',
                    "owner+am-test@gmail.com",
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_aliases
                   (conversation_id, alias_email, alias_local_part, status)
                   VALUES (?, ?, ?, 'active')""",
                (
                    "amc_test",
                    "owner+am-test@gmail.com",
                    "owner+am-test",
                ),
            )
            conn.executemany(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        "amc_test",
                        "imessage",
                        "imsg-older",
                        "imessage_to_gmail",
                        None,
                        "[Apple Messages] Taylor",
                        "Older message",
                        "2026-03-09T10:00:00+00:00",
                        1,
                        "{}",
                    ),
                    (
                        "amc_test",
                        "imessage",
                        "imsg-latest",
                        "imessage_to_gmail",
                        None,
                        "[Apple Messages] Taylor",
                        "Latest message",
                        "2026-03-10T10:00:00+00:00",
                        1,
                        "{}",
                    ),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        self.patchers = [
            mock.patch("app.get_connection", side_effect=self._get_connection),
            mock.patch("app.init_db"),
            mock.patch("app.assert_startup_ready", return_value={"ok": True}),
            mock.patch("app.start_watchers"),
            mock.patch("app.stop_watchers"),
            mock.patch(
                "app.penguinconnect_run_startup_catchup",
                return_value={"success": True, "skipped": True, "reason": "queue_idle"},
            ),
            mock.patch("app.log_action"),
            mock.patch("builtins.print"),
            mock.patch("app.action_log_path", return_value=Path(self.tmpdir.name) / "actions.jsonl"),
            mock.patch.object(app_module, "DB_PATH", self.db_path),
            mock.patch("penguin_connect.log_action"),
            mock.patch(
                "penguin_connect.send_imessage",
                side_effect=AssertionError("Tests must mock send_imessage explicitly"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmpdir.cleanup()

    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def test_status_endpoint_reports_counts_and_runtime_sync_status(self):
        with mock.patch(
            "app.penguinconnect_get_gmail_connection_status",
            return_value={"connected": True, "gmail_email": "owner@gmail.com"},
        ), mock.patch(
            "app.penguinconnect_get_cached_sync_metrics",
            return_value={"totals": {"retry_queue_count": 1}},
        ), mock.patch(
            "app.penguinconnect_get_runtime_sync_status",
            return_value={"running": True, "mode": "incremental"},
        ), mock.patch(
            "app.get_sync_status",
            return_value={"penguin_connect": {"polling": True, "last_sync": "2026-03-10T10:30:00+00:00"}},
        ), TestClient(app_module.app) as client:
            response = client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_conversations"], 1)
        self.assertEqual(body["active_conversations"], 1)
        self.assertTrue(body["sync_status"]["penguin_connect"]["polling"])
        self.assertTrue(body["sync_status"]["penguin_connect"]["running"])
        self.assertEqual(body["sync_metrics"]["totals"]["retry_queue_count"], 1)

    def test_app_startup_refreshes_contacts_once(self):
        with mock.patch("app.refresh_contacts_now", return_value={"success": True}) as mock_refresh, TestClient(
            app_module.app
        ):
            pass

        mock_refresh.assert_called_once_with()

    def test_app_startup_retries_transient_startup_catchup_skips(self):
        results = [
            {"success": True, "skipped": True, "reason": "gmail_rate_limited", "retry_after_seconds": 3},
            {"success": True, "skipped": True, "reason": "queue_busy"},
            {"success": True, "skipped": False, "pending_bootstrap_conversations": 0, "pending_full_verify_conversations": 0},
        ]
        finished = threading.Event()

        def fake_run_startup_catchup():
            result = results.pop(0)
            if not results:
                finished.set()
            return result

        with mock.patch("app.penguinconnect_run_startup_catchup", side_effect=fake_run_startup_catchup) as mock_run, mock.patch(
            "app._startup_catchup_batch_pause_seconds",
            return_value=7.0,
        ), mock.patch("app.time.sleep") as mock_sleep, TestClient(app_module.app):
            self.assertTrue(finished.wait(1))

        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [mock.call(3.0), mock.call(7.0)])

    def test_messages_endpoint_respects_limit_and_returns_latest_first(self):
        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["found"])
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-latest")
        self.assertEqual(body["messages"][0]["body_text"], "Latest message")

    def test_conversations_endpoint_includes_unread_count(self):
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE penguin_connect_messages SET is_read = 0 WHERE provider_message_id = ?",
                ("imsg-latest",),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations")

        self.assertEqual(response.status_code, 200)
        conversation = response.json()["conversations"][0]
        self.assertEqual(conversation["conversation_id"], "amc_test")
        self.assertEqual(conversation["unread_count"], 1)
        self.assertTrue(conversation["has_unread"])
        self.assertFalse(conversation["is_pinned"])
        self.assertFalse(conversation["is_archived"])
        self.assertEqual(conversation["title"], "")
        self.assertEqual(conversation["note"], "")
        self.assertEqual(conversation["labels"], [])
        self.assertEqual(conversation["draft_text"], "")
        self.assertEqual(conversation["follow_up_at"], "")
        self.assertEqual(conversation["last_message_preview"], "Latest message")
        self.assertEqual(conversation["last_message_sender"], "")
        self.assertEqual(conversation["last_message_provider_id"], "imsg-latest")
        self.assertFalse(conversation["last_message_has_attachments"])

    def test_conversation_management_endpoint_pins_and_archives(self):
        with TestClient(app_module.app) as client:
            pin_response = client.post(
                "/penguin-connect/conversations/amc_test/management",
                json={
                    "pinned": True,
                    "title": "Local Taylor Thread",
                    "note": "Follow up after intro",
                    "labels": ["VIP", "#Hiring", "vip", " ".join(["long"] * 20)],
                    "draft_text": "Draft reply from local UI",
                    "follow_up_at": "2026-03-12T09:30",
                },
            )
            pinned_list_response = client.get("/penguin-connect/conversations")
            archive_response = client.post("/penguin-connect/conversations/amc_test/management", json={"archived": True})
            archived_list_response = client.get("/penguin-connect/conversations")
            unarchive_response = client.post("/penguin-connect/conversations/amc_test/management", json={"archived": False})

        self.assertEqual(pin_response.status_code, 200)
        pin_body = pin_response.json()
        self.assertTrue(pin_body["success"])
        self.assertTrue(pin_body["is_pinned"])
        self.assertFalse(pin_body["is_archived"])
        self.assertEqual(pin_body["title"], "Local Taylor Thread")
        self.assertEqual(pin_body["note"], "Follow up after intro")
        self.assertEqual(pin_body["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(pin_body["draft_text"], "Draft reply from local UI")
        self.assertEqual(pin_body["follow_up_at"], "2026-03-12T09:30")

        pinned_conversation = pinned_list_response.json()["conversations"][0]
        self.assertTrue(pinned_conversation["is_pinned"])
        self.assertFalse(pinned_conversation["is_archived"])
        self.assertEqual(pinned_conversation["title"], "Local Taylor Thread")
        self.assertEqual(pinned_conversation["note"], "Follow up after intro")
        self.assertEqual(pinned_conversation["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(pinned_conversation["draft_text"], "Draft reply from local UI")
        self.assertEqual(pinned_conversation["follow_up_at"], "2026-03-12T09:30")

        self.assertEqual(archive_response.status_code, 200)
        archive_body = archive_response.json()
        self.assertFalse(archive_body["is_pinned"])
        self.assertTrue(archive_body["is_archived"])
        self.assertEqual(archive_body["title"], "Local Taylor Thread")
        self.assertEqual(archive_body["note"], "Follow up after intro")
        self.assertEqual(archive_body["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(archive_body["draft_text"], "Draft reply from local UI")
        self.assertEqual(archive_body["follow_up_at"], "2026-03-12T09:30")

        archived_conversation = archived_list_response.json()["conversations"][0]
        self.assertFalse(archived_conversation["is_pinned"])
        self.assertTrue(archived_conversation["is_archived"])
        self.assertEqual(archived_conversation["title"], "Local Taylor Thread")
        self.assertEqual(archived_conversation["note"], "Follow up after intro")
        self.assertEqual(archived_conversation["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(archived_conversation["draft_text"], "Draft reply from local UI")
        self.assertEqual(archived_conversation["follow_up_at"], "2026-03-12T09:30")

        self.assertEqual(unarchive_response.status_code, 200)
        unarchive_body = unarchive_response.json()
        self.assertFalse(unarchive_body["is_pinned"])
        self.assertFalse(unarchive_body["is_archived"])

    def test_conversation_management_endpoint_rejects_unknown_conversation(self):
        with TestClient(app_module.app) as client:
            response = client.post("/penguin-connect/conversations/missing/management", json={"pinned": True})

        self.assertEqual(response.status_code, 404)

    def test_read_state_endpoint_marks_conversation_read_and_unread(self):
        with TestClient(app_module.app) as client:
            unread_response = client.post("/penguin-connect/conversations/amc_test/read-state", json={"unread": True})
            read_response = client.post("/penguin-connect/conversations/amc_test/read-state", json={"unread": False})

        self.assertEqual(unread_response.status_code, 200)
        unread_body = unread_response.json()
        self.assertEqual(unread_body["updated_messages"], 2)
        self.assertEqual(unread_body["unread_count"], 2)
        self.assertTrue(unread_body["has_unread"])

        self.assertEqual(read_response.status_code, 200)
        read_body = read_response.json()
        self.assertEqual(read_body["updated_messages"], 2)
        self.assertEqual(read_body["unread_count"], 0)
        self.assertFalse(read_body["has_unread"])

        conn = self._get_connection()
        try:
            unread_count = conn.execute(
                "SELECT COUNT(*) FROM penguin_connect_messages WHERE conversation_id = ? AND COALESCE(is_read, 0) = 0",
                ("amc_test",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(unread_count, 0)

    def test_message_management_endpoint_stars_cached_message(self):
        with TestClient(app_module.app) as client:
            star_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "starred": True},
            )
            note_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "note": "Ask about timing"},
            )
            messages_response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})
            unstar_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "starred": False},
            )
            clear_note_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "note": ""},
            )

        self.assertEqual(star_response.status_code, 200)
        self.assertTrue(star_response.json()["is_starred"])

        self.assertEqual(note_response.status_code, 200)
        note_body = note_response.json()
        self.assertTrue(note_body["is_starred"])
        self.assertEqual(note_body["message_note"], "Ask about timing")
        self.assertTrue(note_body["has_note"])

        self.assertEqual(messages_response.status_code, 200)
        message = messages_response.json()["messages"][0]
        self.assertEqual(message["provider_message_id"], "imsg-latest")
        self.assertTrue(message["is_starred"])
        self.assertEqual(message["message_note"], "Ask about timing")

        self.assertEqual(unstar_response.status_code, 200)
        unstar_body = unstar_response.json()
        self.assertFalse(unstar_body["is_starred"])
        self.assertEqual(unstar_body["message_note"], "Ask about timing")

        self.assertEqual(clear_note_response.status_code, 200)
        clear_note_body = clear_note_response.json()
        self.assertFalse(clear_note_body["is_starred"])
        self.assertEqual(clear_note_body["message_note"], "")
        self.assertFalse(clear_note_body["has_note"])

    def test_message_management_endpoint_rejects_unknown_message(self):
        with TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "missing-message", "starred": True},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "message_not_found")

    def test_message_search_endpoint_searches_cached_messages(self):
        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/messages/search", params={"query": "latest", "limit": 10})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["messages"][0]["conversation_id"], "amc_test")
        self.assertEqual(body["messages"][0]["display_name"], "Taylor")
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-latest")

    def test_message_search_endpoint_searches_attachment_metadata(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "imessage",
                    "imsg-voice",
                    "imessage_to_gmail",
                    None,
                    "[Apple Messages] Taylor",
                    "",
                    "2026-03-08T10:00:00+00:00",
                    1,
                    json.dumps({"attachments": [{"transfer_name": "voice-note.m4a", "mime_type": "audio/mp4"}]}),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/messages/search", params={"query": "voice-note", "limit": 10})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-voice")
        self.assertEqual(body["messages"][0]["attachments"][0]["transfer_name"], "voice-note.m4a")

    def test_message_search_endpoint_filters_scoped_views(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversation_management
                   (conversation_id, title, labels)
                   VALUES (?, ?, ?)""",
                ("amc_test", "Launch crew", json.dumps(["planning"])),
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, alias_email, status)
                   VALUES (?, ?, ?, ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_other",
                    "iMessage;-;chat-other",
                    "Morgan",
                    "owner+other@gmail.com",
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_other",
                    "imessage",
                    "imsg-audio",
                    "imessage_to_gmail",
                    None,
                    "Morgan",
                    "[Apple Messages] Morgan",
                    "",
                    "2026-03-11T10:00:00+00:00",
                    1,
                    json.dumps({"attachments": [{"transfer_name": "voice-memo.m4a", "mime_type": "audio/mp4"}]}),
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "imessage",
                    "imsg-file",
                    "imessage_to_gmail",
                    None,
                    "Taylor",
                    "[Apple Messages] Taylor",
                    "",
                    "2026-03-11T09:00:00+00:00",
                    1,
                    json.dumps({"attachments": [{"transfer_name": "deck.pdf", "mime_type": "application/pdf"}]}),
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "manual",
                    "manual-sent",
                    "manual_to_imessage",
                    "owner@gmail.com",
                    "Me",
                    "[Apple Messages] Taylor",
                    "I sent the plan.",
                    "2026-03-11T09:30:00+00:00",
                    1,
                    "{}",
                ),
            )
            conn.execute(
                "UPDATE penguin_connect_messages SET is_read = 0 WHERE provider_message_id = ?",
                ("imsg-latest",),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            title_response = client.get("/penguin-connect/messages/search", params={"query": "launch", "limit": 10})
            audio_response = client.get("/penguin-connect/messages/search", params={"view": "audio", "limit": 10})
            files_response = client.get("/penguin-connect/messages/search", params={"view": "files", "limit": 10})
            unread_response = client.get("/penguin-connect/messages/search", params={"view": "unread", "limit": 10})
            mine_response = client.get("/penguin-connect/messages/search", params={"view": "mine", "limit": 10})
            current_response = client.get(
                "/penguin-connect/messages/search",
                params={"view": "current", "conversation_id": "amc_test", "limit": 10},
            )

        self.assertEqual(title_response.status_code, 200)
        title_body = title_response.json()
        self.assertGreaterEqual(title_body["count"], 1)
        self.assertEqual(title_body["messages"][0]["title"], "Launch crew")
        self.assertEqual(title_body["messages"][0]["labels"], ["planning"])

        self.assertEqual(audio_response.status_code, 200)
        audio_body = audio_response.json()
        self.assertEqual(audio_body["view"], "audio")
        self.assertEqual([message["provider_message_id"] for message in audio_body["messages"]], ["imsg-audio"])

        self.assertEqual(files_response.status_code, 200)
        file_ids = {message["provider_message_id"] for message in files_response.json()["messages"]}
        self.assertIn("imsg-audio", file_ids)
        self.assertIn("imsg-file", file_ids)

        self.assertEqual(unread_response.status_code, 200)
        unread_ids = {message["provider_message_id"] for message in unread_response.json()["messages"]}
        self.assertIn("imsg-latest", unread_ids)

        self.assertEqual(mine_response.status_code, 200)
        self.assertEqual([message["provider_message_id"] for message in mine_response.json()["messages"]], ["manual-sent"])

        self.assertEqual(current_response.status_code, 200)
        current_body = current_response.json()
        self.assertEqual(current_body["view"], "current")
        self.assertEqual(current_body["conversation_id"], "amc_test")
        self.assertTrue(current_body["messages"])
        self.assertTrue(all(message["conversation_id"] == "amc_test" for message in current_body["messages"]))
        self.assertNotIn("imsg-audio", {message["provider_message_id"] for message in current_body["messages"]})

    def test_attachment_endpoint_serves_stored_message_file(self):
        attachment_path = Path(self.tmpdir.name) / "voice-note.m4a"
        attachment_path.write_bytes(b"fake-audio")
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "imessage",
                    "imsg-voice-file",
                    "imessage_to_gmail",
                    None,
                    "[Apple Messages] Taylor",
                    "",
                    "2026-03-08T10:05:00+00:00",
                    1,
                    json.dumps(
                        {
                            "attachments": [
                                {
                                    "filename": str(attachment_path),
                                    "transfer_name": "voice-note.m4a",
                                    "mime_type": "audio/mp4",
                                    "size": len(b"fake-audio"),
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations/amc_test/attachments/0",
                params={"provider_message_id": "imsg-voice-file"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"fake-audio")
        self.assertTrue(response.headers["content-type"].startswith("audio/mp4"))
        self.assertIn("voice-note.m4a", response.headers["content-disposition"])

    def test_attachment_endpoint_rejects_unknown_attachment(self):
        with TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations/amc_test/attachments/0",
                params={"provider_message_id": "imsg-latest"},
            )

        self.assertEqual(response.status_code, 404)

    def test_contacts_endpoint_searches_cached_contacts(self):
        with TestClient(app_module.app) as client:
            favorite_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "favorite": True},
            )
            note_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "note": "Met at demo day. Ask about pilots."},
            )
            response = client.get("/penguin-connect/contacts", params={"search": "taylor", "limit": 10})
            phone_response = client.get("/penguin-connect/contacts", params={"search": "+15127436385", "limit": 10})
            note_search_response = client.get("/penguin-connect/contacts", params={"search": "pilots", "limit": 10})
            favorites_response = client.get("/penguin-connect/contacts", params={"source": "favorites", "limit": 1})
            unfavorite_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "favorite": False},
            )
            empty_favorites_response = client.get("/penguin-connect/contacts", params={"source": "favorites", "limit": 10})

        self.assertEqual(favorite_response.status_code, 200)
        self.assertTrue(favorite_response.json()["is_favorite"])
        self.assertEqual(note_response.status_code, 200)
        self.assertTrue(note_response.json()["is_favorite"])
        self.assertTrue(note_response.json()["has_note"])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total_contacts"], 2)
        self.assertEqual(body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(body["contacts"][0]["contact_key"], "phone:15127436385")
        self.assertTrue(body["contacts"][0]["is_favorite"])
        self.assertEqual(body["contacts"][0]["contact_note"], "Met at demo day. Ask about pilots.")
        self.assertEqual(body["contacts"][0]["primary_handle"], "+1 (512) 743-6385")
        self.assertEqual(body["contacts"][0]["handle_type"], "phone")
        self.assertEqual(phone_response.status_code, 200)
        phone_body = phone_response.json()
        self.assertEqual(phone_body["count"], 1)
        self.assertEqual(phone_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertTrue(phone_body["contacts"][0]["is_favorite"])
        self.assertEqual(note_search_response.status_code, 200)
        note_body = note_search_response.json()
        self.assertEqual(note_body["count"], 1)
        self.assertEqual(note_body["contacts"][0]["contact_key"], "phone:15127436385")
        self.assertEqual(note_body["contacts"][0]["contact_note"], "Met at demo day. Ask about pilots.")

        self.assertEqual(favorites_response.status_code, 200)
        favorites_body = favorites_response.json()
        self.assertEqual(favorites_body["source"], "favorites")
        self.assertEqual(favorites_body["count"], 1)
        self.assertEqual(favorites_body["contacts"][0]["contact_key"], "phone:15127436385")

        self.assertEqual(unfavorite_response.status_code, 200)
        self.assertFalse(unfavorite_response.json()["is_favorite"])
        self.assertEqual(empty_favorites_response.status_code, 200)
        self.assertEqual(empty_favorites_response.json()["count"], 0)

    def test_contacts_endpoint_searches_unsaved_conversation_participants(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, chat_type, participants,
                    alias_email, status)
                   VALUES (?, ?, ?, ?, 'dm', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_unsaved",
                    "chat-unsaved",
                    "Unsaved Thread",
                    '["+1 (415) 555-0199"]',
                    "owner+unsaved@gmail.com",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/contacts", params={"search": "5550199", "limit": 10})
            saved_response = client.get("/penguin-connect/contacts", params={"search": "+15127436385", "limit": 10})
            saved_only_response = client.get(
                "/penguin-connect/contacts",
                params={"search": "5550199", "source": "contacts", "limit": 10},
            )
            unsaved_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "participants", "limit": 10},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total_contacts"], 2)
        self.assertEqual(body["participant_count"], 1)
        result = body["contacts"][0]
        self.assertEqual(result["source"], "conversation")
        self.assertFalse(result["is_saved"])
        self.assertEqual(result["display_name"], "+1 (415) 555-0199")
        self.assertEqual(result["primary_handle"], "+1 (415) 555-0199")
        self.assertEqual(result["phone_normalized"], "14155550199")
        self.assertEqual(result["handle_type"], "phone")
        self.assertEqual(result["conversation_id"], "amc_unsaved")
        self.assertIn("Unsaved Thread", result["organization"])

        self.assertEqual(saved_response.status_code, 200)
        saved_body = saved_response.json()
        self.assertEqual(saved_body["count"], 1)
        self.assertEqual(saved_body["participant_count"], 0)
        self.assertEqual(saved_body["contacts"][0]["source"], "contacts")
        self.assertTrue(saved_body["contacts"][0]["is_saved"])

        self.assertEqual(saved_only_response.status_code, 200)
        self.assertEqual(saved_only_response.json()["count"], 0)
        self.assertEqual(saved_only_response.json()["source"], "contacts")

        self.assertEqual(unsaved_browse_response.status_code, 200)
        unsaved_body = unsaved_browse_response.json()
        self.assertEqual(unsaved_body["source"], "participants")
        self.assertEqual(unsaved_body["count"], 1)
        self.assertEqual(unsaved_body["participant_count"], 1)
        self.assertEqual(unsaved_body["contacts"][0]["primary_handle"], "+1 (415) 555-0199")

    def test_contacts_refresh_endpoint_runs_refresh_once(self):
        with mock.patch(
            "app.refresh_contacts_now",
            return_value={"success": True, "contacts_count": 2, "display_names_updated": 1},
        ) as mock_refresh, TestClient(app_module.app) as client:
            mock_refresh.reset_mock()
            response = client.post("/penguin-connect/contacts/refresh")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["contacts_count"], 2)
        mock_refresh.assert_called_once_with()

    def test_contacts_create_endpoint_runs_osascript_and_refreshes_cache(self):
        completed = subprocess.CompletedProcess(["osascript"], 0, stdout="person-123\n", stderr="")
        with mock.patch("app.subprocess.run", return_value=completed) as mock_run, mock.patch(
            "app.refresh_contacts_now",
            return_value={"success": True, "contacts_count": 3, "display_names_updated": 0},
        ) as mock_refresh, TestClient(app_module.app) as client:
            mock_refresh.reset_mock()
            response = client.post(
                "/penguin-connect/contacts",
                json={
                    "first_name": "Jordan",
                    "last_name": "Example",
                    "phones": ["+1 (415) 555-0100"],
                    "emails": ["jordan@example.test"],
                    "refresh_after": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["contact_id"], "person-123")
        self.assertEqual(body["refresh"]["contacts_count"], 3)
        mock_refresh.assert_called_once_with()
        script = mock_run.call_args.args[0][2]
        self.assertIn('first name:"Jordan"', script)
        self.assertIn('value:"+1 (415) 555-0100"', script)
        self.assertIn('value:"jordan@example.test"', script)

    def test_messages_draft_endpoint_copies_and_opens_messages(self):
        with mock.patch("app._copy_to_clipboard") as mock_copy, mock.patch("app._open_messages_app") as mock_open, TestClient(
            app_module.app
        ) as client:
            response = client.post(
                "/penguin-connect/messages/draft",
                json={
                    "participants": ["+14155550100", "friend@example.test"],
                    "message": "Starting this thread",
                    "copy_to_clipboard": True,
                    "open_messages": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["participants_count"], 2)
        self.assertEqual(body["draft"], "To: +14155550100, friend@example.test\n\nStarting this thread\n")
        self.assertTrue(body["copied"])
        self.assertTrue(body["opened_messages"])
        mock_copy.assert_called_once_with(body["draft"])
        mock_open.assert_called_once_with()

    def test_recipient_lists_can_be_saved_updated_listed_and_deleted(self):
        with TestClient(app_module.app) as client:
            create_response = client.post(
                "/penguin-connect/recipient-lists",
                json={
                    "name": "Demo crew",
                    "participants": ["+14155550100", "friend@example.test", "+1 (415) 555-0100"],
                    "note": "launch thread",
                },
            )
            list_response = client.get("/penguin-connect/recipient-lists")
            created = create_response.json()["recipient_list"]
            update_response = client.post(
                "/penguin-connect/recipient-lists",
                json={
                    "list_id": created["list_id"],
                    "name": "Demo crew updated",
                    "participants": ["friend@example.test", "+14155550100"],
                },
            )
            delete_response = client.delete(f"/penguin-connect/recipient-lists/{created['list_id']}")
            empty_response = client.get("/penguin-connect/recipient-lists")

        self.assertEqual(create_response.status_code, 200)
        self.assertTrue(create_response.json()["success"])
        self.assertEqual(created["name"], "Demo crew")
        self.assertEqual(created["participants"], ["+14155550100", "friend@example.test"])
        self.assertEqual(created["participants_count"], 2)
        self.assertEqual(created["note"], "launch thread")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["count"], 1)
        self.assertEqual(list_response.json()["recipient_lists"][0]["list_id"], created["list_id"])

        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()["recipient_list"]
        self.assertEqual(updated["list_id"], created["list_id"])
        self.assertEqual(updated["name"], "Demo crew updated")
        self.assertEqual(updated["participants"], ["friend@example.test", "+14155550100"])
        self.assertEqual(updated["note"], "")

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["list_id"], created["list_id"])
        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(empty_response.json()["count"], 0)

    def test_codex_ask_endpoint_runs_local_runner(self):
        with mock.patch(
            "app._run_codex_prompt",
            return_value={"success": True, "answer": "Draft reply", "prompt_chars": 42},
        ) as mock_run, TestClient(app_module.app) as client:
            response = client.post("/penguin-connect/codex/ask", json={"prompt": "Synthetic prompt"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["answer"], "Draft reply")
        mock_run.assert_called_once_with("Synthetic prompt")

    def test_codex_runner_uses_ephemeral_readonly_exec(self):
        def fake_run(command, *, input, capture_output, text, timeout):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("Synthetic answer", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with mock.patch("app.shutil.which", return_value="/usr/local/bin/codex"), mock.patch(
            "app.subprocess.run", side_effect=fake_run
        ) as mock_run:
            result = app_module._run_codex_prompt("Synthetic prompt")

        command = mock_run.call_args.args[0]
        self.assertEqual(result["answer"], "Synthetic answer")
        self.assertEqual(command[:2], ["/usr/local/bin/codex", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn("--output-last-message", command)
        self.assertIn("-", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("--ask-for-approval") + 1], "never")

    def test_ui_endpoint_serves_console_assets(self):
        with TestClient(app_module.app) as client:
            html_response = client.get("/penguin-connect/ui")
            css_response = client.get("/penguin-connect/ui/app.css")
            js_response = client.get("/penguin-connect/ui/app.js")

        self.assertEqual(html_response.status_code, 200)
        self.assertIn("PenguinConnect Console", html_response.text)
        self.assertIn("contactSearch", html_response.text)
        self.assertIn("contactSourceFilters", html_response.text)
        self.assertIn("globalMessageSearch", html_response.text)
        self.assertIn("globalMessageSearchFilters", html_response.text)
        self.assertIn("messageViewFilters", html_response.text)
        self.assertIn("stageDraftButton", html_response.text)
        self.assertIn("draftRecipientChips", html_response.text)
        self.assertIn("recipientListName", html_response.text)
        self.assertIn("saveRecipientListButton", html_response.text)
        self.assertIn("recipientLists", html_response.text)
        self.assertIn("createContactButton", html_response.text)
        self.assertIn("markReadButton", html_response.text)
        self.assertIn("connectionButton", html_response.text)
        self.assertIn("conversationFilters", html_response.text)
        self.assertIn("labelFilters", html_response.text)
        self.assertIn("bulkActions", html_response.text)
        self.assertIn("bulkLabelsInput", html_response.text)
        self.assertIn("bulkLabelButton", html_response.text)
        self.assertIn("bulkArchiveButton", html_response.text)
        self.assertIn("pinButton", html_response.text)
        self.assertIn("archiveButton", html_response.text)
        self.assertIn("threadLocalTitle", html_response.text)
        self.assertIn("threadFollowUpAt", html_response.text)
        self.assertIn("threadTags", html_response.text)
        self.assertIn("threadNote", html_response.text)
        self.assertIn("saveManagementButton", html_response.text)
        self.assertIn("replyContext", html_response.text)
        self.assertIn("codexModes", html_response.text)
        self.assertIn("codexQuestion", html_response.text)
        self.assertIn("codexAnswer", html_response.text)
        self.assertIn("askCodexButton", html_response.text)
        self.assertIn("useCodexDraftButton", html_response.text)
        self.assertIn("senderBadge", html_response.text)
        self.assertIn("threadPeople", html_response.text)
        self.assertIn("threadPeopleState", html_response.text)
        self.assertIn("threadMedia", html_response.text)
        self.assertIn("threadMediaState", html_response.text)
        self.assertIn("mediaFilters", html_response.text)
        self.assertEqual(css_response.status_code, 200)
        self.assertIn(".contact-list", css_response.text)
        self.assertIn(".contact-source-filters", css_response.text)
        self.assertIn(".favorite-contact", css_response.text)
        self.assertIn(".contact-favorite", css_response.text)
        self.assertIn(".contact-note-editor", css_response.text)
        self.assertIn(".contact-note-button", css_response.text)
        self.assertIn(".contact-add", css_response.text)
        self.assertIn(".contact-actions", css_response.text)
        self.assertIn(".contact-related", css_response.text)
        self.assertIn(".contact-thread-link", css_response.text)
        self.assertIn(".contact-create-result", css_response.text)
        self.assertIn(".draft-recipient-chip", css_response.text)
        self.assertIn(".recipient-list-tools", css_response.text)
        self.assertIn(".recipient-list-item", css_response.text)
        self.assertIn(".search-result", css_response.text)
        self.assertIn(".message-search-filters", css_response.text)
        self.assertIn(".message-view-filters", css_response.text)
        self.assertIn(".toggle-row", css_response.text)
        self.assertIn(".unread-badge", css_response.text)
        self.assertIn(".label-badge", css_response.text)
        self.assertIn(".draft-badge", css_response.text)
        self.assertIn(".followup-badge", css_response.text)
        self.assertIn(".attachment-link", css_response.text)
        self.assertIn(".audio-attachment", css_response.text)
        self.assertIn(".image-attachment", css_response.text)
        self.assertIn(".conversation-filters", css_response.text)
        self.assertIn(".label-filters", css_response.text)
        self.assertIn(".bulk-actions", css_response.text)
        self.assertIn(".bulk-label-row", css_response.text)
        self.assertIn(".bulk-label-input", css_response.text)
        self.assertIn(".conversation-select", css_response.text)
        self.assertIn(".conversation-preview", css_response.text)
        self.assertIn(".message.starred", css_response.text)
        self.assertIn(".message.noted", css_response.text)
        self.assertIn(".message-note-editor", css_response.text)
        self.assertIn(".message-actions", css_response.text)
        self.assertIn(".reply-context", css_response.text)
        self.assertIn(".thread-management", css_response.text)
        self.assertIn(".codex-modes", css_response.text)
        self.assertIn("#codexQuestion", css_response.text)
        self.assertIn("#codexAnswer", css_response.text)
        self.assertIn(".thread-people", css_response.text)
        self.assertIn(".thread-person-actions", css_response.text)
        self.assertIn(".thread-person-name", css_response.text)
        self.assertIn(".thread-person.known-contact", css_response.text)
        self.assertIn(".media-filters", css_response.text)
        self.assertIn(".thread-media", css_response.text)
        self.assertIn(".media-item", css_response.text)
        self.assertEqual(js_response.status_code, 200)
        self.assertIn("buildCodexPrompt", js_response.text)
        self.assertIn("selectedConversationContext", js_response.text)
        self.assertIn("messageSearchContext", js_response.text)
        self.assertIn("contactContext", js_response.text)
        self.assertIn("contactSources", js_response.text)
        self.assertIn("renderCodexModes", js_response.text)
        self.assertIn("askCodex", js_response.text)
        self.assertIn("useCodexAnswerAsDraft", js_response.text)
        self.assertIn("renderCodexAnswerControls", js_response.text)
        self.assertIn("Local sender", js_response.text)
        self.assertIn("conversationDisplayName", js_response.text)
        self.assertIn("sourceDisplayName", js_response.text)
        self.assertIn("conversationParticipants", js_response.text)
        self.assertIn("loadThreadContactMatches", js_response.text)
        self.assertIn("source=contacts", js_response.text)
        self.assertIn("contactMatchesHandle", js_response.text)
        self.assertIn("renderThreadPeople", js_response.text)
        self.assertIn("fillContactFormFromHandle", js_response.text)
        self.assertIn("fillContactFormFromContact", js_response.text)
        self.assertIn("renderContactSourceFilters", js_response.text)
        self.assertIn("renderContacts", js_response.text)
        self.assertIn("toggleContactFavorite", js_response.text)
        self.assertIn("saveContactNote", js_response.text)
        self.assertIn("contactNoteText", js_response.text)
        self.assertIn("contacts/management", js_response.text)
        self.assertIn("findConversationsForContact", js_response.text)
        self.assertIn("renderContactRelatedThreads", js_response.text)
        self.assertIn("openContactConversation", js_response.text)
        self.assertIn("addDraftRecipient", js_response.text)
        self.assertIn("addContactToDraft", js_response.text)
        self.assertIn("renderDraftRecipientChips", js_response.text)
        self.assertIn("removeDraftRecipient", js_response.text)
        self.assertIn("loadRecipientLists", js_response.text)
        self.assertIn("saveRecipientList", js_response.text)
        self.assertIn("useRecipientList", js_response.text)
        self.assertIn("renderMessageSearchResults", js_response.text)
        self.assertIn("renderMessageSearchFilters", js_response.text)
        self.assertIn("messageSearchViews", js_response.text)
        self.assertIn("renderMessageViewFilters", js_response.text)
        self.assertIn("messageMatchesView", js_response.text)
        self.assertIn("messagesLoading", js_response.text)
        self.assertIn("isAudioAttachment", js_response.text)
        self.assertIn("renderAudioAttachment", js_response.text)
        self.assertIn("isImageAttachment", js_response.text)
        self.assertIn("renderImageAttachment", js_response.text)
        self.assertIn("threadMediaItems", js_response.text)
        self.assertIn("mediaMatchesView", js_response.text)
        self.assertIn("renderThreadMedia", js_response.text)
        self.assertIn("focusMediaMessage", js_response.text)
        self.assertIn("renderConversationFilters", js_response.text)
        self.assertIn("renderLabelFilters", js_response.text)
        self.assertIn("conversationMatchesLabel", js_response.text)
        self.assertIn("followUpLabel", js_response.text)
        self.assertIn("follow_up_at", js_response.text)
        self.assertIn("bulkMarkSelectedRead", js_response.text)
        self.assertIn("bulkArchiveSelected", js_response.text)
        self.assertIn("bulkApplyLabels", js_response.text)
        self.assertIn("mergeConversationLabels", js_response.text)
        self.assertIn("conversationPreviewText", js_response.text)
        self.assertIn("setReplyContext", js_response.text)
        self.assertIn("toggleMessageStar", js_response.text)
        self.assertIn("isStarredMessage", js_response.text)
        self.assertIn("saveMessageNote", js_response.text)
        self.assertIn("hasMessageNote", js_response.text)
        self.assertIn("messages/management", js_response.text)
        self.assertIn("messageCopyText", js_response.text)
        self.assertIn("renderManagementFields", js_response.text)
        self.assertIn("stageDraft", js_response.text)
        self.assertIn("createContact", js_response.text)
        self.assertIn("setReadState", js_response.text)
        self.assertIn("setConversationManagement", js_response.text)
        self.assertIn("saveConversationManagement", js_response.text)
        self.assertIn("scheduleDraftSave", js_response.text)
        self.assertIn("saveLocalDraft", js_response.text)
        self.assertIn("toggleConnection", js_response.text)
        self.assertIn("attachmentUrl", js_response.text)

    def test_messages_endpoint_uses_header_display_name_for_own_gmail_messages(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, 'gmail', 'gmail-self', 'email_to_imessage', ?, ?, ?, ?, ?, 1, ?)""",
                (
                    "amc_test",
                    "owner@gmail.com",
                    "Owner <owner@gmail.com>",
                    "Re: Taylor",
                    "From me in Gmail",
                    "2026-03-11T10:00:00+00:00",
                    "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["messages"][0]["provider_message_id"], "gmail-self")
        self.assertEqual(body["messages"][0]["sender_name"], "Owner")

    def test_messages_endpoint_uses_me_for_manual_messages_without_a_display_name(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)), TestClient(app_module.app) as client:
            send_response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"sender_email": "owner@gmail.com", "message": "Hello from Gmail"},
            )
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(send_response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["messages"][0]["direction"], "manual_to_imessage")
        self.assertEqual(body["messages"][0]["sender_name"], "Me")

    def test_messages_endpoint_uses_me_for_historical_self_authored_imessage_rows(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, 'imessage', 'imsg-self', 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
                (
                    "amc_test",
                    "owner+am-test@gmail.com",
                    "Taylor",
                    "iMessage · Taylor",
                    "Sent from Messages",
                    "2026-03-11T11:00:00+00:00",
                    '{"is_from_me": true}',
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-self")
        self.assertEqual(body["messages"][0]["sender_name"], "Me")

    def test_alias_endpoint_returns_not_found_for_unknown_conversation(self):
        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc-missing/alias")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "conversation_not_found")

    def test_send_endpoint_rejects_sender_not_connected_to_gmail(self):
        with TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"sender_email": "attacker@example.com", "message": "blocked"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "sender_not_connected_gmail")

        conn = self._get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM penguin_connect_messages WHERE direction = 'manual_to_imessage'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0)

    def test_send_endpoint_persists_manual_message_for_verified_sender(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send, TestClient(
            app_module.app
        ) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"sender_email": "owner@gmail.com", "message": "Hello from Gmail"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_send.assert_called_once_with("chat-123", "Hello from Gmail", attachment_paths=None)

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT sender_email, sender_name, body_text, direction
                   FROM penguin_connect_messages
                   WHERE direction = 'manual_to_imessage'"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["sender_email"], "owner@gmail.com")
        self.assertEqual(row["sender_name"], "Me")
        self.assertEqual(row["body_text"], "Hello from Gmail")
        self.assertEqual(row["direction"], "manual_to_imessage")

    def test_send_endpoint_defaults_to_local_conversation_sender(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send, TestClient(
            app_module.app
        ) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"message": "Hello from local console"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_send.assert_called_once_with("chat-123", "Hello from local console", attachment_paths=None)

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT sender_email, sender_name, body_text, direction
                   FROM penguin_connect_messages
                   WHERE direction = 'manual_to_imessage'"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["sender_email"], "owner@gmail.com")
        self.assertEqual(row["sender_name"], "Me")
        self.assertEqual(row["body_text"], "Hello from local console")
        self.assertEqual(row["direction"], "manual_to_imessage")

    def test_send_endpoint_forwards_attachment_paths(self):
        with mock.patch("app.refresh_contacts_now", return_value={"success": True}), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={
                    "sender_email": "owner@gmail.com",
                    "message": "",
                    "attachment_paths": ["/tmp/voice-memo.m4a"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_send.assert_called_once_with("chat-123", "", attachment_paths=["/tmp/voice-memo.m4a"])

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT body_text, metadata
                   FROM penguin_connect_messages
                   WHERE direction = 'manual_to_imessage'"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["body_text"], "")
        self.assertEqual(json.loads(row["metadata"])["manual_attachment_count"], 1)

    def test_send_endpoint_stages_browser_attachments(self):
        captured_paths = []

        def fake_send(_chat_id, _message, attachment_paths=None):
            captured_paths.extend(attachment_paths or [])
            self.assertEqual(Path(captured_paths[0]).read_bytes(), b"fake-image")
            return True, None

        encoded = base64.b64encode(b"fake-image").decode("ascii")
        with mock.patch("app.refresh_contacts_now", return_value={"success": True}), mock.patch(
            "penguin_connect.send_imessage",
            side_effect=fake_send,
        ), TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={
                    "sender_email": "owner@gmail.com",
                    "message": "see attached",
                    "attachments": [
                        {
                            "filename": "photo.png",
                            "mime_type": "image/png",
                            "size": len(b"fake-image"),
                            "data_base64": encoded,
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(len(captured_paths), 1)
        self.assertFalse(Path(captured_paths[0]).exists())


if __name__ == "__main__":
    unittest.main()
