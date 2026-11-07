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
        value = float(raw) if raw else 0.5
    except ValueError:
        value = 0.5
    return max(0.25, min(value, 60.0))


def slack_live_enabled() -> bool:
    raw = (os.environ.get("PENGUIN_CONNECT_SLACK_LIVE") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_seconds(name: str, default: float, low: float, high: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(value, high))


def slack_live_interval_seconds() -> float:
    """Cross-channel activity probe cadence (search.messages is Tier 2, ~20 req/min)."""
    return _env_seconds("PENGUIN_CONNECT_SLACK_LIVE_INTERVAL", 4.0, 3.0, 120.0)


def slack_live_hot_interval_seconds() -> float:
    """Cadence for probing the conversation(s) currently open in the UI."""
    return _env_seconds("PENGUIN_CONNECT_SLACK_LIVE_HOT_INTERVAL", 2.0, 1.0, 60.0)


_hot_chats: dict[tuple[str, str], float] = {}
_hot_chats_lock = threading.Lock()
HOT_CHAT_TTL_SECONDS = 45.0
MAX_HOT_CHATS = 2


def note_hot_chat(provider: str, chat_id: str) -> None:
    """Record that the UI is looking at this chat, so live sync probes it more often."""
    if not chat_id:
        return
    with _hot_chats_lock:
        _hot_chats[(provider, chat_id)] = time.monotonic()


def hot_chats(provider: str, ttl: float = HOT_CHAT_TTL_SECONDS) -> list[str]:
    now = time.monotonic()
    with _hot_chats_lock:
        for key in [k for k, seen in _hot_chats.items() if now - seen > ttl]:
            del _hot_chats[key]
        live = sorted(
            ((seen, key[1]) for key, seen in _hot_chats.items() if key[0] == provider),
            reverse=True,
        )
    return [chat_id for _, chat_id in live[:MAX_HOT_CHATS]]


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


class ChangeSignal:
    """Monotonic counter that push listeners (SSE) can wait on without busy work."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.version = 0

    def bump(self) -> None:
        with self._condition:
            self.version += 1
            self._condition.notify_all()

    def wait_for_change(self, seen: int, timeout: float) -> int:
        with self._condition:
            self._condition.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version


workspace_changed = ChangeSignal()


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
        # True when the source file changed on the last poll, even without new rows
        # (e.g. a chat was read on the phone, which flips is_read in chat.db).
        self.changed = False

    def poll(self) -> list[str]:
        path = Path(self._db_path())
        if not path.exists():
            return []
        token = file_token([path, f"{path}-wal", f"{path}-journal"])
        self.changed = False
        if token == self._token:
            return []
        first_poll = self._token is None
        if self._cursor is None:
            max_rowid, _ = self._changes(path, 1 << 62)
            self._cursor = max(0, max_rowid - STARTUP_LOOKBACK_ROWS)
        max_rowid, chats = self._changes(path, self._cursor)
        # Only advance once the read succeeded, so a locked database is retried next tick.
        self._token = token
        self._cursor = max(self._cursor, max_rowid)
        self.changed = not first_poll
        return chats


class SlackActivityWatcher:
    """Polling change detector for Slack (a user token has no push channel).

    Two cheap probes, in different Slack rate-limit buckets:
      * search(): one cross-channel search.messages call (newest first) every `interval`
        seconds; any (channel, ts) newer than the watermark marks that channel changed.
      * probe(chat): conversations.history limit=1 for the chat(s) open in the UI every
        `hot_interval` seconds, which also covers search-index lag.
    Honors Retry-After on 429 and backs off exponentially on other failures. All inputs are
    injected callables so this is testable without Slack.
    """

    name = "slack"
    MAX_BACKOFF_SECONDS = 60.0
    MAX_RETRY_AFTER_SECONDS = 120.0

    def __init__(
        self,
        search: Callable[[], dict],
        probe: Callable[[str], dict],
        hot_chats_fn: Callable[[], list[str]],
        invalidate: Callable[[list[str]], None],
        interval: Callable[[], float] = slack_live_interval_seconds,
        hot_interval: Callable[[], float] = slack_live_hot_interval_seconds,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._search = search
        self._probe = probe
        self._hot_chats = hot_chats_fn
        self._invalidate = invalidate
        self._interval = interval
        self._hot_interval = hot_interval
        self._clock = clock
        self._watermark: float | None = None
        self._last_ts: dict[str, float] = {}
        self._next_search = 0.0
        self._next_hot = 0.0
        self._backoff_until = 0.0
        self._failures = 0
        self.changed = False
        self.last_error = ""

    def _handle_failure(self, result: dict, now: float) -> None:
        self.last_error = str(result.get("error") or "slack_unavailable")
        retry_after = result.get("retry_after")
        if retry_after is not None:
            try:
                delay = min(max(float(retry_after), 1.0), self.MAX_RETRY_AFTER_SECONDS)
            except (TypeError, ValueError):
                delay = self.MAX_BACKOFF_SECONDS
        else:
            self._failures += 1
            delay = min(self._interval() * (2 ** self._failures), self.MAX_BACKOFF_SECONDS)
        self._backoff_until = now + delay

    def _record(self, chat_id: str, ts: float) -> bool:
        """Remember the newest ts per chat; True if it advanced past what we knew."""
        if ts > self._last_ts.get(chat_id, 0.0):
            self._last_ts[chat_id] = ts
            return True
        return False

    def poll(self) -> list[str]:
        now = self._clock()
        self.changed = False
        if now < self._backoff_until:
            return []
        changed: list[str] = []
        if now >= self._next_search:
            self._next_search = now + self._interval()
            result = self._search()
            if not result.get("ok"):
                self._handle_failure(result, now)
                return []
            self._failures = 0
            self.last_error = ""
            items = sorted(result.get("items") or [], key=lambda item: item[1], reverse=True)
            if self._watermark is None:
                # First successful poll only establishes the baseline.
                for chat_id, ts in items:
                    self._record(chat_id, ts)
                self._watermark = max((ts for _, ts in items), default=0.0)
            else:
                watermark = self._watermark
                for chat_id, ts in items:
                    if ts > watermark and self._record(chat_id, ts) and chat_id not in changed:
                        changed.append(chat_id)
                self._watermark = max([watermark, *(ts for _, ts in items)])
        if now >= self._next_hot and self._watermark is not None:
            self._next_hot = now + self._hot_interval()
            for chat_id in self._hot_chats():
                result = self._probe(chat_id)
                if not result.get("ok"):
                    self._handle_failure(result, now)
                    break
                ts = float(result.get("ts") or 0.0)
                if chat_id not in self._last_ts:
                    self._last_ts[chat_id] = ts  # baseline
                elif self._record(chat_id, ts) and chat_id not in changed:
                    changed.append(chat_id)
        changed = changed[:MAX_CHATS_PER_TICK]
        if changed:
            self._invalidate(changed)
        return changed


class LiveSyncWorker:
    def __init__(
        self,
        watchers: list[SourceWatcher],
        refresh: Callable[[str, list[str]], int],
        log: Callable[..., None],
        interval: Callable[[], float] = live_sync_interval_seconds,
        name: str = "penguinconnect-live-sync",
    ) -> None:
        self._name = name
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
            if refreshed or any(getattr(watcher, "changed", False) for watcher in self._watchers):
                workspace_changed.bump()
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
        self._thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
