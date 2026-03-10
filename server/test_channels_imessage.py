import unittest
from unittest import mock

from channels import get_channel_adapter


class IMessageChannelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.subprocess_patcher = mock.patch(
            "channels.imessage.subprocess.run",
            side_effect=AssertionError("Tests must mock iMessage subprocess sends explicitly"),
        )
        self.subprocess_patcher.start()

    def tearDown(self):
        self.subprocess_patcher.stop()

    def test_registry_returns_imessage_adapter(self):
        adapter = get_channel_adapter("imessage")
        self.assertEqual(adapter.provider, "imessage")
        self.assertIs(get_channel_adapter("apple_messages"), adapter)
        self.assertIs(get_channel_adapter("sms"), adapter)
        self.assertIs(get_channel_adapter("rcs"), adapter)

    def test_adapter_delegates_conversation_listing(self):
        adapter = get_channel_adapter("imessage")
        payload = {"available": True, "chats": [{"chat_id": "chat-1"}]}
        with mock.patch("channels.imessage.browse_imessage_chats", return_value=payload) as mock_browse:
            result = adapter.list_conversations(limit=25)
        mock_browse.assert_called_once_with(search=None, limit=25)
        self.assertEqual(result, payload)

    def test_send_message_uses_exact_chat_guid_and_not_buddy_fallback(self):
        adapter = get_channel_adapter("imessage")
        completed = mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch(
            "channels.imessage.resolve_apple_messages_chat",
            return_value={"guid": "RCS;+;chat123", "service_name": "RCS"},
        ), mock.patch("channels.imessage.subprocess.run", return_value=completed) as mock_run:
            ok, err = adapter.send_message("chat123", "hello")

        self.assertTrue(ok)
        self.assertIsNone(err)
        script = mock_run.call_args.args[0][2]
        self.assertIn('set targetChat to chat id "RCS;+;chat123"', script)
        self.assertNotIn("targetBuddy", script)

    def test_send_message_fails_closed_when_chat_route_is_unresolved(self):
        adapter = get_channel_adapter("imessage")
        with mock.patch("channels.imessage.resolve_apple_messages_chat", return_value=None), mock.patch(
            "channels.imessage.subprocess.run"
        ) as mock_run:
            ok, err = adapter.send_message("chat123", "hello")

        self.assertFalse(ok)
        self.assertEqual(err, "unsafe_chat_route_unresolved")
        mock_run.assert_not_called()

    def test_send_message_fails_closed_when_chat_route_is_ambiguous(self):
        adapter = get_channel_adapter("imessage")
        with mock.patch(
            "channels.imessage.resolve_apple_messages_chat",
            return_value={"guid": "iMessage;-;chat123", "ambiguous": True},
        ), mock.patch("channels.imessage.subprocess.run") as mock_run:
            ok, err = adapter.send_message("chat123", "hello")

        self.assertFalse(ok)
        self.assertEqual(err, "unsafe_chat_route_ambiguous")
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
