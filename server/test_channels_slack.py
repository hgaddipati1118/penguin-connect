import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import penguin_connect
from channels.slack import SlackChannelAdapter
from db import SCHEMA


class SlackChannelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_SLACK_TOKEN": "xoxp-test-token",
                "PENGUIN_CONNECT_SLACK_STATE_PATH": str(Path(self.tmpdir.name) / "slack-state.json"),
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmpdir.cleanup()

    def test_lists_channels_with_recent_message_seed(self):
        adapter = SlackChannelAdapter()

        def fake_api(method, **kwargs):
            if method == "auth.test":
                return {"ok": True, "user_id": "USELF", "team": "Slashy"}
            if method == "users.list":
                return {
                    "ok": True,
                    "members": [
                        {"id": "USELF", "profile": {"display_name": "Harsha"}},
                        {"id": "UANH", "profile": {"display_name": "Anh"}},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "conversations.list":
                return {
                    "ok": True,
                    "channels": [
                        {"id": "C_PRODUCT", "name": "product", "name_normalized": "product"},
                        {"id": "DANH", "is_im": True, "user": "UANH"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "search.messages":
                return {
                    "ok": True,
                    "messages": {
                        "matches": [
                            {
                                "channel": {"id": "C_PRODUCT"},
                                "ts": "1785000000.000100",
                                "user": "UANH",
                                "text": "Ready for <@USELF>",
                            }
                        ],
                        "pagination": {"next_cursor": ""},
                    },
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            result = adapter.list_conversations(limit=100)

        self.assertTrue(result["available"])
        self.assertEqual(result["workspace"], "Slashy")
        self.assertEqual(result["chats"][0]["name"], "#product")
        self.assertEqual(result["chats"][0]["latest_message"]["text"], "Ready for @Harsha")
        self.assertEqual(result["chats"][1]["name"], "Anh")
        self.assertTrue(Path(os.environ["PENGUIN_CONNECT_SLACK_STATE_PATH"]).exists())

    def test_fetches_and_sends_channel_messages(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {"USELF": "Harsha", "UANH": "Anh"}
        history_calls = 0

        def fake_api(method, **kwargs):
            nonlocal history_calls
            if method == "conversations.history":
                history_calls += 1
                return {
                    "ok": True,
                    "messages": [
                        {"ts": "1785000002.000100", "user": "USELF", "text": "Done"},
                        {"ts": "1785000001.000100", "user": "UANH", "text": "Can you check?"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "chat.postMessage":
                self.assertEqual(kwargs["json_body"], {"channel": "C_PRODUCT", "text": "Shipping"})
                return {"ok": True, "ts": "1785000003.000100"}
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            messages = adapter.fetch_messages("C_PRODUCT", limit=15)
            cached_messages = adapter.fetch_messages("C_PRODUCT", limit=15)
            sent = adapter.send_message("C_PRODUCT", "Shipping")

        self.assertEqual([message["text"] for message in messages], ["Can you check?", "Done"])
        self.assertEqual(cached_messages, messages)
        self.assertEqual(history_calls, 1)
        self.assertFalse(messages[0]["is_from_me"])
        self.assertTrue(messages[1]["is_from_me"])
        self.assertEqual(sent, (True, None))

    def test_is_gracefully_unavailable_without_token(self):
        adapter = SlackChannelAdapter()
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_SLACK_TOKEN": ""}, clear=False):
            self.assertFalse(adapter.list_conversations()["available"])
            self.assertEqual(adapter.fetch_messages("C_PRODUCT"), [])


class SlackBridgeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES ('owner@gmail.com', 'test', '[]', 'connected')"""
        )

    def tearDown(self):
        self.conn.close()

    def test_discovery_persists_channel_and_preview(self):
        channel = mock.Mock()
        channel.list_conversations.return_value = {
            "available": True,
            "workspace": "Slashy",
            "chats": [
                {
                    "chat_id": "C_PRODUCT",
                    "name": "#product",
                    "chat_type": "channel",
                    "participants": ["U_ANH"],
                    "latest_message": {
                        "native_message_id": "1785000000.000100",
                        "timestamp": "2026-07-25T12:00:00+00:00",
                        "text": "Ready to ship",
                        "handle": "U_ANH",
                        "push_name": "Anh",
                        "is_from_me": False,
                        "attachments": [],
                    },
                }
            ],
        }
        with mock.patch.object(penguin_connect, "_SLACK_CHANNEL", channel), mock.patch(
            "penguin_connect.log_action"
        ):
            count = penguin_connect.ensure_slack_conversations_discovered(
                self.conn,
                "owner@gmail.com",
            )

        self.assertEqual(count, 1)
        conversation = self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE source_provider = 'slack'"
        ).fetchone()
        self.assertEqual(conversation["display_name"], "#product")
        message = self.conn.execute(
            "SELECT * FROM penguin_connect_messages WHERE conversation_id = ?",
            (conversation["conversation_id"],),
        ).fetchone()
        self.assertEqual(message["provider_message_id"], "slack:1785000000.000100")
        self.assertEqual(message["body_text"], "Ready to ship")
        self.assertEqual(message["sender_name"], "Anh")


if __name__ == "__main__":
    unittest.main()
