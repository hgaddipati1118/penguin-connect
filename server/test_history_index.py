import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import browse_sources
import search_index


def _make_chat_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT,
            display_name TEXT, room_name TEXT, service_name TEXT
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER,
            is_from_me INTEGER, handle_id INTEGER, service TEXT, associated_message_type INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        INSERT INTO handle VALUES (1, '+15551234567');
        INSERT INTO chat VALUES (1, 'any;-;+15551234567', '+15551234567', '', '', 'iMessage');
        INSERT INTO chat VALUES (2, 'any;+;chat99', 'chat99', 'Lease crew', '', 'iMessage');
        INSERT INTO message VALUES (1, 'the lease renewal is due friday', NULL, 700000000000000000, 0, 1, 'iMessage', 0);
        INSERT INTO message VALUES (2, 'Loved "the lease renewal"', NULL, 700000001000000000, 1, 1, 'iMessage', 2000);
        INSERT INTO message VALUES (3, 'flight lands at 6', NULL, 700000002000000000, 1, 0, 'iMessage', 0);
        INSERT INTO message VALUES (4, NULL, NULL, 700000003000000000, 0, 1, 'iMessage', 0);
        INSERT INTO chat_message_join VALUES (1, 1), (1, 2), (2, 3), (1, 4);
        """
    )
    conn.commit()
    conn.close()


class HistoryIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.chat_db = root / "chat.db"
        _make_chat_db(self.chat_db)
        self.patchers = [
            mock.patch.object(browse_sources, "IMESSAGE_DB", str(self.chat_db)),
            mock.patch.object(search_index, "SEARCH_DB_PATH", root / "search.db"),
            mock.patch.object(search_index, "DB_PATH", root / "missing-cache.db"),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        self.tmp.cleanup()

    def test_reader_skips_tapbacks_and_empty_rows_but_advances_cursor(self):
        result = browse_sources.list_imessage_history_for_index(0, 100)
        self.assertTrue(result["available"])
        self.assertEqual([m["rowid"] for m in result["messages"]], [1, 3])
        self.assertEqual(result["max_rowid"], 4)

    def test_indexes_history_incrementally_and_searches_it(self):
        first = search_index.refresh_imessage_history_index(batch_size=1)
        self.assertTrue(first["success"])
        self.assertEqual(first["indexed"], 2)
        self.assertEqual(first["last_rowid"], 4)

        results = search_index.hybrid_search("lease renewal")["results"]
        # Best match first; the "Lease crew" group also matches on its chat title.
        self.assertIn("lease renewal", results[0]["snippet"])
        self.assertEqual(results[0]["metadata"]["provider_message_id"], "imessage:1")

        conn = sqlite3.connect(self.chat_db)
        conn.execute(
            "INSERT INTO message VALUES (5, 'new lease draft attached', NULL, 700000004000000000, 1, 0, 'iMessage', 0)"
        )
        conn.execute("INSERT INTO chat_message_join VALUES (2, 5)")
        conn.commit()
        conn.close()

        second = search_index.refresh_imessage_history_index()
        self.assertEqual(second["indexed"], 1)
        self.assertEqual(second["last_rowid"], 5)
        self.assertEqual(search_index.refresh_imessage_history_index()["indexed"], 0)
        snippets = [r["snippet"] for r in search_index.hybrid_search("draft")["results"]]
        self.assertEqual(len(snippets), 1)
        self.assertIn("new lease draft attached", snippets[0])

    def test_cache_refresh_does_not_delete_history_documents(self):
        search_index.refresh_imessage_history_index()
        search_index.refresh_message_search_index()
        self.assertEqual(len(search_index.hybrid_search("flight")["results"]), 1)

    def test_unmounted_external_volume_is_refused(self):
        with mock.patch.object(search_index, "SEARCH_DB_PATH", Path("/Volumes/NotMountedXYZ/penguin/search.db")):
            with self.assertRaises(sqlite3.OperationalError):
                search_index.refresh_imessage_history_index()
            self.assertEqual(search_index.hybrid_search("lease")["results"], [])
        self.assertFalse(Path("/Volumes/NotMountedXYZ").exists())


if __name__ == "__main__":
    unittest.main()
