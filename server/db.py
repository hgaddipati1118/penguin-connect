"""SQLite database for the PenguinConnect macOS local bridge."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(
    os.environ.get("PENGUIN_CONNECT_DATA_DIR", str(Path.home() / "penguinconnect-local-bridge-data"))
).expanduser()
DB_PATH = Path(os.environ.get("PENGUIN_CONNECT_DB_PATH", str(DATA_DIR / "cache.db"))).expanduser()

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
    conversation_id TEXT NOT NULL UNIQUE,
    imessage_chat_id TEXT NOT NULL,
    display_name TEXT,
    chat_type TEXT DEFAULT 'dm',
    participants TEXT,
    alias_email TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    gmail_thread_id TEXT,
    last_synced_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(gmail_email, imessage_chat_id)
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
    initial_sync_completed_at TEXT,
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

CREATE INDEX IF NOT EXISTS idx_penguin_connect_conv_status ON penguin_connect_conversations(gmail_email, status);
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
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(penguin_connect_sync_state)").fetchall()}
    if "initial_sync_completed_at" not in columns:
        conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN initial_sync_completed_at TEXT")
    if "last_message_ts" not in columns:
        conn.execute("ALTER TABLE penguin_connect_sync_state ADD COLUMN last_message_ts TEXT")
    conn.execute(
        """UPDATE penguin_connect_sync_state
           SET initial_sync_completed_at = COALESCE(initial_sync_completed_at, last_synced_at, updated_at)
           WHERE initial_sync_completed_at IS NULL
             AND (last_imessage_ts IS NOT NULL OR last_gmail_ts IS NOT NULL OR last_synced_at IS NOT NULL)"""
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_penguin_connect_sync_last_message ON penguin_connect_sync_state(last_message_ts)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_penguin_connect_poll_rate_limit ON penguin_connect_poll_state(gmail_rate_limited_until)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_penguin_connect_jobs_finished ON penguin_connect_jobs(status, finished_at)"
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
