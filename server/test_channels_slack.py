import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import penguin_connect
from channels.slack import SlackChannelAdapter, _slack_token
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

    def test_fetches_thread_replies_with_author_and_parent_metadata(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {
            "USELF": "Harsha",
            "UANH": "Anh",
            "UDHRUV": "Dhruv",
        }
        adapter._user_avatars = {
            "USELF": "https://cdn.example.test/harsha.png",
            "UANH": "https://cdn.example.test/anh.png",
            "UDHRUV": "https://cdn.example.test/dhruv.png",
        }

        def fake_api(method, **kwargs):
            if method == "conversations.history":
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Root question",
                            "reply_count": 2,
                            "reply_users_count": 2,
                            "latest_reply": "1785000003.000100",
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "conversations.replies":
                self.assertEqual(kwargs["params"]["channel"], "C_PRODUCT")
                self.assertEqual(kwargs["params"]["ts"], "1785000001.000100")
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Root question",
                            "reply_count": 2,
                        },
                        {
                            "ts": "1785000002.000100",
                            "thread_ts": "1785000001.000100",
                            "parent_user_id": "UANH",
                            "user": "UDHRUV",
                            "text": "First reply",
                        },
                        {
                            "ts": "1785000003.000100",
                            "thread_ts": "1785000001.000100",
                            "parent_user_id": "UANH",
                            "user": "USELF",
                            "text": "Second reply",
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            messages = adapter.fetch_messages("C_PRODUCT", limit=50)

        self.assertEqual(
            [message["text"] for message in messages],
            ["Root question", "First reply", "Second reply"],
        )
        self.assertEqual(messages[0]["reply_count"], 2)
        self.assertFalse(messages[0]["is_thread_reply"])
        self.assertEqual(messages[1]["thread_ts"], "1785000001.000100")
        self.assertTrue(messages[1]["is_thread_reply"])
        self.assertEqual(messages[1]["thread_parent_name"], "Anh")
        self.assertEqual(messages[1]["push_name"], "Dhruv")
        self.assertEqual(
            messages[1]["sender_avatar_url"],
            "https://cdn.example.test/dhruv.png",
        )
        self.assertTrue(messages[2]["is_from_me"])

    def test_refreshes_history_without_refetching_unchanged_slack_threads(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {"USELF": "Harsha", "UANH": "Anh"}
        history_calls = 0
        reply_calls = 0

        def fake_api(method, **_kwargs):
            nonlocal history_calls, reply_calls
            if method == "conversations.history":
                history_calls += 1
                latest_reply = (
                    "1785000003.000100"
                    if history_calls >= 3
                    else "1785000002.000100"
                )
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Root question",
                            "reply_count": 2 if history_calls >= 3 else 1,
                            "latest_reply": latest_reply,
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "conversations.replies":
                reply_calls += 1
                replies = [
                    {
                        "ts": "1785000001.000100",
                        "user": "UANH",
                        "text": "Root question",
                    },
                    {
                        "ts": "1785000002.000100",
                        "thread_ts": "1785000001.000100",
                        "user": "USELF",
                        "text": "First reply",
                    },
                ]
                if history_calls >= 3:
                    replies.append(
                        {
                            "ts": "1785000003.000100",
                            "thread_ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "New reply",
                        }
                    )
                return {
                    "ok": True,
                    "messages": replies,
                    "response_metadata": {"next_cursor": ""},
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            first = adapter.fetch_messages("C_PRODUCT", limit=50)
            adapter._history_cache.clear()
            second = adapter.fetch_messages("C_PRODUCT", limit=50)
            adapter._history_cache.clear()
            third = adapter.fetch_messages("C_PRODUCT", limit=50)

        self.assertEqual(history_calls, 3)
        self.assertEqual(reply_calls, 2)
        self.assertEqual(second, first)
        self.assertEqual(third[-1]["text"], "New reply")

    def test_sends_reply_into_existing_slack_thread(self):
        adapter = SlackChannelAdapter()

        def fake_api(method, **kwargs):
            self.assertEqual(method, "chat.postMessage")
            self.assertEqual(
                kwargs["json_body"],
                {
                    "channel": "C_PRODUCT",
                    "text": "Replying here",
                    "thread_ts": "1785000001.000100",
                },
            )
            return {"ok": True, "ts": "1785000004.000100"}

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            result = adapter.send_message(
                "C_PRODUCT",
                "Replying here",
                reply_to_message_id="slack:1785000001.000100",
            )

        self.assertEqual(result, (True, None))

    def test_resolves_missing_slack_user_once_with_users_info(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        user_info_calls = 0

        def fake_api(method, **kwargs):
            nonlocal user_info_calls
            if method == "users.list":
                return {"ok": True, "members": [], "response_metadata": {"next_cursor": ""}}
            if method == "conversations.history":
                return {
                    "ok": True,
                    "messages": [
                        {"ts": "1785000002.000100", "user": "UEXTERNAL", "text": "Second"},
                        {"ts": "1785000001.000100", "user": "UEXTERNAL", "text": "First"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "users.info":
                user_info_calls += 1
                self.assertEqual(kwargs["params"], {"user": "UEXTERNAL"})
                return {
                    "ok": True,
                    "user": {
                        "id": "UEXTERNAL",
                        "profile": {"display_name": "External guest"},
                    },
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            messages = adapter.fetch_messages("C_PRODUCT", limit=15)

        self.assertEqual(
            [message["push_name"] for message in messages],
            ["External guest", "External guest"],
        )
        self.assertEqual(user_info_calls, 1)

    def test_reuses_recent_workspace_discovery(self):
        adapter = SlackChannelAdapter()

        def fake_api(method, **_kwargs):
            if method == "auth.test":
                return {"ok": True, "user_id": "USELF", "team": "Slashy"}
            if method == "users.list":
                return {"ok": True, "members": [], "response_metadata": {"next_cursor": ""}}
            if method == "conversations.list":
                return {
                    "ok": True,
                    "channels": [{"id": "C_PRODUCT", "name": "product"}],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "search.messages":
                return {"ok": True, "messages": {"matches": [], "pagination": {}}}
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api) as api:
            first = adapter.list_conversations(limit=100)
            second = adapter.list_conversations(limit=100)

        self.assertEqual(second, first)
        self.assertEqual(api.call_count, 4)

    def test_is_gracefully_unavailable_without_token(self):
        adapter = SlackChannelAdapter()
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_SLACK_TOKEN": ""},
            clear=False,
        ), mock.patch("channels.slack._read_slack_keychain_token", return_value=""):
            self.assertFalse(adapter.list_conversations()["available"])
            self.assertEqual(adapter.fetch_messages("C_PRODUCT"), [])

    def test_reads_user_token_from_keychain_when_env_is_unset(self):
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_SLACK_TOKEN": ""},
            clear=False,
        ), mock.patch(
            "channels.slack._read_slack_keychain_token",
            return_value="xoxp-keychain-token",
        ):
            self.assertEqual(_slack_token(), "xoxp-keychain-token")

    def test_environment_token_takes_precedence_over_keychain(self):
        with mock.patch(
            "channels.slack._read_slack_keychain_token"
        ) as read_keychain:
            self.assertEqual(_slack_token(), "xoxp-test-token")
        read_keychain.assert_not_called()


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

    def test_manual_thread_reply_dispatches_to_slack_and_persists_thread_metadata(self):
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, source_chat_id,
                source_chat_identifier, source_service_name, display_name, chat_type,
                participants, status, exclude_from_sync)
               VALUES ('owner@gmail.com', 'slack', 'slack-thread', 'C_PRODUCT',
                       'C_PRODUCT', 'Slack', '#product', 'channel', '[]', 'active', 0)"""
        )
        self.conn.commit()
        channel = mock.Mock()
        channel.send_message.return_value = (True, None)

        with mock.patch.object(penguin_connect, "_SLACK_CHANNEL", channel), mock.patch(
            "penguin_connect._source_adapter_for_provider",
            return_value=channel,
        ), mock.patch(
            "penguin_connect.refresh_conversation_exclusions"
        ), mock.patch("penguin_connect.log_action"):
            result = penguin_connect.send_manual_message(
                self.conn,
                conversation_id="slack-thread",
                body_text="Nested reply",
                reply_to_message_id="slack:1785000001.000100",
            )

        self.assertTrue(result["success"])
        channel.send_message.assert_called_once_with(
            "C_PRODUCT",
            "Nested reply",
            attachment_paths=None,
            reply_to_message_id="slack:1785000001.000100",
        )
        row = self.conn.execute(
            """SELECT metadata
               FROM penguin_connect_messages
               WHERE conversation_id = 'slack-thread'"""
        ).fetchone()
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["thread_ts"], "1785000001.000100")
        self.assertTrue(metadata["is_thread_reply"])


if __name__ == "__main__":
    unittest.main()
