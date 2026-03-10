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

    def test_adapter_delegates_conversation_listing(self):
        adapter = get_channel_adapter("imessage")
        payload = {"available": True, "chats": [{"chat_id": "chat-1"}]}
        with mock.patch("channels.imessage.browse_imessage_chats", return_value=payload) as mock_browse:
            result = adapter.list_conversations(limit=25)
        mock_browse.assert_called_once_with(search=None, limit=25)
        self.assertEqual(result, payload)


if __name__ == "__main__":
    unittest.main()
