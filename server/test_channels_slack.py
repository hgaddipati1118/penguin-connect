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
                "PENGUIN_CONNECT_SLACK_ATTACHMENT_DIR": str(
                    Path(self.tmpdir.name) / "slack-attachments"
                ),
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

    def test_downloads_private_file_once_and_reuses_local_cache(self):
        adapter = SlackChannelAdapter()
        response = mock.MagicMock()
        response.headers = {"content-length": "15"}
        response.iter_bytes.return_value = [b"synthetic-image"]
        response.raise_for_status.return_value = None
        stream_context = mock.MagicMock()
        stream_context.__enter__.return_value = response
        client = mock.MagicMock()
        client.stream.return_value = stream_context
        client_context = mock.MagicMock()
        client_context.__enter__.return_value = client
        remote_url = (
            "https://files.slack.com/files-pri/T_TEST-F_TEST/download/screenshot.png"
        )

        with mock.patch("channels.slack.httpx.Client", return_value=client_context) as client_factory:
            first = adapter.download_attachment(
                remote_url,
                "F_TEST",
                "Screenshot 2026-07-26.png",
            )
            second = adapter.download_attachment(
                remote_url,
                "F_TEST",
                "Screenshot 2026-07-26.png",
            )

        self.assertEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), b"synthetic-image")
        self.assertEqual(client_factory.call_count, 1)
        self.assertEqual(
            client_factory.call_args.kwargs["headers"],
            {"Authorization": "Bearer xoxp-test-token"},
        )
        client.stream.assert_called_once_with("GET", remote_url)

    def test_rejects_non_slack_private_file_url_without_network_access(self):
        adapter = SlackChannelAdapter()

        with mock.patch("channels.slack.httpx.Client") as client_factory:
            result = adapter.download_attachment(
                "https://example.com/private/file.png",
                "F_TEST",
                "file.png",
            )

        self.assertIsNone(result)
        client_factory.assert_not_called()

    def test_empty_incremental_fetch_preserves_latest_history_checkpoint(self):
        state_path = Path(os.environ["PENGUIN_CONNECT_SLACK_STATE_PATH"])
        state_path.write_text(json.dumps({
            "last_history_channel": "C_PRODUCT",
            "last_history_message_at": "2026-07-26T10:36:42+00:00",
            "workspace": "Slashy",
        }))
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._workspace_name = "Slashy"

        with mock.patch.object(
            adapter,
            "_api",
            return_value={
                "ok": True,
                "messages": [],
                "response_metadata": {"next_cursor": ""},
            },
        ):
            self.assertEqual(
                adapter.fetch_messages(
                    "C_PRODUCT",
                    limit=15,
                    since="2026-07-26T10:36:42+00:00",
                ),
                [],
            )

        state = json.loads(state_path.read_text())
        self.assertEqual(
            state["last_history_message_at"],
            "2026-07-26T10:36:42+00:00",
        )
        self.assertNotIn("updated_at", state)

    def test_new_slack_message_advances_latest_history_checkpoint(self):
        state_path = Path(os.environ["PENGUIN_CONNECT_SLACK_STATE_PATH"])
        state_path.write_text(json.dumps({
            "last_history_channel": "C_PRODUCT",
            "last_history_message_at": "2026-07-26T10:36:42+00:00",
            "workspace": "Slashy",
        }))
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._workspace_name = "Slashy"

        with mock.patch.object(
            adapter,
            "_api",
            return_value={
                "ok": True,
                "messages": [{
                    "ts": "1785062263.000100",
                    "user": "UANH",
                    "text": "New activity",
                }],
                "response_metadata": {"next_cursor": ""},
            },
        ):
            messages = adapter.fetch_messages(
                "C_PRODUCT",
                limit=15,
                since="2026-07-26T10:36:42+00:00",
            )

        self.assertEqual([message["text"] for message in messages], ["New activity"])
        state = json.loads(state_path.read_text())
        self.assertEqual(
            state["last_history_message_at"],
            messages[0]["timestamp"],
        )
        self.assertIn("updated_at", state)

    def test_lists_channel_participants_with_native_ids_and_caches_the_result(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {
            "USELF": "Harsha",
            "UANH": "Anh",
            "UDHRUV": "Dhruv",
        }
        adapter._user_avatars = {
            "UANH": "https://cdn.example.test/anh.png",
        }
        member_calls = 0

        def fake_api(method, **kwargs):
            nonlocal member_calls
            if method != "conversations.members":
                raise AssertionError(method)
            member_calls += 1
            self.assertEqual(kwargs["params"]["channel"], "C_PRODUCT")
            if not kwargs["params"].get("cursor"):
                return {
                    "ok": True,
                    "members": ["USELF", "UANH"],
                    "response_metadata": {"next_cursor": "page-2"},
                }
            self.assertEqual(kwargs["params"]["cursor"], "page-2")
            return {
                "ok": True,
                "members": ["UDHRUV", "UANH"],
                "response_metadata": {"next_cursor": ""},
            }

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            first = adapter.list_participants("C_PRODUCT")
            second = adapter.list_participants("C_PRODUCT")

        self.assertTrue(first["available"])
        self.assertEqual(first["channel_id"], "C_PRODUCT")
        self.assertEqual(first["participants"], [
            {
                "id": "UANH",
                "display_name": "Anh",
                "avatar_url": "https://cdn.example.test/anh.png",
                "is_self": False,
            },
            {
                "id": "UDHRUV",
                "display_name": "Dhruv",
                "avatar_url": "",
                "is_self": False,
            },
            {
                "id": "USELF",
                "display_name": "Harsha",
                "avatar_url": "",
                "is_self": True,
            },
        ])
        self.assertEqual(second, first)
        self.assertEqual(member_calls, 2)

    def test_normalizes_slack_reactions_with_counts_and_own_state(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {"USELF": "Owner", "UANH": "Teammate"}

        def fake_api(method, **_kwargs):
            if method == "conversations.history":
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Ready to ship",
                            "reactions": [
                                {
                                    "name": "+1",
                                    "count": 2,
                                    "users": ["USELF", "UANH"],
                                },
                                {
                                    "name": "custom_penguin",
                                    "count": 1,
                                    "users": ["UANH"],
                                },
                            ],
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            messages = adapter.fetch_messages("C_PRODUCT", limit=15)

        self.assertEqual(
            messages[0]["provider_reactions"],
            [
                {
                    "name": "+1",
                    "emoji": "👍",
                    "count": 2,
                    "reacted_by_me": True,
                },
                {
                    "name": "custom_penguin",
                    "emoji": ":custom_penguin:",
                    "count": 1,
                    "reacted_by_me": False,
                },
            ],
        )

    def test_adds_and_removes_native_slack_reactions_idempotently(self):
        adapter = SlackChannelAdapter()
        calls = []

        def fake_api(method, **kwargs):
            calls.append((method, kwargs))
            if method == "reactions.add":
                return {"ok": False, "error": "already_reacted"}
            if method == "reactions.remove":
                return {"ok": False, "error": "no_reaction"}
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            added = adapter.set_reaction(
                "C_PRODUCT",
                "slack:1785000001.000100",
                "👍",
            )
            removed = adapter.set_reaction(
                "C_PRODUCT",
                "1785000001.000100",
                "+1",
                remove=True,
            )

        self.assertEqual(added, (True, None))
        self.assertEqual(removed, (True, None))
        self.assertEqual(
            calls,
            [
                (
                    "reactions.add",
                    {
                        "json_body": {
                            "channel": "C_PRODUCT",
                            "timestamp": "1785000001.000100",
                            "name": "+1",
                        }
                    },
                ),
                (
                    "reactions.remove",
                    {
                        "json_body": {
                            "channel": "C_PRODUCT",
                            "timestamp": "1785000001.000100",
                            "name": "+1",
                        }
                    },
                ),
            ],
        )

    def test_rejects_invalid_slack_reaction_before_calling_api(self):
        adapter = SlackChannelAdapter()
        with mock.patch.object(adapter, "_api") as mock_api:
            result = adapter.set_reaction(
                "C_PRODUCT",
                "slack:1785000001.000100",
                "not a valid reaction",
            )

        self.assertEqual(result, (False, "slack_invalid_reaction"))
        mock_api.assert_not_called()

    def test_edits_and_deletes_native_slack_messages(self):
        adapter = SlackChannelAdapter()
        calls = []

        def fake_api(method, **kwargs):
            calls.append((method, kwargs))
            if method == "chat.update":
                return {"ok": True, "ts": "1785000001.000100"}
            if method == "chat.delete":
                return {"ok": True, "ts": "1785000001.000100"}
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            edited = adapter.edit_message(
                "C_PRODUCT",
                "slack:1785000001.000100",
                "  Updated synthetic message  ",
            )
            deleted = adapter.delete_message(
                "C_PRODUCT",
                "1785000001.000100",
            )

        self.assertEqual(edited, (True, None))
        self.assertEqual(deleted, (True, None))
        self.assertEqual(
            calls,
            [
                (
                    "chat.update",
                    {
                        "json_body": {
                            "channel": "C_PRODUCT",
                            "ts": "1785000001.000100",
                            "text": "Updated synthetic message",
                        }
                    },
                ),
                (
                    "chat.delete",
                    {
                        "json_body": {
                            "channel": "C_PRODUCT",
                            "ts": "1785000001.000100",
                        }
                    },
                ),
            ],
        )

    def test_rejects_invalid_slack_message_mutations_before_api_calls(self):
        adapter = SlackChannelAdapter()
        with mock.patch.object(adapter, "_api") as mock_api:
            missing_text = adapter.edit_message(
                "C_PRODUCT",
                "1785000001.000100",
                "   ",
            )
            missing_target = adapter.delete_message("", "")

        self.assertEqual(missing_text, (False, "slack_edit_text_required"))
        self.assertEqual(missing_target, (False, "slack_message_target_required"))
        mock_api.assert_not_called()

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

    def test_scans_requested_history_window_for_older_active_thread_roots(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {"USELF": "Harsha", "UANH": "Anh"}
        reply_calls = 0

        def fake_api(method, **kwargs):
            nonlocal reply_calls
            if method == "conversations.history":
                self.assertEqual(kwargs["params"]["limit"], 50)
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Older active thread",
                            "reply_count": 1,
                            "latest_reply": "1785000050.000100",
                        },
                        *[
                            {
                                "ts": f"17850000{index:02d}.000100",
                                "user": "USELF",
                                "text": f"Standalone {index}",
                            }
                            for index in range(2, 21)
                        ],
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "conversations.replies":
                reply_calls += 1
                self.assertEqual(kwargs["params"]["ts"], "1785000001.000100")
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Older active thread",
                        },
                        {
                            "ts": "1785000050.000100",
                            "thread_ts": "1785000001.000100",
                            "parent_user_id": "UANH",
                            "user": "USELF",
                            "text": "Fresh nested reply",
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            messages = adapter.fetch_messages("C_PRODUCT", limit=50)

        self.assertEqual(reply_calls, 1)
        self.assertIn("Fresh nested reply", [message["text"] for message in messages])

    def test_keeps_thread_root_when_tail_window_contains_its_reply(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {"USELF": "Harsha", "UANH": "Anh"}

        def fake_api(method, **_kwargs):
            if method == "conversations.history":
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Thread root",
                            "reply_count": 1,
                            "latest_reply": "1785000002.000100",
                        },
                        {
                            "ts": "1785000003.000100",
                            "user": "USELF",
                            "text": "New standalone",
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "conversations.replies":
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "UANH",
                            "text": "Thread root",
                        },
                        {
                            "ts": "1785000002.000100",
                            "thread_ts": "1785000001.000100",
                            "parent_user_id": "UANH",
                            "user": "USELF",
                            "text": "Nested reply",
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            messages = adapter.fetch_messages("C_PRODUCT", limit=2)

        self.assertEqual(
            [message["text"] for message in messages],
            ["Thread root", "Nested reply", "New standalone"],
        )

    def test_keeps_attachment_only_slack_thread_reply_with_fallback_text(self):
        adapter = SlackChannelAdapter()
        adapter._self_user_id = "USELF"
        adapter._users = {"USELF": "Harsha", "ULINEAR": "Linear"}

        def fake_api(method, **_kwargs):
            if method == "conversations.history":
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "USELF",
                            "text": "Please file this",
                            "reply_count": 1,
                            "latest_reply": "1785000002.000100",
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if method == "conversations.replies":
                return {
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1785000001.000100",
                            "user": "USELF",
                            "text": "Please file this",
                        },
                        {
                            "ts": "1785000002.000100",
                            "thread_ts": "1785000001.000100",
                            "parent_user_id": "USELF",
                            "user": "ULINEAR",
                            "text": "",
                            "attachments": [
                                {
                                    "fallback": "Created issue SLA-5404",
                                    "color": "2f80ed",
                                }
                            ],
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api):
            messages = adapter.fetch_messages("C_PRODUCT", limit=50)

        self.assertEqual(
            [message["text"] for message in messages],
            ["Please file this", "Created issue SLA-5404"],
        )
        self.assertTrue(messages[1]["is_thread_reply"])
        self.assertEqual(messages[1]["push_name"], "Linear")

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

    def test_uploads_attachment_as_a_native_slack_thread_reply(self):
        adapter = SlackChannelAdapter()
        attachment = Path(self.tmpdir.name) / "thread-brief.pdf"
        attachment.write_bytes(b"%PDF")
        api_calls = []

        def fake_api(method, **kwargs):
            api_calls.append((method, kwargs))
            if method == "files.getUploadURLExternal":
                self.assertEqual(
                    kwargs["json_body"],
                    {"filename": "thread-brief.pdf", "length": 4},
                )
                return {
                    "ok": True,
                    "upload_url": "https://files.slack.com/upload/v1/signed",
                    "file_id": "FTHREAD",
                }
            if method == "files.completeUploadExternal":
                self.assertEqual(
                    kwargs["json_body"],
                    {
                        "files": [{"id": "FTHREAD", "title": "thread-brief.pdf"}],
                        "channel_id": "C_PRODUCT",
                        "initial_comment": "The requested brief",
                        "thread_ts": "1785000001.000100",
                    },
                )
                return {"ok": True, "files": [{"id": "FTHREAD"}]}
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api), mock.patch.object(
            adapter,
            "_upload_external_file",
            return_value=(True, None),
            create=True,
        ) as mock_upload:
            result = adapter.send_message(
                "C_PRODUCT",
                "The requested brief",
                attachment_paths=[str(attachment)],
                reply_to_message_id="slack:1785000001.000100",
            )

        self.assertEqual(result, (True, None))
        mock_upload.assert_called_once_with(
            "https://files.slack.com/upload/v1/signed",
            attachment,
        )
        self.assertEqual(
            [method for method, _kwargs in api_calls],
            ["files.getUploadURLExternal", "files.completeUploadExternal"],
        )

    def test_allows_attachment_only_slack_message(self):
        adapter = SlackChannelAdapter()
        attachment = Path(self.tmpdir.name) / "screenshot.png"
        attachment.write_bytes(b"png")

        def fake_api(method, **kwargs):
            if method == "files.getUploadURLExternal":
                return {
                    "ok": True,
                    "upload_url": "https://files.slack.com/upload/v1/signed",
                    "file_id": "FIMAGE",
                }
            if method == "files.completeUploadExternal":
                self.assertEqual(
                    kwargs["json_body"],
                    {
                        "files": [{"id": "FIMAGE", "title": "screenshot.png"}],
                        "channel_id": "C_PRODUCT",
                    },
                )
                return {"ok": True, "files": [{"id": "FIMAGE"}]}
            raise AssertionError(method)

        with mock.patch.object(adapter, "_api", side_effect=fake_api), mock.patch.object(
            adapter,
            "_upload_external_file",
            return_value=(True, None),
            create=True,
        ):
            result = adapter.send_message(
                "C_PRODUCT",
                "",
                attachment_paths=[str(attachment)],
            )

        self.assertEqual(result, (True, None))

    def test_rejects_missing_slack_attachment_before_calling_api(self):
        adapter = SlackChannelAdapter()
        with mock.patch.object(adapter, "_api") as mock_api:
            result = adapter.send_message(
                "C_PRODUCT",
                "This must not post by itself",
                attachment_paths=[str(Path(self.tmpdir.name) / "missing.pdf")],
            )

        self.assertEqual(result, (False, "slack_attachment_missing"))
        mock_api.assert_not_called()

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
        self.conn.execute(
            """INSERT INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction,
                sender_email, sender_name, subject, body_text, message_timestamp,
                is_read, metadata)
               VALUES ('slack-thread', 'slack', 'slack:1785000002.000100',
                       'provider_to_gmail', 'U_TEAMMATE', 'Taylor Example',
                       'Slack: #product', 'The nested detail',
                       '2026-07-25T12:00:02+00:00', 1,
                       '{"native_message_id":"1785000002.000100",
                         "thread_ts":"1785000001.000100",
                         "is_thread_reply":true}')"""
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
                reply_context_message_id="slack:1785000002.000100",
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
               WHERE conversation_id = 'slack-thread'
                 AND provider = 'manual'"""
        ).fetchone()
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["thread_ts"], "1785000001.000100")
        self.assertTrue(metadata["is_thread_reply"])
        self.assertEqual(
            metadata["reply_context"],
            {
                "message_id": "1785000002.000100",
                "sender": "Taylor Example",
                "text": "The nested detail",
            },
        )


if __name__ == "__main__":
    unittest.main()
