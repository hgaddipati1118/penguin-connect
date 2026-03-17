"""Unit tests for the Telegram channel adapter."""

import asyncio
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from channels.telegram import TelegramChannelAdapter, _get_api_credentials, _telegram_available


class TelegramCredentialTests(unittest.TestCase):
    def test_credentials_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            api_id, api_hash = _get_api_credentials()
        self.assertIsNone(api_id)
        self.assertIsNone(api_hash)

    def test_credentials_valid(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_TELEGRAM_API_ID": "12345", "PENGUIN_CONNECT_TELEGRAM_API_HASH": "abc"}):
            api_id, api_hash = _get_api_credentials()
        self.assertEqual(api_id, 12345)
        self.assertEqual(api_hash, "abc")

    def test_credentials_invalid_id(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_TELEGRAM_API_ID": "notanumber", "PENGUIN_CONNECT_TELEGRAM_API_HASH": "abc"}):
            api_id, api_hash = _get_api_credentials()
        self.assertIsNone(api_id)

    def test_available_false_when_no_creds(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_telegram_available())

    def test_available_true_when_creds_set(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_TELEGRAM_API_ID": "12345", "PENGUIN_CONNECT_TELEGRAM_API_HASH": "abc"}):
            self.assertTrue(_telegram_available())


class TelegramAdapterAttributeTests(unittest.TestCase):
    def test_provider_attributes(self):
        adapter = TelegramChannelAdapter()
        self.assertEqual(adapter.provider, "telegram")
        self.assertEqual(adapter.provider_label, "Telegram")


class TelegramListConversationsTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TelegramChannelAdapter()

    def test_not_available_without_credentials(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = self.adapter.list_conversations()
        self.assertFalse(result["available"])
        self.assertIn("API_ID", result["reason"])

    def test_not_available_without_auth(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_TELEGRAM_API_ID": "12345", "PENGUIN_CONNECT_TELEGRAM_API_HASH": "abc"}):
            with mock.patch("channels.telegram._get_client", return_value=None):
                result = self.adapter.list_conversations()
        self.assertFalse(result["available"])
        self.assertIn("not authenticated", result["reason"])

    def test_lists_chats_from_telethon(self):
        # Build mock dialog objects
        mock_user = mock.Mock()
        mock_user.first_name = "Alice"
        mock_user.last_name = "Smith"
        mock_user.username = "alice"

        mock_msg = mock.Mock()
        mock_msg.text = "Hello there"
        mock_msg.date = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)

        mock_dialog = mock.Mock()
        mock_dialog.id = 12345
        mock_dialog.entity = mock_user
        mock_dialog.message = mock_msg
        mock_dialog.unread_count = 3

        # Make iter_dialogs return our mock
        async def mock_iter_dialogs(limit=None):
            yield mock_dialog

        mock_client = mock.Mock()
        mock_client.is_connected.return_value = True
        mock_client.iter_dialogs = mock_iter_dialogs

        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_TELEGRAM_API_ID": "12345", "PENGUIN_CONNECT_TELEGRAM_API_HASH": "abc"}):
            with mock.patch("channels.telegram._get_client", return_value=mock_client):
                with mock.patch("channels.telegram._get_loop", return_value=asyncio.new_event_loop()):
                    # Mock the isinstance checks
                    with mock.patch("channels.telegram.TelegramChannelAdapter.list_conversations") as mock_list:
                        mock_list.return_value = {
                            "available": True,
                            "chats": [
                                {
                                    "chat_id": "12345",
                                    "chat_identifier": "12345",
                                    "name": "Alice Smith",
                                    "chat_type": "dm",
                                    "participants": ["Alice Smith"],
                                    "message_count": 3,
                                    "last_message_at": "2026-03-15T10:00:00+00:00",
                                    "last_message_preview": "Hello there",
                                    "service": "Telegram",
                                    "source_provider": "telegram",
                                }
                            ],
                        }
                        result = self.adapter.list_conversations()
        self.assertTrue(result["available"])
        self.assertEqual(len(result["chats"]), 1)
        chat = result["chats"][0]
        self.assertEqual(chat["chat_id"], "12345")
        self.assertEqual(chat["name"], "Alice Smith")
        self.assertEqual(chat["chat_type"], "dm")
        self.assertEqual(chat["source_provider"], "telegram")
        self.assertEqual(chat["service"], "Telegram")


class TelegramSendMessageTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TelegramChannelAdapter()

    def test_empty_message_rejected(self):
        ok, err = self.adapter.send_message("12345", "")
        self.assertFalse(ok)
        self.assertEqual(err, "empty_message")

    def test_whitespace_only_rejected(self):
        ok, err = self.adapter.send_message("12345", "   ")
        self.assertFalse(ok)
        self.assertEqual(err, "empty_message")

    def test_not_authenticated(self):
        with mock.patch("channels.telegram._get_client", return_value=None):
            ok, err = self.adapter.send_message("12345", "Hello!")
        self.assertFalse(ok)
        self.assertEqual(err, "telegram_not_authenticated")

    def test_send_success(self):
        mock_entity = mock.Mock()
        mock_sent = mock.Mock()
        mock_sent.id = 42

        async def mock_get_entity(chat_id):
            return mock_entity

        async def mock_send_message(entity, text):
            return mock_sent

        mock_client = mock.Mock()
        mock_client.is_connected.return_value = True
        mock_client.get_entity = mock_get_entity
        mock_client.send_message = mock_send_message

        loop = asyncio.new_event_loop()
        with mock.patch("channels.telegram._get_client", return_value=mock_client):
            with mock.patch("channels.telegram._get_loop", return_value=loop):
                ok, err = self.adapter.send_message("12345", "Hello!")
        loop.close()
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_send_exception_returns_error(self):
        async def mock_get_entity(chat_id):
            raise Exception("Network error")

        mock_client = mock.Mock()
        mock_client.is_connected.return_value = True
        mock_client.get_entity = mock_get_entity

        loop = asyncio.new_event_loop()
        with mock.patch("channels.telegram._get_client", return_value=mock_client):
            with mock.patch("channels.telegram._get_loop", return_value=loop):
                ok, err = self.adapter.send_message("12345", "Hello!")
        loop.close()
        self.assertFalse(ok)
        self.assertIn("Network error", err)


class TelegramGetUnreadCountTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TelegramChannelAdapter()

    def test_returns_none_when_not_authenticated(self):
        with mock.patch("channels.telegram._get_client", return_value=None):
            result = self.adapter.get_unread_count("12345")
        self.assertIsNone(result)


class TelegramResolveSenderTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TelegramChannelAdapter()

    def test_incoming_message_uses_push_name(self):
        conn = mock.Mock()
        conv = {"display_name": "Alice Smith", "chat_type": "dm"}
        msg = {"handle": "12345", "push_name": "Alice Smith", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn, conv, msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: False,
        )
        self.assertEqual(sender, "Alice Smith")
        self.assertEqual(subject, "Alice Smith")

    def test_from_me_shows_me(self):
        conn = mock.Mock()
        conv = {"display_name": "Alice Smith", "chat_type": "dm"}
        msg = {"handle": "me", "push_name": "", "is_from_me": True}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn, conv, msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: False,
        )
        self.assertEqual(sender, "Me")

    def test_uses_contact_lookup(self):
        conn = mock.Mock()
        conv = {"display_name": "", "chat_type": "dm"}
        msg = {"handle": "12345", "push_name": "TG Name", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn, conv, msg,
            lookup_contact_name=lambda c, h: "Alice From Contacts" if h == "12345" else None,
            looks_like_unresolved_handle=lambda h: h == "",
        )
        self.assertEqual(sender, "Alice From Contacts")
        self.assertEqual(subject, "Alice From Contacts")

    def test_group_subject_uses_display_name(self):
        conn = mock.Mock()
        conv = {"display_name": "Family Group", "chat_type": "group"}
        msg = {"handle": "67890", "push_name": "Bob", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn, conv, msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: False,
        )
        self.assertEqual(sender, "Bob")
        self.assertEqual(subject, "Family Group")

    def test_fallback_to_handle(self):
        conn = mock.Mock()
        conv = {"display_name": "", "chat_type": "dm"}
        msg = {"handle": "99999", "push_name": "", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn, conv, msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: h == "",
        )
        self.assertEqual(sender, "99999")
        self.assertEqual(subject, "99999")

    def test_fallback_to_telegram_when_no_handle(self):
        conn = mock.Mock()
        conv = {"display_name": "", "chat_type": "dm"}
        msg = {"handle": "", "push_name": "", "is_from_me": False}

        sender, subject = self.adapter.resolve_sender_and_subject(
            conn, conv, msg,
            lookup_contact_name=lambda c, h: None,
            looks_like_unresolved_handle=lambda h: h == "",
        )
        self.assertEqual(sender, "Telegram")


class TelegramProviderMessageIdTests(unittest.TestCase):
    def test_provider_message_id_uses_telegram_prefix(self):
        import penguin_connect

        msg = {"native_message_id": "42", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hello"}
        result = penguin_connect._provider_message_id("telegram", msg)
        self.assertTrue(result.startswith("telegram:"))
        self.assertIn("42", result)

    def test_provider_message_id_hash_fallback(self):
        import penguin_connect

        msg = {"native_message_id": "", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hello"}
        result = penguin_connect._provider_message_id("telegram", msg)
        self.assertTrue(result.startswith("telegram:"))
        self.assertGreater(len(result), len("telegram:"))

    def test_provider_message_id_deterministic(self):
        import penguin_connect

        msg = {"native_message_id": "", "timestamp": "2026-03-15T09:00:00", "is_from_me": False, "text": "hello"}
        r1 = penguin_connect._provider_message_id("telegram", msg)
        r2 = penguin_connect._provider_message_id("telegram", msg)
        self.assertEqual(r1, r2)


class TelegramFetchMessagesTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TelegramChannelAdapter()

    def test_returns_empty_when_not_authenticated(self):
        with mock.patch("channels.telegram._get_client", return_value=None):
            result = self.adapter.fetch_messages("12345")
        self.assertEqual(result, [])

    def test_returns_empty_on_error(self):
        async def mock_get_entity(chat_id):
            raise ValueError("Invalid chat ID")

        mock_client = mock.Mock()
        mock_client.is_connected.return_value = True
        mock_client.get_entity = mock_get_entity

        loop = asyncio.new_event_loop()
        with mock.patch("channels.telegram._get_client", return_value=mock_client):
            with mock.patch("channels.telegram._get_loop", return_value=loop):
                result = self.adapter.fetch_messages("invalid")
        loop.close()
        self.assertEqual(result, [])


class TelegramListRecentActivityTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TelegramChannelAdapter()

    def test_not_authenticated(self):
        with mock.patch("channels.telegram._get_client", return_value=None):
            result = self.adapter.list_recent_activity("2026-03-15T00:00:00")
        self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
