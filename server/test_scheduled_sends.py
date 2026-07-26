import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
from db import SCHEMA


class ScheduledSendTests(unittest.TestCase):
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
                    '["owner@gmail.com"]',
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_provider, source_chat_id, display_name,
                    chat_type, participants, alias_email, status)
                   VALUES (?, ?, 'imessage', ?, ?, 'dm', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_test",
                    "chat-123",
                    "Taylor",
                    '["+14155550101"]',
                    "owner+am-test@gmail.com",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        self.patchers = [
            mock.patch("app.get_connection", side_effect=self._get_connection),
            mock.patch("app.init_db"),
            mock.patch("app.assert_startup_ready", return_value={"ok": True}),
            mock.patch("app.refresh_contacts_now", return_value={"success": True}),
            mock.patch("app.start_watchers"),
            mock.patch("app.stop_watchers"),
            mock.patch("app.start_scheduled_send_worker"),
            mock.patch("app.stop_scheduled_send_worker"),
            mock.patch(
                "app.penguinconnect_run_startup_catchup",
                return_value={"success": True, "skipped": True, "reason": "queue_idle"},
            ),
            mock.patch("app.log_action"),
            mock.patch("builtins.print"),
            mock.patch("app.action_log_path", return_value=Path(self.tmpdir.name) / "actions.jsonl"),
            mock.patch.object(app_module, "DB_PATH", self.db_path),
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

    def test_schedule_list_and_cancel_message(self):
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        with TestClient(app_module.app) as client:
            create_response = client.post(
                "/penguin-connect/conversations/amc_test/scheduled-messages",
                json={"message": "See you later", "scheduled_at": scheduled_at},
            )
            self.assertEqual(create_response.status_code, 200)
            created = create_response.json()["scheduled_message"]
            self.assertEqual(created["status"], "scheduled")
            self.assertEqual(created["message"], "See you later")
            scheduled_id = created["scheduled_id"]

            list_response = client.get("/penguin-connect/conversations/amc_test/scheduled-messages")
            self.assertEqual(list_response.status_code, 200)
            rows = list_response.json()["scheduled_messages"]
            self.assertEqual([row["scheduled_id"] for row in rows], [scheduled_id])

            cancel_response = client.post(f"/penguin-connect/scheduled-messages/{scheduled_id}/cancel", json={})
            self.assertEqual(cancel_response.status_code, 200)
            self.assertEqual(cancel_response.json()["scheduled_message"]["status"], "cancelled")

    def test_run_due_scheduled_message_uses_manual_send_path(self):
        due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_scheduled_messages
                   (scheduled_id, conversation_id, sender_email, body_text, attachment_paths,
                    scheduled_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'scheduled')""",
                (
                    "scheduled_test",
                    "amc_test",
                    "",
                    "Due now",
                    json.dumps(["/tmp/penguin-connect-test.txt"]),
                    due_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "app.penguinconnect_send_manual_message",
            return_value={"success": True, "provider_message_id": "manual-1"},
        ) as mock_send, TestClient(app_module.app) as client:
            response = client.post("/penguin-connect/scheduled-messages/run-due")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed"], 1)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["conversation_id"], "amc_test")
        self.assertEqual(mock_send.call_args.kwargs["body_text"], "Due now")
        self.assertEqual(mock_send.call_args.kwargs["attachment_paths"], ["/tmp/penguin-connect-test.txt"])

        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT status, provider_message_id FROM penguin_connect_scheduled_messages WHERE scheduled_id = ?",
                ("scheduled_test",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["provider_message_id"], "manual-1")

    def test_scheduled_thread_reply_preserves_parent_until_delivery(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_provider, source_chat_id, display_name,
                    chat_type, participants, status)
                   VALUES (?, ?, 'slack', ?, ?, 'channel', ?, 'active')""",
                (
                    "owner@gmail.com",
                    "slack_thread_test",
                    "C_PRODUCT",
                    "#product",
                    '["U_ANH"]',
                ),
            )
            conn.commit()
        finally:
            conn.close()
        scheduled_at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        with TestClient(app_module.app) as client:
            create_response = client.post(
                "/penguin-connect/conversations/slack_thread_test/scheduled-messages",
                json={
                    "message": "Nested reply",
                    "scheduled_at": scheduled_at,
                    "reply_to_message_id": "slack:1785000001.000100",
                },
            )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()["scheduled_message"]
        self.assertEqual(
            created["reply_to_message_id"],
            "slack:1785000001.000100",
        )
        conn = self._get_connection()
        try:
            conn.execute(
                """UPDATE penguin_connect_scheduled_messages
                   SET scheduled_at = ?
                   WHERE scheduled_id = ?""",
                (
                    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                    created["scheduled_id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "app.penguinconnect_send_manual_message",
            return_value={"success": True, "provider_message_id": "manual-thread-reply"},
        ) as mock_send:
            result = app_module.run_due_scheduled_messages()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(
            mock_send.call_args.kwargs["reply_to_message_id"],
            "slack:1785000001.000100",
        )

    def test_thread_reply_is_rejected_for_non_slack_conversation(self):
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/scheduled-messages",
                json={
                    "message": "Not a Slack thread",
                    "scheduled_at": scheduled_at,
                    "reply_to_message_id": "slack:1785000001.000100",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "thread_replies_only_supported_for_slack",
        )


if __name__ == "__main__":
    unittest.main()
