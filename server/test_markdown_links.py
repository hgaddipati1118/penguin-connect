import sqlite3
import unittest
from unittest import mock

import penguin_connect
from db import SCHEMA


class MarkdownLinkRewriteTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            """INSERT INTO penguin_connect_accounts
               (gmail_email, keychain_service, send_as_aliases, status)
               VALUES (?, ?, ?, 'connected')""",
            (
                "owner@gmail.com",
                "penguinconnect-local-bridge.gmail.owner@gmail.com",
                '["owner@gmail.com"]',
            ),
        )
        self.conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, conversation_id, imessage_chat_id, display_name, chat_type, participants,
                alias_email, status)
               VALUES (?, ?, ?, ?, 'dm', '[]', ?, 'active')""",
            (
                "owner@gmail.com",
                "amc_test",
                "chat-123",
                "Docs Chat",
                "owner+am-test@gmail.com",
            ),
        )

    def tearDown(self):
        self.conn.close()

    def _conversation_row(self):
        return self.conn.execute(
            "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
            ("amc_test",),
        ).fetchone()

    def test_send_to_source_conversation_rewrites_markdown_links(self):
        conv = self._conversation_row()

        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send:
            ok, error = penguin_connect._send_to_source_conversation(
                conv,
                "See [Docs](https://example.com/docs) for details",
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(
            mock_send.call_args.args[1],
            "See Docs: https://example.com/docs for details",
        )

    def test_send_manual_message_stores_rewritten_markdown_links(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)):
            result = penguin_connect.send_manual_message(
                self.conn,
                conversation_id="amc_test",
                sender_email="owner@gmail.com",
                body_text="Read [Docs](https://example.com/docs)",
            )

        self.assertTrue(result["success"])
        row = self.conn.execute(
            """SELECT body_text
               FROM penguin_connect_messages
               WHERE conversation_id = ? AND direction = 'manual_to_imessage'
               ORDER BY id DESC
               LIMIT 1""",
            ("amc_test",),
        ).fetchone()
        self.assertEqual(row["body_text"], "Read Docs: https://example.com/docs")


if __name__ == "__main__":
    unittest.main()
