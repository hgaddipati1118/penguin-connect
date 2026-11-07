import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import live_sync
from live_sync import LiveSyncWorker, SlackActivityWatcher, SourceWatcher, imessage_changes, whatsapp_changes


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


class FakeSlack:
    """Mocked Slack client: scripted search/probe results, records calls."""

    def __init__(self):
        self.now = 1000.0
        self.search_results = []
        self.probe_results = {}
        self.search_calls = 0
        self.probe_calls = []
        self.hot = []
        self.invalidated = []

    def search(self):
        self.search_calls += 1
        result = self.search_results.pop(0) if len(self.search_results) > 1 else self.search_results[0]
        return result

    def probe(self, chat_id):
        self.probe_calls.append(chat_id)
        return self.probe_results.get(chat_id, {"ok": True, "ts": 0.0})

    def watcher(self):
        return SlackActivityWatcher(
            self.search,
            self.probe,
            lambda: list(self.hot),
            lambda ids: self.invalidated.append(list(ids)),
            interval=lambda: 4.0,
            hot_interval=lambda: 2.0,
            clock=lambda: self.now,
        )


def _ok(*items):
    return {"ok": True, "items": list(items)}


class SlackActivityWatcherTests(unittest.TestCase):
    def setUp(self):
        self.slack = FakeSlack()

    def test_first_poll_sets_baseline_without_refresh(self):
        self.slack.search_results = [_ok(("C1", 10.0), ("C2", 9.0))]
        watcher = self.slack.watcher()
        self.assertEqual(watcher.poll(), [])
        self.assertEqual(self.slack.invalidated, [])

    def test_only_changed_channels_are_reported_and_invalidated(self):
        self.slack.search_results = [
            _ok(("C1", 10.0), ("C2", 9.0)),
            _ok(("C3", 12.0), ("C1", 10.0), ("C2", 9.0)),
        ]
        watcher = self.slack.watcher()
        watcher.poll()
        self.slack.now += 4
        self.assertEqual(watcher.poll(), ["C3"])
        self.assertEqual(self.slack.invalidated, [["C3"]])

    def test_no_change_means_no_refresh(self):
        self.slack.search_results = [_ok(("C1", 10.0))]
        watcher = self.slack.watcher()
        watcher.poll()
        for _ in range(3):
            self.slack.now += 4
            self.assertEqual(watcher.poll(), [])
        self.assertEqual(self.slack.invalidated, [])

    def test_search_is_throttled_to_interval(self):
        self.slack.search_results = [_ok(("C1", 10.0))]
        watcher = self.slack.watcher()
        watcher.poll()
        self.slack.now += 1
        watcher.poll()
        self.slack.now += 1
        watcher.poll()
        self.assertEqual(self.slack.search_calls, 1)
        self.slack.now += 2
        watcher.poll()
        self.assertEqual(self.slack.search_calls, 2)

    def test_429_retry_after_is_honored(self):
        self.slack.search_results = [
            _ok(("C1", 10.0)),
            {"ok": False, "error": "ratelimited", "retry_after": 30.0},
            _ok(("C1", 11.0)),
        ]
        watcher = self.slack.watcher()
        watcher.poll()
        self.slack.now += 4
        self.assertEqual(watcher.poll(), [])  # rate limited
        calls = self.slack.search_calls
        self.slack.now += 25
        self.assertEqual(watcher.poll(), [])  # still backing off
        self.assertEqual(self.slack.search_calls, calls)
        self.slack.now += 6  # past Retry-After
        self.assertEqual(watcher.poll(), ["C1"])

    def test_generic_failure_backs_off_exponentially_then_recovers(self):
        self.slack.search_results = [
            _ok(("C1", 10.0)),
            {"ok": False, "error": "slack_request_failed:boom", "retry_after": None},
            _ok(("C1", 11.0)),
        ]
        watcher = self.slack.watcher()
        watcher.poll()
        self.slack.now += 4
        watcher.poll()  # failure 1 -> 8s backoff
        self.slack.now += 5
        watcher.poll()
        self.assertEqual(self.slack.search_calls, 2)
        self.slack.now += 4
        self.assertEqual(watcher.poll(), ["C1"])

    def test_hot_chat_probe_detects_change_between_searches(self):
        self.slack.search_results = [_ok(("C1", 10.0))]
        self.slack.hot = ["C9"]
        self.slack.probe_results = {"C9": {"ok": True, "ts": 5.0}}
        watcher = self.slack.watcher()
        watcher.poll()  # baseline for C9
        self.slack.now += 2
        self.assertEqual(watcher.poll(), [])
        self.assertEqual(self.slack.search_calls, 1)
        self.slack.probe_results["C9"] = {"ok": True, "ts": 6.0}
        self.slack.now += 2
        self.assertEqual(watcher.poll(), ["C9"])

    def test_search_and_probe_do_not_double_report(self):
        self.slack.search_results = [_ok(("C9", 5.0)), _ok(("C9", 6.0))]
        self.slack.hot = ["C9"]
        self.slack.probe_results = {"C9": {"ok": True, "ts": 5.0}}
        watcher = self.slack.watcher()
        watcher.poll()
        self.slack.probe_results = {"C9": {"ok": True, "ts": 6.0}}
        self.slack.now += 4
        self.assertEqual(watcher.poll(), ["C9"])  # search and probe both see ts 6.0
        self.slack.now += 4
        self.assertEqual(watcher.poll(), [])

    def test_worker_refreshes_only_changed_and_survives_failures(self):
        self.slack.search_results = [
            _ok(("C1", 10.0)),
            _ok(("C2", 11.0), ("C1", 10.0)),
        ]
        refreshed = []
        logged = []
        worker = LiveSyncWorker(
            [self.slack.watcher()],
            refresh=lambda source, chats: refreshed.append((source, chats)) or len(chats),
            log=lambda event, **fields: logged.append(event),
        )
        worker.tick()
        self.slack.now += 4
        self.assertEqual(worker.tick(), {"slack": 1})
        self.assertEqual(refreshed, [("slack", ["C2"])])

        def boom():
            raise RuntimeError("network down")

        failing = SlackActivityWatcher(boom, self.slack.probe, lambda: [], lambda ids: None, clock=lambda: 0.0)
        worker = LiveSyncWorker([failing], refresh=lambda *_: 0, log=lambda event, **f: logged.append(event))
        self.assertEqual(worker.tick(), {})
        self.assertIn("live_sync_exception", logged)

    def test_hot_chat_registry_expires_and_limits(self):
        live_sync._hot_chats.clear()
        self.addCleanup(live_sync._hot_chats.clear)
        live_sync.note_hot_chat("slack", "C1")
        live_sync.note_hot_chat("slack", "C2")
        live_sync.note_hot_chat("slack", "C3")
        live_sync.note_hot_chat("whatsapp", "W1")
        self.assertEqual(live_sync.hot_chats("slack"), ["C3", "C2"])
        self.assertEqual(live_sync.hot_chats("slack", ttl=-1), [])

    def test_env_config_and_kill_switch(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_SLACK_LIVE_INTERVAL": "7"}):
            self.assertEqual(live_sync.slack_live_interval_seconds(), 7.0)
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_SLACK_LIVE_INTERVAL": "0.1"}):
            self.assertEqual(live_sync.slack_live_interval_seconds(), 3.0)
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_SLACK_LIVE": "0"}):
            self.assertFalse(live_sync.slack_live_enabled())
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_SLACK_LIVE": ""}):
            self.assertTrue(live_sync.slack_live_enabled())


if __name__ == "__main__":
    unittest.main()
