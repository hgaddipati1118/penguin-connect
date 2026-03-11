import sqlite3
import tempfile
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
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
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
            "app.penguinconnect_get_sync_metrics",
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

    def test_messages_endpoint_respects_limit_and_returns_latest_first(self):
        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["found"])
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-latest")
        self.assertEqual(body["messages"][0]["body_text"], "Latest message")

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


if __name__ == "__main__":
    unittest.main()
