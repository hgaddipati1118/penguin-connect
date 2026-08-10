"""Tests for batched Apple Messages browsing."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import browse_sources


class BrowseSourceTests(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.database.close()
        conn = sqlite3.connect(self.database.name)
        conn.executescript(
            """
            CREATE TABLE chat (
                guid TEXT,
                chat_identifier TEXT,
                display_name TEXT,
                room_name TEXT,
                service_name TEXT
            );
            CREATE TABLE message (
                text TEXT,
                attributedBody BLOB,
                date INTEGER
            );
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE handle (id TEXT);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            """
        )
        conn.executemany(
            """INSERT INTO chat
               (guid, chat_identifier, display_name, room_name, service_name)
               VALUES (?, ?, ?, ?, ?)""",
            [
                ("iMessage;-;+15550000001", "+15550000001", "", "", "iMessage"),
                ("iMessage;+;group-1", "group-1", "", "Launch Crew", "iMessage"),
            ],
        )
        conn.executemany(
            "INSERT INTO handle (id) VALUES (?)",
            [("+15550000001",), ("+15550000002",), ("+15550000003",)],
        )
        conn.executemany(
            "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
            [(1, 1), (2, 2), (2, 3)],
        )
        attributed_text = "Newest attributed message"
        attributed_body = (
            b"NSString\x01\x84\x01+"
            + bytes([len(attributed_text.encode())])
            + attributed_text.encode()
        )
        conn.executemany(
            "INSERT INTO message (text, attributedBody, date) VALUES (?, ?, ?)",
            [
                ("Older direct message", None, 100),
                ("Newest direct message", None, 200),
                ("Group hello", None, 150),
                (None, attributed_body, 250),
            ],
        )
        conn.executemany(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
            [(1, 1), (1, 2), (2, 3), (2, 4)],
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.database.name)

    def test_browse_chats_batches_participants_and_latest_previews(self):
        with mock.patch.object(browse_sources, "IMESSAGE_DB", self.database.name):
            result = browse_sources.browse_imessage_chats(limit=None)

        self.assertTrue(result["available"])
        self.assertEqual(len(result["chats"]), 2)
        by_guid = {chat["chat_guid"]: chat for chat in result["chats"]}
        direct = by_guid["iMessage;-;+15550000001"]
        group = by_guid["iMessage;+;group-1"]
        self.assertEqual(direct["participants"], ["+15550000001"])
        self.assertEqual(direct["last_message_preview"], "Newest direct message")
        self.assertEqual(direct["message_count"], 2)
        self.assertEqual(group["participants"], ["+15550000002", "+15550000003"])
        self.assertEqual(group["last_message_preview"], "Newest attributed message")
        self.assertEqual(group["name"], "Launch Crew")


if __name__ == "__main__":
    unittest.main()
