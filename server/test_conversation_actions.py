import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
from db import SCHEMA


class ConversationActionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "cache.db"
        conn = self._get_connection()
        try:
            conn.executescript(SCHEMA)
            conn.execute(
                """INSERT INTO penguin_connect_accounts
                   (gmail_email, keychain_service, send_as_aliases, status)
                   VALUES ('owner@example.test', 'test-keychain', '[]', 'connected')"""
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_provider, source_chat_id,
                    display_name, chat_type, participants, status)
                   VALUES ('owner@example.test', 'conversation_test', 'slack', 'channel-1',
                           'Synthetic project', 'channel', '[]', 'active')"""
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
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def test_manual_action_can_be_created_and_listed(self):
        with TestClient(app_module.app) as client:
            create_response = client.post(
                "/penguin-connect/conversations/conversation_test/actions",
                json={
                    "action_type": "email_sent",
                    "summary": "Sent the requested follow-up email",
                    "occurred_at": "2026-08-04T17:30:00+00:00",
                },
            )
            list_response = client.get(
                "/penguin-connect/conversations/conversation_test/actions"
            )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()["action"]
        self.assertEqual(created["action_type"], "email_sent")
        self.assertEqual(created["source"], "manual")
        self.assertEqual(created["summary"], "Sent the requested follow-up email")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(
            [row["action_id"] for row in list_response.json()["actions"]],
            [created["action_id"]],
        )

    def test_manual_action_requires_a_summary(self):
        with TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/conversation_test/actions",
                json={"action_type": "manual_note", "summary": "   "},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "action_summary_required")

    def test_follow_up_changes_are_logged_once(self):
        payload = {"follow_up_at": "2026-08-07T16:00:00+00:00"}
        with TestClient(app_module.app) as client:
            first_response = client.post(
                "/penguin-connect/conversations/conversation_test/management",
                json=payload,
            )
            second_response = client.post(
                "/penguin-connect/conversations/conversation_test/management",
                json=payload,
            )
            actions_response = client.get(
                "/penguin-connect/conversations/conversation_test/actions"
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        actions = actions_response.json()["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "follow_up_set")
        self.assertEqual(actions[0]["detail"], payload["follow_up_at"])

    def test_successful_agent_suggested_send_is_logged_without_message_body(self):
        with mock.patch(
            "app.penguinconnect_send_manual_message",
            return_value={"success": True, "provider_message_id": "slack-message-1"},
        ):
            with TestClient(app_module.app) as client:
                send_response = client.post(
                    "/penguin-connect/conversations/conversation_test/send",
                    json={
                        "message": "Private message content",
                        "action_source": "penguin_agent",
                    },
                )
                actions_response = client.get(
                    "/penguin-connect/conversations/conversation_test/actions"
                )

        self.assertEqual(send_response.status_code, 200)
        actions = actions_response.json()["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "message_sent")
        self.assertEqual(actions[0]["source"], "penguin_agent")
        self.assertEqual(actions[0]["detail"], "Slack")
        self.assertNotIn("Private message content", str(actions))


if __name__ == "__main__":
    unittest.main()
