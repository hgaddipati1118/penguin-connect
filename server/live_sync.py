"""Near-real-time sync of new iMessage and WhatsApp messages into Penguin's cache.

The UI already polls /workspace-revision every few seconds and redraws when the cache
changes, but nothing copied new source messages into the cache unless a chat was opened
(the background poller only runs when Gmail is connected). This worker watches the source
databases cheaply (stat first, then a ROWID query only when a file changed) and refreshes
just the conversations that received new messages.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

# How far back to look on startup, so messages that arrived while the server was down
# still reach the cache.
STARTUP_LOOKBACK_ROWS = 500
MAX_CHATS_PER_TICK = 40
DISCOVERY_MIN_INTERVAL_SECONDS = 30.0


def live_sync_enabled() -> bool:
    raw = (os.environ.get("PENGUIN_CONNECT_LIVE_SYNC") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def live_sync_interval_seconds() -> float:
    raw = (os.environ.get("PENGUIN_CONNECT_LIVE_SYNC_INTERVAL_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else 2.0
    except ValueError:
        value = 2.0
    return max(0.5, min(value, 60.0))


def file_token(paths: Iterable[str | Path]) -> tuple:
    """Cheap change token: mtime and size of a database and its journal files."""
    token = []
    for path in paths:
        try:
            stat = os.stat(path)
            token.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            token.append(None)
    return tuple(token)


def _read_only(path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


def imessage_changes(db_path: str | Path, after_rowid: int) -> tuple[int, list[str]]:
    """Return (max_rowid, chat guids with messages newer than after_rowid), newest first."""
    conn = _read_only(db_path)
    try:
        max_rowid = int(conn.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
        if max_rowid <= after_rowid:
            return max_rowid, []
        rows = conn.execute(
            """SELECT c.guid, MAX(m.ROWID) AS newest
               FROM message m
               JOIN chat_message_join j ON j.message_id = m.ROWID
               JOIN chat c ON c.ROWID = j.chat_id
               WHERE m.ROWID > ?
               GROUP BY c.guid
               ORDER BY newest DESC
               LIMIT ?""",
            (after_rowid, MAX_CHATS_PER_TICK),
        ).fetchall()
        return max_rowid, [str(row[0]) for row in rows if row[0]]
    finally:
        conn.close()


def whatsapp_changes(db_path: str | Path, after_rowid: int) -> tuple[int, list[str]]:
    """Return (max_rowid, chat JIDs with messages newer than after_rowid), newest first."""
    conn = _read_only(db_path)
    try:
        max_rowid = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0])
        if max_rowid <= after_rowid:
            return max_rowid, []
        rows = conn.execute(
            """SELECT chat_jid, MAX(rowid) AS newest
               FROM messages
               WHERE rowid > ?
               GROUP BY chat_jid
               ORDER BY newest DESC
               LIMIT ?""",
            (after_rowid, MAX_CHATS_PER_TICK),
        ).fetchall()
        return max_rowid, [str(row[0]) for row in rows if row[0]]
    finally:
        conn.close()


class SourceWatcher:
    """Tracks one source database and reports chats that changed since the last check."""

    def __init__(
        self,
        name: str,
        db_path: Callable[[], str | Path],
        changes: Callable[[str | Path, int], tuple[int, list[str]]],
    ) -> None:
        self.name = name
        self._db_path = db_path
        self._changes = changes
        self._token: tuple | None = None
        self._cursor: int | None = None

    def poll(self) -> list[str]:
        path = Path(self._db_path())
        if not path.exists():
            return []
        token = file_token([path, f"{path}-wal", f"{path}-journal"])
        if token == self._token:
            return []
        if self._cursor is None:
            max_rowid, _ = self._changes(path, 1 << 62)
            self._cursor = max(0, max_rowid - STARTUP_LOOKBACK_ROWS)
        max_rowid, chats = self._changes(path, self._cursor)
        # Only advance once the read succeeded, so a locked database is retried next tick.
        self._token = token
        self._cursor = max(self._cursor, max_rowid)
        return chats


class LiveSyncWorker:
    def __init__(
        self,
        watchers: list[SourceWatcher],
        refresh: Callable[[str, list[str]], int],
        log: Callable[..., None],
        interval: Callable[[], float] = live_sync_interval_seconds,
    ) -> None:
        self._watchers = watchers
        self._refresh = refresh
        self._log = log
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> dict[str, int]:
        refreshed: dict[str, int] = {}
        for watcher in self._watchers:
            try:
                chats = watcher.poll()
                if chats:
                    refreshed[watcher.name] = self._refresh(watcher.name, chats)
            except sqlite3.OperationalError:
                # Source database busy (e.g. WhatsApp history sync writing); retry next tick.
                continue
            except Exception as exc:  # keep the loop alive; surface the error in actions log
                self._log("live_sync_exception", source=watcher.name, error=str(exc) or exc.__class__.__name__)
        return refreshed

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            refreshed = self.tick()
            if refreshed:
                self._log(
                    "live_sync_refreshed",
                    sources=refreshed,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            self._stop.wait(self._interval())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="penguinconnect-live-sync")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
