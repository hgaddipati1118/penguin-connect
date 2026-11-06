import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import live_sync
from live_sync import LiveSyncWorker, SourceWatcher, imessage_changes, whatsapp_changes


def _make_chat_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        INSERT INTO chat (ROWID, guid) VALUES (1, 'any;-;+15550001'), (2, 'any;+;group');
        """
    )
    return conn


def _add_imessage(conn: sqlite3.Connection, chat_rowid: int, text: str) -> None:
    cursor = conn.execute("INSERT INTO message (text) VALUES (?)", (text,))
    conn.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat_rowid, cursor.lastrowid))
    conn.commit()


def _bump_mtime(path: Path) -> None:
    stamp = time.time() + 5
    os.utime(path, (stamp, stamp))


class LiveSyncChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chat_db = Path(self.tmp.name) / "chat.db"
        self.conn = _make_chat_db(self.chat_db)
        self.addCleanup(self.conn.close)

    def test_imessage_changes_lists_chats_newest_first(self):
        _add_imessage(self.conn, 1, "a")
        _add_imessage(self.conn, 2, "b")
        _add_imessage(self.conn, 1, "c")
        max_rowid, chats = imessage_changes(self.chat_db, 1)
        self.assertEqual(max_rowid, 3)
        self.assertEqual(chats, ["any;-;+15550001", "any;+;group"])
        self.assertEqual(imessage_changes(self.chat_db, 3), (3, []))

    def test_watcher_reports_only_new_chats_after_first_poll(self):
        live_sync.STARTUP_LOOKBACK_ROWS = 0
        self.addCleanup(setattr, live_sync, "STARTUP_LOOKBACK_ROWS", 500)
        _add_imessage(self.conn, 1, "old")
        watcher = SourceWatcher("imessage", lambda: self.chat_db, imessage_changes)
        self.assertEqual(watcher.poll(), [])
        self.assertEqual(watcher.poll(), [])  # unchanged file: no query

        _add_imessage(self.conn, 2, "new")
        _bump_mtime(self.chat_db)
        self.assertEqual(watcher.poll(), ["any;+;group"])
        _bump_mtime(self.chat_db)
        self.assertEqual(watcher.poll(), [])

    def test_watcher_startup_lookback_catches_recent_messages(self):
        _add_imessage(self.conn, 1, "recent")
        watcher = SourceWatcher("imessage", lambda: self.chat_db, imessage_changes)
        self.assertEqual(watcher.poll(), ["any;-;+15550001"])

    def test_missing_database_is_ignored(self):
        watcher = SourceWatcher("whatsapp", lambda: Path(self.tmp.name) / "nope.db", whatsapp_changes)
        self.assertEqual(watcher.poll(), [])

    def test_whatsapp_changes(self):
        path = Path(self.tmp.name) / "messages.db"
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, content TEXT, PRIMARY KEY (id, chat_jid))")
        conn.executemany(
            "INSERT INTO messages VALUES (?, ?, ?)",
            [("1", "a@s.whatsapp.net", "x"), ("2", "b@g.us", "y"), ("3", "b@g.us", "z")],
        )
        conn.commit()
        self.assertEqual(whatsapp_changes(path, 1), (3, ["b@g.us"]))

    def test_worker_tick_refreshes_and_survives_errors(self):
        calls = []
        logged = []

        class Boom:
            name = "broken"

            def poll(self):
                raise RuntimeError("bad")

        class Fixed:
            name = "imessage"

            def poll(self):
                return ["chat-1"]

        worker = LiveSyncWorker(
            [Boom(), Fixed()],
            refresh=lambda source, chats: calls.append((source, chats)) or len(chats),
            log=lambda event, **fields: logged.append(event),
        )
        self.assertEqual(worker.tick(), {"imessage": 1})
        self.assertEqual(calls, [("imessage", ["chat-1"])])
        self.assertEqual(logged, ["live_sync_exception"])


if __name__ == "__main__":
    unittest.main()
