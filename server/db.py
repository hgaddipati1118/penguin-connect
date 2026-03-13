"""SQLite database for the PenguinConnect macOS local bridge."""

from __future__ import annotations

import email.utils
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quoted_content import extract_latest_email_text

DATA_DIR = Path(
    os.environ.get("PENGUIN_CONNECT_DATA_DIR", str(Path.home() / "penguinconnect-local-bridge-data"))
).expanduser()
DB_PATH = Path(os.environ.get("PENGUIN_CONNECT_DB_PATH", str(DATA_DIR / "cache.db"))).expanduser()
APPLE_MESSAGES_DB = Path(
    os.environ.get("PENGUIN_CONNECT_APPLE_MESSAGES_DB_PATH", str(Path.home() / "Library" / "Messages" / "chat.db"))
).expanduser()
APPLE_MESSAGES_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
MIN_INITIAL_FULL_VERIFY_DELAY_MINUTES = 3 * 24 * 60
MAX_INITIAL_FULL_VERIFY_DELAY_MINUTES = 8 * 24 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT,
    last_name TEXT,
    organization TEXT,
    phone TEXT,
    phone_normalized TEXT,
    email TEXT,
    source_db TEXT,
    imported_at TEXT DEFAULT (datetime('now')),
    UNIQUE(phone_normalized, first_name, last_name)
);

CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);

CREATE TABLE IF NOT EXISTS penguin_connect_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_email TEXT NOT NULL UNIQUE,
    keychain_service TEXT NOT NULL,
    primary_send_as TEXT,
    send_as_aliases TEXT,
    status TEXT NOT NULL DEFAULT 'connected',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS penguin_connect_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_email TEXT NOT NULL,
    source_provider TEXT NOT NULL DEFAULT 'imessage',
    conversation_id TEXT NOT NULL UNIQUE,
    imessage_chat_id TEXT NOT NULL,
    imessage_chat_identifier TEXT,
    imessage_service_name TEXT,
    display_name TEXT,
    chat_type TEXT DEFAULT 'dm',
    participants TEXT,
    alias_email TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    gmail_thread_id TEXT,
    last_synced_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(gmail_email, source_provider, imessage_chat_id)
);

CREATE TABLE IF NOT EXISTS penguin_connect_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES penguin_connect_conversations(conversation_id) ON DELETE CASCADE,
    alias_email TEXT NOT NULL UNIQUE,
    alias_local_part TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    disconnected_at TEXT
);

CREATE TABLE IF NOT EXISTS penguin_connect_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES penguin_connect_conversations(conversation_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    gmail_message_id TEXT,
    gmail_thread_id TEXT,
    direction TEXT NOT NULL,
    sender_email TEXT,
    sender_name TEXT,
    subject TEXT,
    body_text TEXT,
    message_timestamp TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(conversation_id, provider_message_id)
);

CREATE TABLE IF NOT EXISTS penguin_connect_sync_state (
    conversation_id TEXT PRIMARY KEY REFERENCES penguin_connect_conversations(conversation_id) ON DELETE CASCADE,
    last_imessage_ts TEXT,
    last_gmail_ts TEXT,
    last_message_ts TEXT,
    last_gmail_history_id TEXT,
    pending_gmail_activity_at TEXT,
    initial_sync_completed_at TEXT,
    initial_sync_empty_verified_at TEXT,
    next_full_verify_at TEXT,
    full_verify_completed_at TEXT,
    last_synced_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS penguin_connect_poll_state (
    gmail_email TEXT PRIMARY KEY REFERENCES penguin_connect_accounts(gmail_email) ON DELETE CASCADE,
    last_gmail_history_id TEXT,
    gmail_rate_limited_until TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS penguin_connect_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    queue_name TEXT NOT NULL DEFAULT 'default',
    dedupe_key TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 12,
    next_run_at TEXT NOT NULL DEFAULT (datetime('now')),
    lease_until TEXT,
    lease_owner TEXT,
    last_error TEXT,
    result_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_penguin_connect_conv_status ON penguin_connect_conversations(gmail_email, source_provider, status);
CREATE INDEX IF NOT EXISTS idx_penguin_connect_alias_conv ON penguin_connect_aliases(conversation_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_penguin_connect_alias_one_active
ON penguin_connect_aliases(conversation_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_penguin_connect_msg_conv_ts ON penguin_connect_messages(conversation_id, message_timestamp);
CREATE INDEX IF NOT EXISTS idx_penguin_connect_msg_gmail ON penguin_connect_messages(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_penguin_connect_jobs_ready ON penguin_connect_jobs(job_type, status, next_run_at, id);
CREATE INDEX IF NOT EXISTS idx_penguin_connect_jobs_lease ON penguin_connect_jobs(job_type, status, lease_until);
CREATE UNIQUE INDEX IF NOT EXISTS idx_penguin_connect_jobs_active_dedupe
ON penguin_connect_jobs(dedupe_key)
WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'leased');
"""


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_provider(value: str | None) -> str:
    return (value or "imessage").strip().lower() or "imessage"


def _load_message_metadata(raw_value: str | None) -> dict:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_iso_value(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _max_iso_value(*values: str | None) -> str | None:
    candidates = [(parsed, value) for value in values if value for parsed in [_parse_iso_value(value)] if parsed]
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    for value in values:
        raw = (value or "").strip()
        if raw:
            return raw
    return None


def _min_iso_value(*values: str | None) -> str | None:
    candidates = [(parsed, value) for value in values if value for parsed in [_parse_iso_value(value)] if parsed]
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    for value in values:
        raw = (value or "").strip()
        if raw:
            return raw
    return None


def _merge_history_ids(*values: str | None) -> str | None:
    present = [(value or "").strip() for value in values if (value or "").strip()]
    if not present:
        return None
    numeric: list[tuple[int, str]] = []
    for value in present:
        try:
            numeric.append((int(value), value))
        except Exception:
            return present[0]
    return max(numeric, key=lambda item: item[0])[1]


def _initial_full_verify_delay_minutes(conversation_id: str) -> int:
    normalized = (conversation_id or "").strip() or "conversation"
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    span = max(0, MAX_INITIAL_FULL_VERIFY_DELAY_MINUTES - MIN_INITIAL_FULL_VERIFY_DELAY_MINUTES)
    return MIN_INITIAL_FULL_VERIFY_DELAY_MINUTES + (int.from_bytes(digest[:8], "big") % (span + 1))


def schedule_next_full_verify_at(conversation_id: str, *, base_iso: str | None = None) -> str:
    base_dt = _parse_iso_value(base_iso) or datetime.now(timezone.utc)
    due_dt = base_dt + timedelta(minutes=_initial_full_verify_delay_minutes(conversation_id))
    return due_dt.isoformat()


def _message_delivery_status(metadata: dict) -> str:
    return (metadata.get("delivery_status") or "pending").strip().lower() or "pending"


def _provider_aware_conversation_id(gmail_email: str, source_provider: str, source_chat_id: str) -> str:
    normalized_provider = _normalize_provider(source_provider)
    payload = f"{_normalize_email(gmail_email)}::{normalized_provider}::{source_chat_id}".encode("utf-8")
    return f"amc_{hashlib.sha256(payload).hexdigest()}"


def _apple_messages_provider(service_name: str | None) -> str | None:
    normalized = (service_name or "").strip().lower()
    if normalized in {"imessage", "sms", "rcs"}:
        return normalized
    return None


def _apple_messages_service_rank(service_name: str | None) -> int:
    normalized = (service_name or "").strip().lower()
    if normalized == "imessage":
        return 0
    if normalized == "rcs":
        return 1
    if normalized == "sms":
        return 2
    return 3


def _looks_like_apple_messages_guid(value: str | None) -> bool:
    parts = (value or "").strip().split(";")
    return len(parts) == 3 and all(parts)


def _apple_messages_ts_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return (APPLE_MESSAGES_EPOCH + timedelta(seconds=int(value) / 1_000_000_000)).isoformat()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _apple_messages_route_candidates_with_connection(
    messages_conn: sqlite3.Connection,
    chat_key: str | None,
) -> list[dict[str, str | int | None]]:
    lookup = (chat_key or "").strip()
    if not lookup:
        return []

    query = """
        SELECT
            c.ROWID,
            c.guid,
            c.chat_identifier,
            c.service_name,
            c.is_archived,
            MAX(m.date) AS last_message_date
        FROM chat c
        LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        LEFT JOIN message m ON m.ROWID = cmj.message_id
        WHERE {match_column} = ?
          AND c.service_name IN ('iMessage', 'SMS', 'RCS')
        GROUP BY c.ROWID
    """
    rows = []
    matched_by_guid = False
    if _looks_like_apple_messages_guid(lookup):
        rows = messages_conn.execute(query.format(match_column="c.guid"), (lookup,)).fetchall()
        matched_by_guid = bool(rows)
    if not rows:
        rows = messages_conn.execute(query.format(match_column="c.chat_identifier"), (lookup,)).fetchall()
    if not rows:
        return []

    candidates: list[dict[str, str | int | None]] = []
    for row in sorted(
        rows,
        key=lambda candidate: (
            _apple_messages_service_rank(candidate["service_name"]),
            int(candidate["is_archived"] or 0),
            -(int(candidate["last_message_date"] or 0)),
            -(int(candidate["ROWID"] or 0)),
        ),
    ):
        provider = _apple_messages_provider(row["service_name"])
        if not provider:
            continue
        candidates.append(
            {
                "rowid": row["ROWID"],
                "guid": row["guid"],
                "chat_identifier": row["chat_identifier"],
                "service_name": row["service_name"],
                "source_provider": provider,
                "last_message_at": _apple_messages_ts_to_iso(row["last_message_date"]),
                "matched_by_guid": matched_by_guid,
            }
        )
    return candidates


def _apple_messages_route_candidates(chat_key: str | None) -> list[dict[str, str | int | None]]:
    if not APPLE_MESSAGES_DB.exists():
        return []

    conn = sqlite3.connect(f"file:{APPLE_MESSAGES_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return _apple_messages_route_candidates_with_connection(conn, chat_key)
    finally:
        conn.close()


def _resolve_apple_messages_route(chat_key: str | None) -> dict[str, str] | None:
    candidates = _apple_messages_route_candidates(chat_key)
    if not candidates:
        return None
    best = candidates[0]
    return {
        "guid": str(best["guid"] or ""),
        "chat_identifier": str(best["chat_identifier"] or ""),
        "service_name": str(best["service_name"] or ""),
        "source_provider": str(best["source_provider"] or ""),
    }


def _load_conversation_import_timestamps(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    limit: int = 12,
) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT message_timestamp
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND provider = 'imessage'
             AND direction = 'imessage_to_email'
             AND message_timestamp IS NOT NULL
           ORDER BY message_timestamp DESC
           LIMIT ?""",
        (conversation_id, max(1, limit)),
    ).fetchall()
    return [(row["message_timestamp"] or "").strip() for row in rows if (row["message_timestamp"] or "").strip()]


def _load_candidate_message_timestamps(
    messages_conn: sqlite3.Connection,
    chat_rowid: int,
    *,
    limit: int = 24,
) -> set[str]:
    rows = messages_conn.execute(
        """SELECT DISTINCT m.date
           FROM message m
           JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
           WHERE cmj.chat_id = ?
             AND m.date IS NOT NULL
           ORDER BY m.date DESC
           LIMIT ?""",
        (chat_rowid, max(1, limit)),
    ).fetchall()
    return {
        iso_value
        for row in rows
        for iso_value in [_apple_messages_ts_to_iso(row["date"])]
        if iso_value
    }


def _resolve_apple_messages_route_for_conversation(
    conn: sqlite3.Connection,
    conversation_row: sqlite3.Row,
) -> dict[str, str] | None:
    current_chat_id = (conversation_row["imessage_chat_id"] or "").strip()
    current_identifier = (conversation_row["imessage_chat_identifier"] or "").strip()
    if _looks_like_apple_messages_guid(current_chat_id):
        return _resolve_apple_messages_route(current_chat_id)

    lookup = current_identifier or current_chat_id
    if not lookup or not APPLE_MESSAGES_DB.exists():
        return None

    messages_conn = sqlite3.connect(f"file:{APPLE_MESSAGES_DB}?mode=ro", uri=True)
    messages_conn.row_factory = sqlite3.Row
    try:
        candidates = _apple_messages_route_candidates_with_connection(messages_conn, lookup)
        if not candidates:
            return None
        if len(candidates) == 1:
            candidate = candidates[0]
            return {
                "guid": str(candidate["guid"] or ""),
                "chat_identifier": str(candidate["chat_identifier"] or ""),
                "service_name": str(candidate["service_name"] or ""),
                "source_provider": str(candidate["source_provider"] or ""),
            }

        current_service_name = (conversation_row["imessage_service_name"] or "").strip().lower()
        if current_service_name:
            service_matches = [
                candidate
                for candidate in candidates
                if (candidate["service_name"] or "").strip().lower() == current_service_name
            ]
            if len(service_matches) == 1:
                candidate = service_matches[0]
                return {
                    "guid": str(candidate["guid"] or ""),
                    "chat_identifier": str(candidate["chat_identifier"] or ""),
                    "service_name": str(candidate["service_name"] or ""),
                    "source_provider": str(candidate["source_provider"] or ""),
                }

        source_provider_hint = _normalize_provider(conversation_row["source_provider"])
        if source_provider_hint in {"sms", "rcs"}:
            provider_matches = [
                candidate for candidate in candidates if candidate["source_provider"] == source_provider_hint
            ]
            if len(provider_matches) == 1:
                candidate = provider_matches[0]
                return {
                    "guid": str(candidate["guid"] or ""),
                    "chat_identifier": str(candidate["chat_identifier"] or ""),
                    "service_name": str(candidate["service_name"] or ""),
                    "source_provider": str(candidate["source_provider"] or ""),
                }

        active_candidates = [candidate for candidate in candidates if (candidate.get("last_message_at") or "").strip()]
        if len(active_candidates) == 1:
            candidate = active_candidates[0]
            return {
                "guid": str(candidate["guid"] or ""),
                "chat_identifier": str(candidate["chat_identifier"] or ""),
                "service_name": str(candidate["service_name"] or ""),
                "source_provider": str(candidate["source_provider"] or ""),
            }

        observed_timestamps = _load_conversation_import_timestamps(conn, conversation_row["conversation_id"])
        if observed_timestamps:
            scored_candidates: list[tuple[int, dict[str, str | int | None]]] = []
            for candidate in candidates:
                candidate_timestamps = _load_candidate_message_timestamps(
                    messages_conn,
                    int(candidate["rowid"]),
                    limit=max(24, len(observed_timestamps) * 2),
                )
                overlap = sum(1 for timestamp in observed_timestamps if timestamp in candidate_timestamps)
                if overlap > 0:
                    scored_candidates.append((overlap, candidate))
            if scored_candidates:
                scored_candidates.sort(
                    key=lambda item: (
                        -item[0],
                        _apple_messages_service_rank(item[1]["service_name"]),
                        -(int(item[1]["rowid"] or 0)),
                    )
                )
                best_overlap, best_candidate = scored_candidates[0]
                second_overlap = scored_candidates[1][0] if len(scored_candidates) > 1 else -1
                if best_overlap > second_overlap:
                    return {
                        "guid": str(best_candidate["guid"] or ""),
                        "chat_identifier": str(best_candidate["chat_identifier"] or ""),
                        "service_name": str(best_candidate["service_name"] or ""),
                        "source_provider": str(best_candidate["source_provider"] or ""),
                    }
        return None
    finally:
        messages_conn.close()


def _merge_conversation_into_existing_target(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
) -> None:
    if not source_id or not target_id or source_id == target_id:
        return

    source_row = conn.execute(
        """SELECT gmail_thread_id, alias_email, status
           FROM penguin_connect_conversations
           WHERE conversation_id = ? LIMIT 1""",
        (source_id,),
    ).fetchone()
    if not source_row:
        return

    target_active_alias = conn.execute(
        """SELECT 1
           FROM penguin_connect_aliases
           WHERE conversation_id = ? AND status = 'active'
           LIMIT 1""",
        (target_id,),
    ).fetchone()
    if target_active_alias:
        conn.execute(
            """UPDATE penguin_connect_aliases
               SET status = 'disconnected',
                   disconnected_at = COALESCE(disconnected_at, datetime('now'))
               WHERE conversation_id = ? AND status = 'active'""",
            (source_id,),
        )
    conn.execute("UPDATE penguin_connect_aliases SET conversation_id = ? WHERE conversation_id = ?", (target_id, source_id))
    conn.execute("UPDATE OR IGNORE penguin_connect_messages SET conversation_id = ? WHERE conversation_id = ?", (target_id, source_id))
    conn.execute("DELETE FROM penguin_connect_messages WHERE conversation_id = ?", (source_id,))

    source_state = conn.execute(
        """SELECT last_imessage_ts, last_gmail_ts, last_message_ts, last_gmail_history_id,
                  initial_sync_completed_at, initial_sync_empty_verified_at
           FROM penguin_connect_sync_state
           WHERE conversation_id = ?""",
        (source_id,),
    ).fetchone()
    target_state = conn.execute(
        """SELECT last_imessage_ts, last_gmail_ts, last_message_ts, last_gmail_history_id,
                  initial_sync_completed_at, initial_sync_empty_verified_at
           FROM penguin_connect_sync_state
           WHERE conversation_id = ?""",
        (target_id,),
    ).fetchone()
    if source_state:
        merged_last_imessage = _max_iso_value(
            target_state["last_imessage_ts"] if target_state else None,
            source_state["last_imessage_ts"],
        )
        merged_last_gmail = _max_iso_value(
            target_state["last_gmail_ts"] if target_state else None,
            source_state["last_gmail_ts"],
        )
        merged_last_message = _max_iso_value(
            target_state["last_message_ts"] if target_state else None,
            source_state["last_message_ts"],
        )
        merged_history_id = _merge_history_ids(
            target_state["last_gmail_history_id"] if target_state else None,
            source_state["last_gmail_history_id"],
        )
        merged_initial_sync_completed_at = _min_iso_value(
            target_state["initial_sync_completed_at"] if target_state else None,
            source_state["initial_sync_completed_at"],
        )
        merged_initial_sync_empty_verified_at = _min_iso_value(
            target_state["initial_sync_empty_verified_at"] if target_state else None,
            source_state["initial_sync_empty_verified_at"],
        )
        conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, last_imessage_ts, last_gmail_ts, last_message_ts,
                last_gmail_history_id, initial_sync_completed_at, initial_sync_empty_verified_at, last_synced_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(conversation_id) DO UPDATE SET
                 last_imessage_ts = excluded.last_imessage_ts,
                 last_gmail_ts = excluded.last_gmail_ts,
                 last_message_ts = excluded.last_message_ts,
                 last_gmail_history_id = excluded.last_gmail_history_id,
                 initial_sync_completed_at = excluded.initial_sync_completed_at,
                 initial_sync_empty_verified_at = excluded.initial_sync_empty_verified_at,
                 last_synced_at = datetime('now'),
                 updated_at = datetime('now')""",
            (
                target_id,
                merged_last_imessage,
                merged_last_gmail,
                merged_last_message,
                merged_history_id,
                merged_initial_sync_completed_at,
                merged_initial_sync_empty_verified_at,
            ),
        )
        conn.execute("DELETE FROM penguin_connect_sync_state WHERE conversation_id = ?", (source_id,))

    conn.execute(
        """UPDATE penguin_connect_conversations
           SET gmail_thread_id = COALESCE(gmail_thread_id, ?),
               alias_email = COALESCE(alias_email, ?),
               status = CASE
                 WHEN status = 'active' OR ? = 'active' THEN 'active'
                 ELSE status
               END,
               updated_at = datetime('now')
           WHERE conversation_id = ?""",
        (
            source_row["gmail_thread_id"],
            source_row["alias_email"],
            source_row["status"],
            target_id,
        ),
    )
    conn.execute("DELETE FROM penguin_connect_conversations WHERE conversation_id = ?", (source_id,))


def _has_provider_aware_conversation_uniqueness(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA index_list(penguin_connect_conversations)").fetchall():
        if not row["unique"]:
            continue
        index_name = row["name"]
        safe_index_name = index_name.replace("'", "''")
        columns = [
            info["name"]
            for info in conn.execute(f"PRAGMA index_info('{safe_index_name}')").fetchall()
        ]
        if columns == ["gmail_email", "source_provider", "imessage_chat_id"]:
            return True
    return False


def _rebuild_conversations_table_for_provider_uniqueness(conn: sqlite3.Connection) -> None:
    conn.execute(
        """UPDATE penguin_connect_conversations
           SET source_provider = LOWER(COALESCE(NULLIF(source_provider, ''), 'imessage'))"""
    )
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS temp.penguin_connect_aliases__backup;
            DROP TABLE IF EXISTS temp.penguin_connect_messages__backup;
            DROP TABLE IF EXISTS temp.penguin_connect_sync_state__backup;
            CREATE TEMP TABLE penguin_connect_aliases__backup AS
            SELECT * FROM penguin_connect_aliases;
            CREATE TEMP TABLE penguin_connect_messages__backup AS
            SELECT * FROM penguin_connect_messages;
            CREATE TEMP TABLE penguin_connect_sync_state__backup AS
            SELECT * FROM penguin_connect_sync_state;

            DROP TABLE IF EXISTS penguin_connect_conversations__new;
            CREATE TABLE penguin_connect_conversations__new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_email TEXT NOT NULL,
                source_provider TEXT NOT NULL DEFAULT 'imessage',
                conversation_id TEXT NOT NULL UNIQUE,
                imessage_chat_id TEXT NOT NULL,
                imessage_chat_identifier TEXT,
                imessage_service_name TEXT,
                display_name TEXT,
                chat_type TEXT DEFAULT 'dm',
                participants TEXT,
                alias_email TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                gmail_thread_id TEXT,
                last_synced_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(gmail_email, source_provider, imessage_chat_id)
            );

            INSERT INTO penguin_connect_conversations__new (
                id,
                gmail_email,
                source_provider,
                conversation_id,
                imessage_chat_id,
                imessage_chat_identifier,
                imessage_service_name,
                display_name,
                chat_type,
                participants,
                alias_email,
                status,
                gmail_thread_id,
                last_synced_at,
                created_at,
                updated_at
            )
            SELECT
                id,
                gmail_email,
                LOWER(COALESCE(NULLIF(source_provider, ''), 'imessage')),
                conversation_id,
                imessage_chat_id,
                NULL,
                NULL,
                display_name,
                chat_type,
                participants,
                alias_email,
                status,
                gmail_thread_id,
                last_synced_at,
                created_at,
                updated_at
            FROM penguin_connect_conversations;

            DROP TABLE penguin_connect_conversations;
            ALTER TABLE penguin_connect_conversations__new RENAME TO penguin_connect_conversations;

            DELETE FROM penguin_connect_aliases;
            INSERT INTO penguin_connect_aliases SELECT * FROM temp.penguin_connect_aliases__backup;
            DELETE FROM penguin_connect_messages;
            INSERT INTO penguin_connect_messages SELECT * FROM temp.penguin_connect_messages__backup;
            DELETE FROM penguin_connect_sync_state;
            INSERT INTO penguin_connect_sync_state SELECT * FROM temp.penguin_connect_sync_state__backup;

            DROP TABLE temp.penguin_connect_aliases__backup;
            DROP TABLE temp.penguin_connect_messages__backup;
            DROP TABLE temp.penguin_connect_sync_state__backup;
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_legacy_conversation_ids(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT conversation_id, gmail_email, source_provider, imessage_chat_id
           FROM penguin_connect_conversations"""
    ).fetchall()
    updates: list[tuple[str, str, str]] = []
    for row in rows:
        source_chat_id = (row["imessage_chat_id"] or "").strip()
        if not source_chat_id:
            continue
        source_provider = (row["source_provider"] or "imessage").strip().lower() or "imessage"
        new_id = _provider_aware_conversation_id(row["gmail_email"], source_provider, source_chat_id)
        old_id = row["conversation_id"]
        if not old_id or old_id == new_id:
            continue
        if conn.execute(
            "SELECT 1 FROM penguin_connect_conversations WHERE conversation_id = ? LIMIT 1",
            (new_id,),
        ).fetchone():
            # Skip collision cases; discovery-time repair remains as a fallback.
            continue
        updates.append((old_id, new_id, source_provider))

    if not updates:
        return 0

    for old_id, new_id, source_provider in updates:
        temporary_provider = f"legacy-migrating:{old_id}"
        conn.execute(
            "UPDATE penguin_connect_conversations SET source_provider = ? WHERE conversation_id = ?",
            (temporary_provider, old_id),
        )
        conn.execute(
            """INSERT INTO penguin_connect_conversations (
                   gmail_email,
                   source_provider,
                   conversation_id,
                   imessage_chat_id,
                   imessage_chat_identifier,
                   imessage_service_name,
                   display_name,
                   chat_type,
                   participants,
                   alias_email,
                   status,
                   gmail_thread_id,
                   last_synced_at,
                   created_at,
                   updated_at
               )
               SELECT
                   gmail_email,
                   ?,
                   ?,
                   imessage_chat_id,
                   imessage_chat_identifier,
                   imessage_service_name,
                   display_name,
                   chat_type,
                   participants,
                   alias_email,
                   status,
                   gmail_thread_id,
                   last_synced_at,
                   created_at,
                   updated_at
               FROM penguin_connect_conversations
               WHERE conversation_id = ?""",
            (source_provider, new_id, old_id),
        )
        conn.execute("UPDATE penguin_connect_aliases SET conversation_id = ? WHERE conversation_id = ?", (new_id, old_id))
        conn.execute("UPDATE penguin_connect_messages SET conversation_id = ? WHERE conversation_id = ?", (new_id, old_id))
        conn.execute("UPDATE penguin_connect_sync_state SET conversation_id = ? WHERE conversation_id = ?", (new_id, old_id))
        conn.execute("DELETE FROM penguin_connect_conversations WHERE conversation_id = ?", (old_id,))

    return len(updates)


def _migrate_apple_messages_conversation_routes(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT conversation_id, gmail_email, source_provider, imessage_chat_id,
                  imessage_chat_identifier, imessage_service_name
           FROM penguin_connect_conversations
           WHERE source_provider IN ('imessage', 'sms', 'rcs')"""
    ).fetchall()

    migrated = 0
    for row in rows:
        old_id = (row["conversation_id"] or "").strip()
        current_chat_id = (row["imessage_chat_id"] or "").strip()
        current_identifier = (row["imessage_chat_identifier"] or "").strip()
        route = _resolve_apple_messages_route_for_conversation(conn, row)
        if not route or not route.get("guid") or not route.get("source_provider"):
            continue

        target_provider = route["source_provider"]
        target_chat_id = route["guid"]
        target_identifier = route.get("chat_identifier") or current_identifier or current_chat_id
        target_service_name = route.get("service_name") or row["imessage_service_name"]
        target_conversation_id = _provider_aware_conversation_id(row["gmail_email"], target_provider, target_chat_id)

        same_identity = (
            old_id == target_conversation_id
            and _normalize_provider(row["source_provider"]) == target_provider
            and current_chat_id == target_chat_id
        )
        if same_identity:
            conn.execute(
                """UPDATE penguin_connect_conversations
                   SET imessage_chat_identifier = ?, imessage_service_name = ?, updated_at = datetime('now')
                   WHERE conversation_id = ?""",
                (target_identifier, target_service_name, old_id),
            )
            continue

        existing_target = conn.execute(
            "SELECT conversation_id FROM penguin_connect_conversations WHERE conversation_id = ? LIMIT 1",
            (target_conversation_id,),
        ).fetchone()
        if existing_target and existing_target["conversation_id"] != old_id:
            _merge_conversation_into_existing_target(conn, old_id, target_conversation_id)
            migrated += 1
            continue

        temporary_provider = f"route-migrating:{old_id}"
        conn.execute(
            "UPDATE penguin_connect_conversations SET source_provider = ? WHERE conversation_id = ?",
            (temporary_provider, old_id),
        )
        conn.execute(
            """INSERT INTO penguin_connect_conversations (
                   gmail_email,
                   source_provider,
                   conversation_id,
                   imessage_chat_id,
                   imessage_chat_identifier,
                   imessage_service_name,
                   display_name,
                   chat_type,
                   participants,
                   alias_email,
                   status,
                   gmail_thread_id,
                   last_synced_at,
                   created_at,
                   updated_at
               )
               SELECT
                   gmail_email,
                   ?,
                   ?,
                   ?,
                   ?,
                   ?,
                   display_name,
                   chat_type,
                   participants,
                   alias_email,
                   status,
                   gmail_thread_id,
                   last_synced_at,
                   created_at,
                   updated_at
               FROM penguin_connect_conversations
               WHERE conversation_id = ?""",
            (
                target_provider,
                target_conversation_id,
                target_chat_id,
                target_identifier,
                target_service_name,
                old_id,
            ),
        )
        conn.execute("UPDATE penguin_connect_aliases SET conversation_id = ? WHERE conversation_id = ?", (target_conversation_id, old_id))
        conn.execute("UPDATE penguin_connect_messages SET conversation_id = ? WHERE conversation_id = ?", (target_conversation_id, old_id))
        conn.execute("UPDATE penguin_connect_sync_state SET conversation_id = ? WHERE conversation_id = ?", (target_conversation_id, old_id))
        conn.execute("DELETE FROM penguin_connect_conversations WHERE conversation_id = ?", (old_id,))
        migrated += 1

    return migrated


def _backfill_email_to_imessage_delivery_bodies(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT id, body_text, metadata
           FROM penguin_connect_messages
           WHERE provider = 'gmail' AND direction = 'email_to_imessage'"""
    ).fetchall()

    updated = 0
    for row in rows:
        current_body = (row["body_text"] or "").strip()
        metadata = _load_message_metadata(row["metadata"])

        source_text = ""
        source_html = ""
        for key in ("source_body_text_raw", "source_body_text"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                source_text = value
                break
        value = metadata.get("source_body_html_raw")
        if isinstance(value, str) and value.strip():
            source_html = value
        if not source_text and not source_html:
            source_text = current_body

        parsed = extract_latest_email_text(
            plain_text=source_text or None,
            html_text=source_html or None,
            snippet=current_body,
        )
        cleaned_body = parsed.text.strip()
        if (
            cleaned_body == current_body
            and metadata.get("quoted_context_count") in {None, 0}
            and metadata.get("gmail_body_source") == parsed.source
            and metadata.get("gmail_quoted_content_removed") == parsed.quoted_content_removed
            and metadata.get("gmail_signature_removed") == parsed.signature_removed
            and metadata.get("gmail_body_safe_for_send") == parsed.safe_for_send
            and metadata.get("gmail_body_safety_flags") == list(parsed.safety_flags)
        ):
            continue

        metadata.pop("quoted_context_count", None)
        metadata["source_body_text"] = cleaned_body
        if source_text:
            metadata["source_body_text_raw"] = source_text
        if source_html:
            metadata["source_body_html_raw"] = source_html
        metadata["gmail_body_source"] = parsed.source
        metadata["gmail_quoted_content_removed"] = parsed.quoted_content_removed
        metadata["gmail_signature_removed"] = parsed.signature_removed
        metadata["gmail_body_safe_for_send"] = parsed.safe_for_send
        metadata["gmail_body_safety_flags"] = list(parsed.safety_flags)
        conn.execute(
            "UPDATE penguin_connect_messages SET body_text = ?, metadata = ? WHERE id = ?",
            (cleaned_body[:20000], json.dumps(metadata), row["id"]),
        )
        updated += 1

    return updated


def _backfill_conversation_gmail_thread_ids(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT conversation_id, gmail_thread_id, COUNT(*) AS thread_count, MAX(message_timestamp) AS latest_message_at
           FROM penguin_connect_messages
           WHERE gmail_thread_id IS NOT NULL
             AND gmail_thread_id != ''
           GROUP BY conversation_id, gmail_thread_id"""
    ).fetchall()
    if not rows:
        return 0

    best_by_conversation: dict[str, sqlite3.Row] = {}
    for row in rows:
        conversation_id = row["conversation_id"]
        current = best_by_conversation.get(conversation_id)
        candidate_key = (
            int(row["thread_count"] or 0),
            row["latest_message_at"] or "",
            row["gmail_thread_id"] or "",
        )
        current_key = (
            int(current["thread_count"] or 0),
            current["latest_message_at"] or "",
            current["gmail_thread_id"] or "",
        ) if current else None
        if current is None or candidate_key > current_key:
            best_by_conversation[conversation_id] = row

    updated = 0
    for conversation_id, row in best_by_conversation.items():
        result = conn.execute(
            """UPDATE penguin_connect_conversations
               SET gmail_thread_id = ?,
                   updated_at = datetime('now')
               WHERE conversation_id = ?
                 AND (gmail_thread_id IS NULL OR gmail_thread_id = '')""",
            (row["gmail_thread_id"], conversation_id),
        )
        if result.rowcount:
            updated += 1
    return updated


def _repair_incomplete_bootstrap_state(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT s.conversation_id
           FROM penguin_connect_sync_state s
           JOIN penguin_connect_conversations c ON c.conversation_id = s.conversation_id
           WHERE c.status = 'active'
             AND s.initial_sync_completed_at IS NOT NULL
             AND s.initial_sync_empty_verified_at IS NULL
             AND NOT EXISTS (
               SELECT 1
               FROM penguin_connect_messages m
               WHERE m.conversation_id = s.conversation_id
                 AND m.direction = 'imessage_to_email'
                 AND m.gmail_message_id IS NOT NULL
             )"""
    ).fetchall()
    if not rows:
        return 0

    conversation_ids = [(row["conversation_id"],) for row in rows]
    conn.executemany(
        """UPDATE penguin_connect_sync_state
           SET initial_sync_completed_at = NULL,
               next_full_verify_at = NULL,
               full_verify_completed_at = NULL,
               updated_at = datetime('now')
           WHERE conversation_id = ?""",
        conversation_ids,
    )
    return len(conversation_ids)


def _allowed_sender_emails(gmail_email: str | None, send_as_aliases_raw: str | None) -> set[str]:
    allowed = {_normalize_email(gmail_email)}
    try:
        aliases = json.loads(send_as_aliases_raw or "[]")
    except Exception:
        aliases = []
    for alias in aliases or []:
        if not isinstance(alias, str):
            continue
        normalized = _normalize_email(alias)
        if normalized:
            allowed.add(normalized)
    return allowed


def _friendly_email_sender_name(sender_name: str | None, sender_email: str | None, *, own_sender: bool) -> str:
    raw_sender_name = (sender_name or "").strip()
    parsed_name, parsed_addr = email.utils.parseaddr(raw_sender_name)
    display_name = (parsed_name or "").strip()
    if display_name:
        return display_name
    if raw_sender_name and "@" not in raw_sender_name and "<" not in raw_sender_name and ">" not in raw_sender_name:
        return raw_sender_name
    normalized_sender = _normalize_email(sender_email or parsed_addr)
    if own_sender:
        return "Me"
    if normalized_sender:
        return normalized_sender
    return raw_sender_name


def _backfill_self_authored_sender_names(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT m.id, m.direction, m.sender_email, m.sender_name, m.metadata,
                  c.gmail_email, a.send_as_aliases
           FROM penguin_connect_messages m
           JOIN penguin_connect_conversations c ON c.conversation_id = m.conversation_id
           LEFT JOIN penguin_connect_accounts a ON a.gmail_email = c.gmail_email
           WHERE m.direction IN ('email_to_imessage', 'manual_to_imessage', 'imessage_to_email')"""
    ).fetchall()

    updated = 0
    for row in rows:
        metadata = _load_message_metadata(row["metadata"])
        target_sender_name = None

        if metadata.get("is_from_me"):
            target_sender_name = "Me"
        else:
            own_sender_emails = _allowed_sender_emails(row["gmail_email"], row["send_as_aliases"])
            if _normalize_email(row["sender_email"]) in own_sender_emails:
                target_sender_name = _friendly_email_sender_name(
                    row["sender_name"],
                    row["sender_email"],
                    own_sender=True,
                )

        if not target_sender_name:
            continue
        if (row["sender_name"] or "").strip() == target_sender_name:
            continue

        conn.execute("UPDATE penguin_connect_messages SET sender_name = ? WHERE id = ?", (target_sender_name, row["id"]))
        updated += 1

    return updated


def _backfill_initial_full_verify_schedule(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT conversation_id, initial_sync_completed_at, full_verify_completed_at
           FROM penguin_connect_sync_state
           WHERE initial_sync_completed_at IS NOT NULL
             AND next_full_verify_at IS NULL"""
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    for row in rows:
        base_iso = (
            row["full_verify_completed_at"]
            or row["initial_sync_completed_at"]
            or datetime.now(timezone.utc).isoformat()
        )
        due_at = schedule_next_full_verify_at(row["conversation_id"], base_iso=base_iso)
        conn.execute(
            "UPDATE penguin_connect_sync_state SET next_full_verify_at = ? WHERE conversation_id = ?",
            (due_at, row["conversation_id"]),
        )
        updated += 1
    return updated


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        sync_columns = {row[1] for row in conn.execute("PRAGMA table_info(penguin_connect_sync_state)").fetchall()}
        conversation_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(penguin_connect_conversations)").fetchall()
        }
        if "source_provider" not in conversation_columns:
            conn.execute("ALTER TABLE penguin_connect_conversations ADD COLUMN source_provider TEXT DEFAULT 'imessage'")
            conn.execute(
                """UPDATE penguin_connect_conversations
                   SET source_provider = LOWER(COALESCE(NULLIF(source_provider, ''), 'imessage'))"""
            )
        else:
            conn.execute(
                """UPDATE penguin_connect_conversations
                   SET source_provider = LOWER(COALESCE(NULLIF(source_provider, ''), 'imessage'))"""
            )
        if "imessage_chat_identifier" not in conversation_columns:
            conn.execute("ALTER TABLE penguin_connect_conversations ADD COLUMN imessage_chat_identifier TEXT")
        if "imessage_service_name" not in conversation_columns:
            conn.execute("ALTER TABLE penguin_connect_conversations ADD COLUMN imessage_service_name TEXT")
        conn.execute(
            """UPDATE penguin_connect_conversations
               SET imessage_chat_identifier = COALESCE(NULLIF(imessage_chat_identifier, ''), imessage_chat_id)
               WHERE source_provider IN ('imessage', 'sms', 'rcs')"""
        )
        if not _has_provider_aware_conversation_uniqueness(conn):
            _rebuild_conversations_table_for_provider_uniqueness(conn)
            conn.executescript(SCHEMA)
            conversation_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(penguin_connect_conversations)").fetchall()
            }
            if "imessage_chat_identifier" not in conversation_columns:
                conn.execute("ALTER TABLE penguin_connect_conversations ADD COLUMN imessage_chat_identifier TEXT")
            if "imessage_service_name" not in conversation_columns:
                conn.execute("ALTER TABLE penguin_connect_conversations ADD COLUMN imessage_service_name TEXT")
            conn.execute(
                """UPDATE penguin_connect_conversations
                   SET imessage_chat_identifier = COALESCE(NULLIF(imessage_chat_identifier, ''), imessage_chat_id)
                   WHERE source_provider IN ('imessage', 'sms', 'rcs')"""
            )
        _migrate_legacy_conversation_ids(conn)
        _migrate_apple_messages_conversation_routes(conn)
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_penguin_connect_conv_provider_status
               ON penguin_connect_conversations(gmail_email, source_provider, status)"""
        )
        if "initial_sync_completed_at" not in sync_columns:
            conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN initial_sync_completed_at TEXT")
        if "initial_sync_empty_verified_at" not in sync_columns:
            conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN initial_sync_empty_verified_at TEXT")
        if "last_message_ts" not in sync_columns:
            conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN last_message_ts TEXT")
        if "pending_gmail_activity_at" not in sync_columns:
            conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN pending_gmail_activity_at TEXT")
        if "next_full_verify_at" not in sync_columns:
            conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN next_full_verify_at TEXT")
        if "full_verify_completed_at" not in sync_columns:
            conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN full_verify_completed_at TEXT")
        conn.execute(
            """UPDATE penguin_connect_sync_state
               SET initial_sync_completed_at = COALESCE(initial_sync_completed_at, last_synced_at, updated_at)
               WHERE initial_sync_completed_at IS NULL
                 AND EXISTS (
                   SELECT 1
                   FROM penguin_connect_messages m
                   WHERE m.conversation_id = penguin_connect_sync_state.conversation_id
                     AND m.direction = 'imessage_to_email'
                     AND m.gmail_message_id IS NOT NULL
                 )"""
        )
        conn.execute(
            """UPDATE penguin_connect_sync_state
               SET last_message_ts = CASE
                 WHEN last_imessage_ts IS NOT NULL AND last_gmail_ts IS NOT NULL
                   THEN CASE WHEN last_imessage_ts >= last_gmail_ts THEN last_imessage_ts ELSE last_gmail_ts END
                 ELSE COALESCE(last_imessage_ts, last_gmail_ts, last_message_ts)
               END
               WHERE last_message_ts IS NULL"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_penguin_connect_sync_bootstrap ON penguin_connect_sync_state(initial_sync_completed_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_penguin_connect_sync_last_message ON penguin_connect_sync_state(last_message_ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_penguin_connect_sync_pending_gmail ON penguin_connect_sync_state(pending_gmail_activity_at)"
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_penguin_connect_sync_next_full_verify
               ON penguin_connect_sync_state(next_full_verify_at, full_verify_completed_at)"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_penguin_connect_poll_rate_limit ON penguin_connect_poll_state(gmail_rate_limited_until)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_penguin_connect_jobs_finished ON penguin_connect_jobs(status, finished_at)"
        )
        _backfill_email_to_imessage_delivery_bodies(conn)
        _backfill_conversation_gmail_thread_ids(conn)
        _repair_incomplete_bootstrap_state(conn)
        _backfill_self_authored_sender_names(conn)
        _backfill_initial_full_verify_schedule(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
