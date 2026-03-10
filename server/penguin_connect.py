"""Local macOS PenguinConnect bridge: messaging channels <-> Gmail (conversation-centric)."""

from __future__ import annotations

import base64
from email import policy
import email.utils
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Optional

from channels import get_channel_adapter

DEFAULT_BACKFILL_DAYS = 7
SYNC_MODES = {"startup_catchup", "backfill", "incremental"}
KEYCHAIN_PREFIX = "penguinconnect-local-bridge.gmail"
PENGUINCONNECT_HEADER = "X-PenguinConnect-Bridge"
GMAIL_SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]
_sync_lock = threading.Lock()
DEFAULT_RETRY_BASE_SECONDS = 30
DEFAULT_RETRY_MAX_BACKOFF_SECONDS = 900
DEFAULT_MAX_RETRIES = 8
DEFAULT_MESSAGE_ID_DOMAIN = "penguinconnect.local"
MAX_REFERENCE_CHAIN = 20
RFC_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")
DEFAULT_MAX_EMAIL_ATTACHMENT_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_IMESSAGE_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_QUOTED_CONTEXT_MESSAGES = 5
MAX_QUOTED_CONTEXT_CHARS = 160
THREAD_REPAIR_HEADER_VALUE = "thread_repair"
MAX_SYNC_WINDOW_HOURS = 24 * 60
DEFAULT_GMAIL_HTTP_TIMEOUT_SECONDS = 60
MIN_GMAIL_HTTP_TIMEOUT_SECONDS = 5
MAX_GMAIL_HTTP_TIMEOUT_SECONDS = 300
DEFAULT_INCREMENTAL_CONVERSATIONS_PER_RUN = 1
MAX_INCREMENTAL_CONVERSATIONS_PER_RUN = 20
DEFAULT_INCREMENTAL_ACTIVITY_WINDOW_MINUTES = 360
MAX_INCREMENTAL_ACTIVITY_WINDOW_MINUTES = 24 * 60
DEFAULT_GMAIL_API_MAX_RETRIES = 3
DEFAULT_GMAIL_API_MAX_BACKOFF_SECONDS = 30
DEFAULT_GMAIL_RATE_LIMIT_PAUSE_SECONDS = 120
DEFAULT_GMAIL_BACKFILL_WRITE_PAUSE_SECONDS = 0.15
MAX_GMAIL_BACKFILL_WRITE_PAUSE_SECONDS = 5.0
SYNC_JOB_TYPE = "sync_conversations"
SYNC_JOB_QUEUE = "sync"
DEFAULT_SYNC_JOB_MAX_ATTEMPTS = 12
DEFAULT_SYNC_JOB_LEASE_SECONDS = 180
DEFAULT_SYNC_JOB_RETRY_BASE_SECONDS = 30
DEFAULT_SYNC_JOB_RETRY_MAX_BACKOFF_SECONDS = 1800
FULL_IMESSAGE_SYNC_SINCE = datetime(2001, 1, 1, tzinfo=timezone.utc).isoformat()
FULL_GMAIL_SYNC_SINCE = datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()
_sync_runtime_lock = threading.Lock()
_UNSET = object()
_IMESSAGE_CHANNEL = get_channel_adapter("imessage")


class _GmailRetryableError(RuntimeError):
    def __init__(self, retry_after_seconds: int, status: Optional[int], reason: str):
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds
        self.status = status
        self.reason = reason


def _new_sync_runtime_state() -> dict[str, Any]:
    return {
        "running": False,
        "mode": None,
        "started_at": None,
        "finished_at": None,
        "last_completed_at": None,
        "selected_conversations": 0,
        "processed_conversations": 0,
        "current_conversation_id": None,
        "current_display_name": None,
        "selection_strategy": None,
        "selection_cutoff": None,
        "last_error": None,
        "last_result": None,
    }


_sync_runtime = _new_sync_runtime_state()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _normalize_source_provider(value: Optional[str]) -> str:
    return (value or "imessage").strip().lower() or "imessage"


def _source_provider_label(source_provider: Optional[str]) -> str:
    normalized = _normalize_source_provider(source_provider)
    try:
        adapter = get_channel_adapter(normalized)
    except KeyError:
        adapter = None
    if adapter:
        label = getattr(adapter, "provider_label", "")
        if label:
            return label
    if normalized == "whatsapp":
        return "WhatsApp"
    if normalized == "telegram":
        return "Telegram"
    return normalized.replace("_", " ").replace("-", " ").title() or "Messaging"


def _conversation_source_provider(conv: sqlite3.Row | dict[str, Any]) -> str:
    try:
        return _normalize_source_provider(conv["source_provider"])
    except Exception:
        return "imessage"


def _conversation_source_chat_id(conv: sqlite3.Row | dict[str, Any]) -> str:
    for key in ("source_chat_id", "imessage_chat_id"):
        try:
            value = (conv[key] or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def _source_adapter_for_provider(source_provider: Optional[str]):
    return get_channel_adapter(_normalize_source_provider(source_provider))


def _source_adapter_for_conversation(conv: sqlite3.Row | dict[str, Any]):
    return _source_adapter_for_provider(_conversation_source_provider(conv))


def _provider_subject(source_provider: Optional[str], display_name: Optional[str]) -> str:
    return f"{_source_provider_label(source_provider)} · {(display_name or '').strip() or 'Conversation'}"


def _strip_provider_subject(subject: Optional[str], source_provider: Optional[str]) -> str:
    normalized_subject = (subject or "").strip()
    prefix = f"{_source_provider_label(source_provider)} · "
    if normalized_subject.startswith(prefix):
        return normalized_subject[len(prefix) :].strip()
    return normalized_subject


def _send_to_source_conversation(
    conv: sqlite3.Row | dict[str, Any],
    message_text: str,
    *,
    attachment_paths: Optional[list[str]] = None,
) -> tuple[bool, Optional[str]]:
    source_provider = _conversation_source_provider(conv)
    source_chat_id = _conversation_source_chat_id(conv)
    if source_provider == "imessage":
        return send_imessage(source_chat_id, message_text, attachment_paths=attachment_paths)
    adapter = _source_adapter_for_provider(source_provider)
    return adapter.send_message(source_chat_id, message_text, attachment_paths=attachment_paths)


def browse_imessage_chats(search=None, limit=100):
    return _IMESSAGE_CHANNEL.list_conversations(search=search, limit=limit)


def list_recent_imessage_chat_activity(since, limit=500):
    return _IMESSAGE_CHANNEL.list_recent_activity(since, limit=limit)


def fetch_imessage_messages(chat_id, limit=50, since=None):
    return _IMESSAGE_CHANNEL.fetch_messages(chat_id, limit=limit, since=since)


def send_imessage(
    chat_identifier: str,
    message_text: str,
    attachment_paths: Optional[list[str]] = None,
) -> tuple[bool, Optional[str]]:
    return _IMESSAGE_CHANNEL.send_message(chat_identifier, message_text, attachment_paths=attachment_paths)


def _get_imessage_unread_count(chat_identifier: str) -> Optional[int]:
    return _IMESSAGE_CHANNEL.get_unread_count(chat_identifier)


def _resolve_imessage_sender_and_subject(
    conn: sqlite3.Connection,
    conv: sqlite3.Row,
    msg: dict[str, Any],
) -> tuple[str, str]:
    return _IMESSAGE_CHANNEL.resolve_sender_and_subject(
        conn,
        conv,
        msg,
        lookup_contact_name=_lookup_contact_name,
        looks_like_unresolved_handle=_looks_like_unresolved_handle,
    )


def _normalize_sync_hours(hours: Optional[int]) -> Optional[int]:
    if hours is None:
        return None
    try:
        value = int(hours)
    except Exception:
        return None
    return max(1, min(value, MAX_SYNC_WINDOW_HOURS))


def _incremental_conversations_per_run() -> int:
    return _env_int(
        "PENGUIN_CONNECT_INCREMENTAL_CONVERSATIONS_PER_RUN",
        DEFAULT_INCREMENTAL_CONVERSATIONS_PER_RUN,
        1,
        MAX_INCREMENTAL_CONVERSATIONS_PER_RUN,
    )


def _incremental_activity_window_minutes() -> int:
    return _env_int(
        "PENGUIN_CONNECT_INCREMENTAL_ACTIVITY_WINDOW_MINUTES",
        DEFAULT_INCREMENTAL_ACTIVITY_WINDOW_MINUTES,
        5,
        MAX_INCREMENTAL_ACTIVITY_WINDOW_MINUTES,
    )


def _gmail_api_max_retries() -> int:
    return _env_int("PENGUIN_CONNECT_GMAIL_API_MAX_RETRIES", DEFAULT_GMAIL_API_MAX_RETRIES, 0, 10)


def _gmail_rate_limit_pause_seconds() -> int:
    return _env_int("PENGUIN_CONNECT_GMAIL_RATE_LIMIT_PAUSE_SECONDS", DEFAULT_GMAIL_RATE_LIMIT_PAUSE_SECONDS, 10, 3600)


def _gmail_api_max_backoff_seconds() -> int:
    return _env_int(
        "PENGUIN_CONNECT_GMAIL_API_MAX_BACKOFF_SECONDS",
        DEFAULT_GMAIL_API_MAX_BACKOFF_SECONDS,
        1,
        3600,
    )


def _sync_window_cutoff(days: int, hours: Optional[int]) -> datetime:
    normalized_hours = _normalize_sync_hours(hours)
    if normalized_hours is not None:
        return datetime.now(timezone.utc) - timedelta(hours=normalized_hours)
    return datetime.now(timezone.utc) - timedelta(days=days)


def _incremental_activity_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=_incremental_activity_window_minutes())


def _recent_activity_sort_value(value: Optional[str]) -> datetime:
    dt = _parse_iso(value)
    return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)


def _sync_due_sort_value(conv: sqlite3.Row) -> datetime:
    for key in ("last_synced_at", "last_message_ts", "last_gmail_ts", "last_imessage_ts", "created_at", "updated_at"):
        dt = _parse_iso(conv[key] if key in conv.keys() else None)
        if dt:
            return dt
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _get_poll_state(conn: sqlite3.Connection, gmail_email: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM penguin_connect_poll_state WHERE gmail_email = ? LIMIT 1",
        (_normalize_email(gmail_email),),
    ).fetchone()


def _upsert_poll_state(
    conn: sqlite3.Connection,
    gmail_email: str,
    *,
    last_gmail_history_id=_UNSET,
    gmail_rate_limited_until=_UNSET,
):
    gmail_email = _normalize_email(gmail_email)
    existing = _get_poll_state(conn, gmail_email)
    if existing:
        if last_gmail_history_id is _UNSET:
            last_gmail_history_id = existing["last_gmail_history_id"]
        if gmail_rate_limited_until is _UNSET:
            gmail_rate_limited_until = existing["gmail_rate_limited_until"]
    else:
        if last_gmail_history_id is _UNSET:
            last_gmail_history_id = None
        if gmail_rate_limited_until is _UNSET:
            gmail_rate_limited_until = None
    conn.execute(
        """INSERT INTO penguin_connect_poll_state
           (gmail_email, last_gmail_history_id, gmail_rate_limited_until, created_at, updated_at)
           VALUES (?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(gmail_email) DO UPDATE SET
             last_gmail_history_id = excluded.last_gmail_history_id,
             gmail_rate_limited_until = excluded.gmail_rate_limited_until,
             updated_at = datetime('now')""",
        (gmail_email, last_gmail_history_id, gmail_rate_limited_until),
    )


def _clear_gmail_rate_limit_pause(conn: sqlite3.Connection, gmail_email: str):
    poll_state = _get_poll_state(conn, gmail_email)
    if not poll_state or not poll_state["gmail_rate_limited_until"]:
        return
    _upsert_poll_state(conn, gmail_email, gmail_rate_limited_until=None)


def _set_gmail_rate_limit_pause(conn: sqlite3.Connection, gmail_email: str, retry_after_seconds: Optional[int]) -> str:
    pause_seconds = max(_gmail_rate_limit_pause_seconds(), int(retry_after_seconds or 0))
    paused_until = (datetime.now(timezone.utc) + timedelta(seconds=pause_seconds)).isoformat()
    _upsert_poll_state(conn, gmail_email, gmail_rate_limited_until=paused_until)
    return paused_until


def _active_gmail_rate_limit_pause(conn: sqlite3.Connection, gmail_email: str) -> Optional[dict[str, Any]]:
    poll_state = _get_poll_state(conn, gmail_email)
    if not poll_state or not poll_state["gmail_rate_limited_until"]:
        return None
    paused_until = _parse_iso(poll_state["gmail_rate_limited_until"])
    now_dt = datetime.now(timezone.utc)
    if not paused_until or paused_until <= now_dt:
        _clear_gmail_rate_limit_pause(conn, gmail_email)
        return None
    return {
        "paused_until": paused_until.isoformat(),
        "retry_after_seconds": max(1, int((paused_until - now_dt).total_seconds())),
    }


def _gmail_rate_limit_skip_result(
    mode: str,
    days: int,
    hours: Optional[int],
    gmail_email: str,
    retry_after_seconds: int,
    paused_until: str,
) -> dict[str, Any]:
    return {
        "success": True,
        "mode": mode,
        "days": days,
        "hours": hours,
        "gmail_email": gmail_email,
        "skipped": True,
        "reason": "gmail_rate_limited",
        "retry_after_seconds": retry_after_seconds,
        "rate_limited_until": paused_until,
    }


def _extract_gmail_error_status(exc: Exception) -> Optional[int]:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except Exception:
        return None


def _extract_gmail_error_headers(exc: Exception) -> dict[str, Any]:
    resp = getattr(exc, "resp", None)
    headers = getattr(resp, "headers", None)
    return headers if isinstance(headers, dict) else dict(headers or {})


def _extract_gmail_retry_after_seconds(exc: Exception) -> Optional[int]:
    headers = _extract_gmail_error_headers(exc)
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except Exception:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(str(raw))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(1, int((parsed - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None


def _gmail_error_reason(exc: Exception) -> str:
    return (getattr(exc, "reason", None) or str(exc) or exc.__class__.__name__).strip()


def _is_retryable_gmail_error(exc: Exception) -> bool:
    status = _extract_gmail_error_status(exc)
    if status == 429:
        return True
    if status != 403:
        return False
    reason = _gmail_error_reason(exc).lower()
    return any(token in reason for token in ("ratelimit", "quota", "too many requests", "user rate limit"))


def _gmail_execute(fn):
    max_retries = _gmail_api_max_retries()
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            if not _is_retryable_gmail_error(exc):
                raise
            retry_after_seconds = _extract_gmail_retry_after_seconds(exc)
            if attempt >= max_retries:
                raise _GmailRetryableError(
                    retry_after_seconds or _gmail_rate_limit_pause_seconds(),
                    _extract_gmail_error_status(exc),
                    _gmail_error_reason(exc) or "gmail_retryable_error",
                ) from exc
            base_wait = min(2 ** (attempt + 1), _gmail_api_max_backoff_seconds())
            jitter = base_wait * 0.1 * (attempt + 1)
            wait_time = max(float(retry_after_seconds or 0), base_wait + jitter)
            time.sleep(wait_time)


def _select_conversations_for_sync(
    conn: sqlite3.Connection,
    gmail_email: str,
    mode: str,
    days: int,
    hours: Optional[int],
    verify_all: bool = False,
    gmail_service=None,
) -> tuple[list[sqlite3.Row], dict[str, Any]]:
    conversations = conn.execute(
        """SELECT c.*,
                  s.last_imessage_ts,
                  s.last_gmail_ts,
                  s.last_message_ts,
                  s.last_gmail_history_id,
                  s.initial_sync_completed_at,
                  s.last_synced_at AS sync_state_last_synced_at
           FROM penguin_connect_conversations c
           LEFT JOIN penguin_connect_sync_state s ON s.conversation_id = c.conversation_id
           WHERE c.gmail_email = ? AND c.status = 'active'
           ORDER BY c.updated_at DESC""",
        (gmail_email,),
    ).fetchall()

    selection = {
        "discovered_conversations": len(conversations),
        "selected_conversations": len(conversations),
        "selection_strategy": "all_active",
    }
    if not conversations:
        return conversations, selection

    if mode == "incremental":
        hot_cutoff_iso = _incremental_activity_cutoff().isoformat()
        recent = list_recent_imessage_chat_activity(hot_cutoff_iso, limit=len(conversations))
        recent_by_chat = {
            row.get("chat_id"): row
            for row in recent.get("chats", [])
            if isinstance(row, dict) and row.get("chat_id")
        }
        gmail_activity, gmail_meta = ({}, {})
        if gmail_service is not None:
            gmail_activity, gmail_meta = _list_recent_gmail_alias_activity(conn, gmail_service, gmail_email, conversations)

        hot: list[sqlite3.Row] = []
        pending: list[sqlite3.Row] = []
        round_robin: list[sqlite3.Row] = []
        hot_imessage = 0
        hot_gmail = 0

        for conv in conversations:
            has_hot_imessage = False
            recent_row = recent_by_chat.get(conv["imessage_chat_id"])
            if recent_row:
                recent_ts = recent_row.get("last_message_at")
                has_hot_imessage = bool(recent_ts and _recent_activity_sort_value(recent_ts) > _recent_activity_sort_value(conv["last_imessage_ts"]))

            gmail_row = gmail_activity.get(conv["conversation_id"])
            gmail_ts = gmail_row.get("last_message_at") if gmail_row else None
            has_hot_gmail = bool(gmail_ts and _recent_activity_sort_value(gmail_ts) > _recent_activity_sort_value(conv["last_gmail_ts"]))

            if has_hot_imessage:
                hot_imessage += 1
            if has_hot_gmail:
                hot_gmail += 1

            if has_hot_imessage or has_hot_gmail:
                hot.append(conv)
            elif not conv["initial_sync_completed_at"]:
                pending.append(conv)
            else:
                round_robin.append(conv)

        hot.sort(key=lambda conv: (_sync_due_sort_value(conv), conv["conversation_id"]))
        pending.sort(key=lambda conv: (_sync_due_sort_value(conv), conv["conversation_id"]))
        round_robin.sort(key=lambda conv: (_sync_due_sort_value(conv), conv["conversation_id"]))

        queued = hot + pending + round_robin
        limit = _incremental_conversations_per_run()
        selected = queued[:limit]
        selection["queued_conversations"] = len(queued)
        selection["selected_conversations"] = len(selected)
        selection["selection_limit"] = limit
        selection["pending_bootstrap_conversations"] = len(pending) + sum(1 for conv in hot if not conv["initial_sync_completed_at"])
        selection["bootstrapped_conversations"] = len(conversations) - selection["pending_bootstrap_conversations"]
        selection["hot_conversations"] = len(hot)
        selection["hot_imessage_conversations"] = hot_imessage
        selection["hot_gmail_conversations"] = hot_gmail
        selection["selection_cutoff"] = hot_cutoff_iso
        if gmail_meta.get("history_initialized"):
            selection["gmail_history_initialized"] = True
        if gmail_meta.get("history_reset"):
            selection["gmail_history_reset"] = True
        if hot:
            selection["selection_strategy"] = "activity_prioritized_round_robin"
        elif pending:
            selection["selection_strategy"] = "pending_bootstrap_round_robin"
        else:
            selection["selection_strategy"] = "round_robin_oldest_synced"
        return selected, selection

    if mode == "startup_catchup":
        selected = [conv for conv in conversations if not conv["initial_sync_completed_at"]]
        hot_cutoff_iso = _incremental_activity_cutoff().isoformat()
        recent = list_recent_imessage_chat_activity(hot_cutoff_iso, limit=len(selected) or 1)
        recent_by_chat = {
            row.get("chat_id"): row
            for row in recent.get("chats", [])
            if isinstance(row, dict) and row.get("chat_id")
        }
        hot = [conv for conv in selected if conv["imessage_chat_id"] in recent_by_chat]
        hot.sort(
            key=lambda conv: (
                recent_by_chat[conv["imessage_chat_id"]].get("first_message_at") or "",
                conv["conversation_id"],
            )
        )
        cold = [conv for conv in selected if conv["imessage_chat_id"] not in recent_by_chat]
        cold.sort(key=lambda conv: (_sync_due_sort_value(conv), conv["conversation_id"]))
        queued = hot + cold
        limit = _incremental_conversations_per_run()
        selected = queued[:limit]
        selection["queued_conversations"] = len(queued)
        selection["selected_conversations"] = len(selected)
        selection["selection_limit"] = limit
        selection["bootstrapped_conversations"] = len(conversations) - len(queued)
        selection["pending_bootstrap_conversations"] = len(queued)
        selection["selection_cutoff"] = hot_cutoff_iso
        selection["selection_strategy"] = (
            "pending_bootstrap_recent_imessage_activity" if hot else "pending_bootstrap_round_robin"
        )
        return selected, selection

    if mode != "backfill":
        return conversations, selection

    if verify_all:
        selected = sorted(conversations, key=lambda conv: (_sync_due_sort_value(conv), conv["conversation_id"]))
        selection["selected_conversations"] = len(selected)
        selection["pending_bootstrap_conversations"] = sum(1 for conv in selected if not conv["initial_sync_completed_at"])
        selection["bootstrapped_conversations"] = len(selected) - selection["pending_bootstrap_conversations"]
        selection["selection_strategy"] = "verify_all_active"
        return selected, selection

    cutoff_iso = _sync_window_cutoff(days, hours).isoformat()
    recent = list_recent_imessage_chat_activity(cutoff_iso, limit=len(conversations))
    if not recent.get("available"):
        selection["selection_strategy"] = "all_active_fallback"
        selection["selection_reason"] = recent.get("reason") or "recent_activity_unavailable"
        return conversations, selection

    recent_by_chat = {
        row.get("chat_id"): row
        for row in recent.get("chats", [])
        if isinstance(row, dict) and row.get("chat_id")
    }
    selected = [conv for conv in conversations if conv["imessage_chat_id"] in recent_by_chat]
    selected.sort(
        key=lambda conv: (
            recent_by_chat[conv["imessage_chat_id"]].get("first_message_at") or "",
            conv["conversation_id"],
        )
    )

    selection["selected_conversations"] = len(selected)
    selection["selection_strategy"] = "recent_imessage_activity"
    selection["selection_cutoff"] = cutoff_iso
    return selected, selection


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _iso_from_gmail_internal_date(value: Optional[str]) -> str:
    if not value:
        return _now_iso()
    try:
        ms = int(value)
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return _now_iso()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "")
    try:
        value = float(raw)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _gmail_backfill_write_pause_seconds() -> float:
    return _env_float(
        "PENGUIN_CONNECT_BACKFILL_WRITE_PAUSE_SECONDS",
        DEFAULT_GMAIL_BACKFILL_WRITE_PAUSE_SECONDS,
        0.0,
        MAX_GMAIL_BACKFILL_WRITE_PAUSE_SECONDS,
    )


def _sync_gmail_write_pause_seconds(mode: str, verify_all: bool) -> float:
    normalized_mode = (mode or "").strip().lower()
    if verify_all or normalized_mode in {"backfill", "startup_catchup"}:
        return _gmail_backfill_write_pause_seconds()
    return 0.0


def _sleep_after_gmail_write(write_pause_seconds: float) -> None:
    if write_pause_seconds <= 0:
        return
    time.sleep(write_pause_seconds)


def _sync_job_max_attempts() -> int:
    return _env_int("PENGUIN_CONNECT_SYNC_JOB_MAX_ATTEMPTS", DEFAULT_SYNC_JOB_MAX_ATTEMPTS, 1, 100)


def _sync_job_lease_seconds() -> int:
    return _env_int("PENGUIN_CONNECT_SYNC_JOB_LEASE_SECONDS", DEFAULT_SYNC_JOB_LEASE_SECONDS, 30, 24 * 3600)


def _sync_job_retry_base_seconds() -> int:
    return _env_int("PENGUIN_CONNECT_SYNC_JOB_RETRY_BASE_SECONDS", DEFAULT_SYNC_JOB_RETRY_BASE_SECONDS, 1, 3600)


def _sync_job_retry_max_backoff_seconds() -> int:
    return _env_int(
        "PENGUIN_CONNECT_SYNC_JOB_RETRY_MAX_BACKOFF_SECONDS",
        DEFAULT_SYNC_JOB_RETRY_MAX_BACKOFF_SECONDS,
        1,
        24 * 3600,
    )


def _sync_job_retry_backoff_seconds(attempt_count: int) -> int:
    base = _sync_job_retry_base_seconds()
    max_backoff = _sync_job_retry_max_backoff_seconds()
    return min(max_backoff, base * (2 ** max(0, attempt_count - 1)))


def _retry_base_seconds() -> int:
    return _env_int("PENGUIN_CONNECT_RETRY_BASE_SECONDS", DEFAULT_RETRY_BASE_SECONDS, 1, 3600)


def _retry_max_backoff_seconds() -> int:
    return _env_int("PENGUIN_CONNECT_RETRY_MAX_BACKOFF_SECONDS", DEFAULT_RETRY_MAX_BACKOFF_SECONDS, 5, 86400)


def _retry_max_retries() -> int:
    return _env_int("PENGUIN_CONNECT_MAX_RETRIES", DEFAULT_MAX_RETRIES, 1, 50)


def _gmail_http_timeout_seconds() -> int:
    return _env_int(
        "PENGUIN_CONNECT_GMAIL_HTTP_TIMEOUT_SECONDS",
        DEFAULT_GMAIL_HTTP_TIMEOUT_SECONDS,
        MIN_GMAIL_HTTP_TIMEOUT_SECONDS,
        MAX_GMAIL_HTTP_TIMEOUT_SECONDS,
    )


def _load_metadata(raw_value: Optional[str]) -> dict[str, Any]:
    try:
        if not raw_value:
            return {}
        data = json.loads(raw_value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _metadata_int(metadata: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(metadata.get(key))
    except Exception:
        return default


def _apply_retry_defaults(metadata: dict[str, Any]) -> dict[str, Any]:
    metadata["max_retries"] = _metadata_int(metadata, "max_retries", _retry_max_retries())
    metadata["retry_count"] = _metadata_int(metadata, "retry_count", 0)
    return metadata


def _retry_backoff_seconds(retry_count: int) -> int:
    base = _retry_base_seconds()
    max_backoff = _retry_max_backoff_seconds()
    # retry_count is 1-indexed after a failed attempt.
    return min(max_backoff, base * (2 ** max(0, retry_count - 1)))


def _should_attempt_delivery_retry(metadata: dict[str, Any], now_dt: datetime) -> bool:
    metadata = _apply_retry_defaults(metadata)
    status = (metadata.get("delivery_status") or "").strip().lower()
    if status in {"delivered", "failed_permanent", "blocked", "ignored"}:
        return False

    if metadata["retry_count"] >= metadata["max_retries"]:
        metadata["delivery_status"] = "failed_permanent"
        metadata["next_retry_at"] = None
        return False

    next_retry_at = _parse_iso(metadata.get("next_retry_at"))
    return not (next_retry_at and next_retry_at > now_dt)


def _mark_delivery_success(metadata: dict[str, Any], result_key: str, result_value: str) -> dict[str, Any]:
    metadata = _apply_retry_defaults(metadata)
    metadata["delivery_status"] = "delivered"
    metadata["last_error"] = None
    metadata["next_retry_at"] = None
    metadata[result_key] = result_value
    return metadata


def _mark_delivery_failure(metadata: dict[str, Any], result_key: str, error: str, now_dt: datetime) -> dict[str, Any]:
    metadata = _apply_retry_defaults(metadata)
    retry_count = metadata["retry_count"] + 1
    metadata["retry_count"] = retry_count
    metadata["last_retry_at"] = now_dt.isoformat()
    metadata["first_failed_at"] = metadata.get("first_failed_at") or now_dt.isoformat()
    metadata["last_error"] = (error or "delivery_failed").strip()
    metadata[result_key] = metadata["last_error"]

    if retry_count >= metadata["max_retries"]:
        metadata["delivery_status"] = "failed_permanent"
        metadata["next_retry_at"] = None
    else:
        metadata["delivery_status"] = "pending"
        metadata["next_retry_at"] = (now_dt + timedelta(seconds=_retry_backoff_seconds(retry_count))).isoformat()

    return metadata


def _delivery_status(direction: str, gmail_message_id: Optional[str], metadata: dict[str, Any]) -> str:
    if metadata.get("blocked"):
        return "blocked"
    if metadata.get("ignored"):
        return "ignored"

    status = (metadata.get("delivery_status") or "").strip().lower()
    if status:
        return status

    if direction == "imessage_to_email":
        return "delivered" if gmail_message_id else "pending"
    if direction == "email_to_imessage":
        send_result = (metadata.get("send_result") or "").strip().lower()
        return "delivered" if send_result.endswith("_ok") else "pending"
    return "pending"


def _normalize_rfc_message_id(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None

    matches = RFC_MESSAGE_ID_RE.findall(value)
    if matches:
        return matches[0]

    token = value.strip("<>").strip()
    if "@" not in token or any(c.isspace() for c in token):
        return None
    return f"<{token}>"


def _normalize_rfc_message_id_list(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            msg_id = _normalize_rfc_message_id(str(item))
            if msg_id:
                values.append(msg_id)
    elif isinstance(raw, str):
        matches = RFC_MESSAGE_ID_RE.findall(raw)
        if matches:
            values.extend(matches)
        else:
            msg_id = _normalize_rfc_message_id(raw)
            if msg_id:
                values.append(msg_id)

    deduped: list[str] = []
    seen = set()
    for msg_id in values:
        if msg_id in seen:
            continue
        seen.add(msg_id)
        deduped.append(msg_id)
    if len(deduped) > MAX_REFERENCE_CHAIN:
        deduped = deduped[-MAX_REFERENCE_CHAIN:]
    return deduped


def _append_reference_id(reference_chain: list[str], msg_id: Optional[str]) -> list[str]:
    out = _normalize_rfc_message_id_list(reference_chain)
    normalized = _normalize_rfc_message_id(msg_id)
    if normalized:
        out = [item for item in out if item != normalized]
        out.append(normalized)
    if len(out) > MAX_REFERENCE_CHAIN:
        out = out[-MAX_REFERENCE_CHAIN:]
    return out


def _build_bridge_rfc_message_id(conversation_id: str, provider_message_id: str) -> str:
    domain = (os.environ.get("PENGUIN_CONNECT_MESSAGE_ID_DOMAIN") or DEFAULT_MESSAGE_ID_DOMAIN).strip().lower()
    if not domain or " " in domain:
        domain = DEFAULT_MESSAGE_ID_DOMAIN
    digest = hashlib.sha1(f"{conversation_id}:{provider_message_id}".encode("utf-8")).hexdigest()
    return f"<am.{digest}@{domain}>"


def _build_repair_rfc_message_id(conversation_id: str, gmail_message_id: str) -> str:
    domain = (os.environ.get("PENGUIN_CONNECT_MESSAGE_ID_DOMAIN") or DEFAULT_MESSAGE_ID_DOMAIN).strip().lower()
    if not domain or " " in domain:
        domain = DEFAULT_MESSAGE_ID_DOMAIN
    digest = hashlib.sha1(f"{conversation_id}:repair:{gmail_message_id}".encode("utf-8")).hexdigest()
    return f"<am-repair.{digest}@{domain}>"


def _is_bridge_rfc_message_id(raw: Optional[str]) -> bool:
    normalized = _normalize_rfc_message_id(raw)
    if not normalized:
        return False
    return normalized.startswith("<am.") or normalized.startswith("<am-repair.")


def _message_is_present_in_gmail(row: sqlite3.Row) -> bool:
    return row["provider"] == "gmail" or bool(row["gmail_message_id"])


def _load_conversation_rfc_rows(conn: sqlite3.Connection, conversation_id: str, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id, provider, provider_message_id, gmail_message_id, gmail_thread_id,
                  sender_email, sender_name, body_text, message_timestamp, metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ?
           ORDER BY message_timestamp DESC, id DESC
           LIMIT ?""",
        (conversation_id, max(1, min(limit, 5000))),
    ).fetchall()


def _resolve_canonical_gmail_thread_id(
    conn: sqlite3.Connection,
    conversation_id: str,
    preferred_thread_id: Optional[str] = None,
    observed_thread_ids: Optional[list[str]] = None,
) -> Optional[str]:
    rows = conn.execute(
        """SELECT gmail_thread_id, COUNT(*) AS thread_count, MAX(message_timestamp) AS latest_message_at
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND gmail_thread_id IS NOT NULL
             AND gmail_thread_id != ''
           GROUP BY gmail_thread_id""",
        (conversation_id,),
    ).fetchall()

    thread_stats: dict[str, tuple[int, str]] = {}
    for row in rows:
        thread_id = (row["gmail_thread_id"] or "").strip()
        if not thread_id:
            continue
        thread_stats[thread_id] = (int(row["thread_count"] or 0), row["latest_message_at"] or "")

    preferred = (preferred_thread_id or "").strip()
    if preferred and preferred in thread_stats:
        return preferred

    if thread_stats:
        ranked = sorted(
            thread_stats.items(),
            key=lambda item: (-item[1][0], -( _parse_iso(item[1][1]).timestamp() if _parse_iso(item[1][1]) else 0), item[0]),
        )
        return ranked[0][0]

    for thread_id in observed_thread_ids or []:
        normalized = (thread_id or "").strip()
        if normalized:
            return normalized

    return preferred or None


def _apply_canonical_thread_reconciliation(
    conn: sqlite3.Connection,
    conversation_id: str,
    canonical_thread_id: Optional[str],
):
    canonical = (canonical_thread_id or "").strip()
    if not canonical:
        return

    rows = conn.execute(
        """SELECT id, gmail_thread_id, metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND gmail_thread_id IS NOT NULL
             AND gmail_thread_id != ''""",
        (conversation_id,),
    ).fetchall()

    for row in rows:
        observed = (row["gmail_thread_id"] or "").strip()
        metadata = _load_metadata(row["metadata"])
        changed = False
        if observed and observed != canonical:
            if metadata.get("gmail_observed_thread_id") != observed:
                metadata["gmail_observed_thread_id"] = observed
                changed = True
            if metadata.get("canonical_gmail_thread_id") != canonical:
                metadata["canonical_gmail_thread_id"] = canonical
                changed = True
            if not metadata.get("thread_reconciled"):
                metadata["thread_reconciled"] = True
                changed = True
        else:
            if metadata.get("canonical_gmail_thread_id") and metadata.get("canonical_gmail_thread_id") != canonical:
                metadata["canonical_gmail_thread_id"] = canonical
                changed = True
            if metadata.get("gmail_observed_thread_id") == canonical and metadata.get("thread_reconciled"):
                metadata.pop("gmail_observed_thread_id", None)
                metadata["thread_reconciled"] = False
                changed = True
        if changed:
            conn.execute(
                "UPDATE penguin_connect_messages SET metadata = ? WHERE id = ?",
                (json.dumps(metadata), row["id"]),
            )

    conn.execute(
        "UPDATE penguin_connect_conversations SET gmail_thread_id = ?, last_synced_at = datetime('now') WHERE conversation_id = ?",
        (canonical, conversation_id),
    )


def _thread_is_bridge_owned(conn: sqlite3.Connection, conversation_id: str, gmail_thread_id: Optional[str]) -> bool:
    thread_id = (gmail_thread_id or "").strip()
    if not thread_id:
        return False
    row = conn.execute(
        """SELECT metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ? AND gmail_thread_id = ?
           ORDER BY message_timestamp ASC, id ASC
           LIMIT 1""",
        (conversation_id, thread_id),
    ).fetchone()
    if not row:
        return False
    metadata = _load_metadata(row["metadata"])
    return _is_bridge_rfc_message_id(metadata.get("rfc_message_id"))


def _load_conversation_rfc_context(
    conn: sqlite3.Connection,
    conversation_id: str,
    preferred_thread_id: Optional[str] = None,
) -> tuple[Optional[str], list[str]]:
    rows = _load_conversation_rfc_rows(conn, conversation_id, limit=500)

    def first_match(candidate_rows: list[sqlite3.Row]) -> tuple[Optional[str], list[str]]:
        for row in candidate_rows:
            if not _message_is_present_in_gmail(row):
                continue
            metadata = _load_metadata(row["metadata"])
            message_id = _normalize_rfc_message_id(metadata.get("rfc_message_id"))
            if not message_id:
                continue
            references = _normalize_rfc_message_id_list(metadata.get("rfc_references"))
            references = _append_reference_id(references, message_id)
            return message_id, references
        return None, []

    preferred = (preferred_thread_id or "").strip()
    if preferred:
        preferred_rows = [row for row in rows if (row["gmail_thread_id"] or "").strip() == preferred]
        message_id, references = first_match(preferred_rows)
        if message_id:
            return message_id, references

    return first_match(rows)


def _quote_preview(text: str) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= MAX_QUOTED_CONTEXT_CHARS:
        return normalized
    return normalized[: MAX_QUOTED_CONTEXT_CHARS - 3].rstrip() + "..."


def _quote_sender_label(row: dict[str, Any]) -> str:
    sender_name = (row.get("sender_name") or "").strip()
    if sender_name:
        parsed_name, _ = email.utils.parseaddr(sender_name)
        return (parsed_name or sender_name).strip()
    sender_email = (row.get("sender_email") or "").strip()
    if sender_email:
        return sender_email
    return "Unknown"


def _build_reply_quote_lines(
    conn: sqlite3.Connection,
    conversation_id: str,
    rfc_in_reply_to: Optional[str],
    rfc_references: list[str],
) -> list[str]:
    rows = _load_conversation_rfc_rows(conn, conversation_id, limit=500)
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = _load_metadata(row["metadata"])
        row_data = {
            "sender_name": row["sender_name"],
            "sender_email": row["sender_email"],
            "body_text": row["body_text"] or "",
            "rfc_in_reply_to": _normalize_rfc_message_id(metadata.get("rfc_in_reply_to")),
        }
        for key in ("rfc_message_id", "repaired_from_rfc_message_id"):
            message_id = _normalize_rfc_message_id(metadata.get(key))
            if not message_id or message_id in index:
                continue
            index[message_id] = row_data

    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    current = _normalize_rfc_message_id(rfc_in_reply_to)
    while current and current not in visited and len(chain) < MAX_QUOTED_CONTEXT_MESSAGES:
        visited.add(current)
        row = index.get(current)
        if not row:
            break
        chain.append(row)
        current = row.get("rfc_in_reply_to")

    if not chain:
        for current in reversed(_normalize_rfc_message_id_list(rfc_references)):
            if current in visited:
                continue
            row = index.get(current)
            if not row:
                continue
            visited.add(current)
            chain.append(row)
            if len(chain) >= MAX_QUOTED_CONTEXT_MESSAGES:
                break

    lines: list[str] = []
    for row in reversed(chain):
        preview = _quote_preview(row.get("body_text") or "")
        if not preview:
            continue
        lines.append(f"> {_quote_sender_label(row)}: {preview}")
    return lines


def _compose_imessage_delivery_body(
    conn: sqlite3.Connection,
    conversation_id: str,
    body_text: str,
    rfc_in_reply_to: Optional[str],
    rfc_references: list[str],
) -> tuple[str, int]:
    base_body = (body_text or "").strip()
    quote_lines = _build_reply_quote_lines(conn, conversation_id, rfc_in_reply_to, rfc_references)
    if not quote_lines:
        return base_body, 0
    if base_body:
        return f"{base_body}\n\nQuoted context:\n" + "\n".join(quote_lines), len(quote_lines)
    return "Quoted context:\n" + "\n".join(quote_lines), len(quote_lines)

def _resolve_gmail_thread_by_rfc_message_id(gmail_service, parent_rfc_message_id: Optional[str]) -> Optional[str]:
    normalized = _normalize_rfc_message_id(parent_rfc_message_id)
    if not normalized:
        return None

    query_candidates = [normalized, normalized.strip("<>")]
    for query_value in query_candidates:
        try:
            listing = _gmail_execute(
                lambda query_value=query_value: gmail_service.users().messages().list(
                    userId="me",
                    q=f"rfc822msgid:{query_value}",
                    maxResults=1,
                    includeSpamTrash=True,
                ).execute()
            )
        except Exception:
            continue

        messages = listing.get("messages") or []
        if not messages:
            continue
        thread_id = messages[0].get("threadId")
        if thread_id:
            return thread_id

        message_id = messages[0].get("id")
        if not message_id:
            continue
        try:
            full = _gmail_execute(
                lambda message_id=message_id: gmail_service.users().messages().get(
                    userId="me",
                    id=message_id,
                    format="minimal",
                ).execute()
            )
            thread_id = full.get("threadId")
            if thread_id:
                return thread_id
        except Exception:
            continue

    return None


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def _contact_display_name(row: Optional[sqlite3.Row]) -> Optional[str]:
    if not row:
        return None
    first = (row["first_name"] or "").strip()
    last = (row["last_name"] or "").strip()
    name = f"{first} {last}".strip()
    if name:
        return name
    organization = (row["organization"] or "").strip()
    return organization or None


def _lookup_contact_name(conn: sqlite3.Connection, participant: str) -> Optional[str]:
    p = (participant or "").strip()
    if not p:
        return None
    if "@" in p and not p.startswith("+"):
        row = conn.execute(
            "SELECT first_name, last_name, organization FROM contacts WHERE lower(email) = lower(?) LIMIT 1",
            (p,),
        ).fetchone()
        return _contact_display_name(row)

    phone_key = _normalize_phone(p)
    if not phone_key:
        return None
    row = conn.execute(
        "SELECT first_name, last_name, organization FROM contacts WHERE phone_normalized LIKE ? LIMIT 1",
        (f"%{phone_key}",),
    ).fetchone()
    return _contact_display_name(row)


def _looks_like_unresolved_handle(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return True
    return not any(ch.isalpha() for ch in candidate)


def deterministic_conversation_id(
    gmail_email: str,
    source_chat_id: str,
    source_provider: str = "imessage",
) -> str:
    normalized_provider = _normalize_source_provider(source_provider)
    payload = f"{_normalize_email(gmail_email)}::{normalized_provider}::{source_chat_id}".encode("utf-8")
    return f"amc_{hashlib.sha256(payload).hexdigest()}"


def _legacy_conversation_id(gmail_email: str, source_chat_id: str) -> str:
    payload = f"{_normalize_email(gmail_email)}::{source_chat_id}".encode("utf-8")
    return f"amc_{hashlib.sha256(payload).hexdigest()}"


def _migrate_conversation_id(conn: sqlite3.Connection, old_id: str, new_id: str, source_provider: str) -> None:
    if not old_id or not new_id or old_id == new_id:
        return
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


def _keychain_service_name(gmail_email: str) -> str:
    return f"{KEYCHAIN_PREFIX}.{_normalize_email(gmail_email)}"


def _read_keychain_secret(service: str, account: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None
    except Exception:
        return None


def _write_keychain_secret(service: str, account: str, secret: str) -> bool:
    try:
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                service,
                "-a",
                account,
                "-w",
                secret,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def connect_gmail_account(conn: sqlite3.Connection, gmail_email: str, token_json: dict[str, Any]) -> dict[str, Any]:
    gmail_email = _normalize_email(gmail_email)
    if "@" not in gmail_email:
        return {"success": False, "error": "invalid_gmail_email"}

    required = ["token", "refresh_token", "client_id", "client_secret"]
    missing = [k for k in required if not token_json.get(k)]
    if missing:
        return {"success": False, "error": f"missing_token_fields:{','.join(missing)}"}

    payload = {
        "token": token_json.get("token"),
        "refresh_token": token_json.get("refresh_token"),
        "token_uri": token_json.get("token_uri") or "https://oauth2.googleapis.com/token",
        "client_id": token_json.get("client_id"),
        "client_secret": token_json.get("client_secret"),
        "scopes": token_json.get("scopes") or GMAIL_SCOPES,
        "expiry": token_json.get("expiry"),
    }
    service_name = _keychain_service_name(gmail_email)
    if not _write_keychain_secret(service_name, gmail_email, json.dumps(payload)):
        return {"success": False, "error": "failed_to_store_keychain_token"}

    conn.execute(
        """INSERT INTO penguin_connect_accounts
           (gmail_email, keychain_service, status, created_at, updated_at)
           VALUES (?, ?, 'connected', datetime('now'), datetime('now'))
           ON CONFLICT(gmail_email) DO UPDATE SET
             keychain_service = excluded.keychain_service,
             status = 'connected',
             updated_at = datetime('now')""",
        (gmail_email, service_name),
    )

    service, err = _build_gmail_service(gmail_email, service_name)
    send_as = []
    primary = gmail_email
    if service and not err:
        send_as, primary = _refresh_send_as_aliases(conn, service, gmail_email)

    return {
        "success": True,
        "gmail_email": gmail_email,
        "keychain_service": service_name,
        "primary_send_as": primary,
        "send_as_aliases": send_as,
    }


def get_connected_account(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM penguin_connect_accounts
           WHERE status = 'connected'
           ORDER BY updated_at DESC, id DESC
           LIMIT 1"""
    ).fetchone()


def get_gmail_connection_status(conn: sqlite3.Connection) -> dict[str, Any]:
    account = get_connected_account(conn)
    if not account:
        return {"connected": False}
    aliases = []
    try:
        aliases = json.loads(account["send_as_aliases"] or "[]")
    except Exception:
        pass
    return {
        "connected": True,
        "gmail_email": account["gmail_email"],
        "primary_send_as": account["primary_send_as"] or account["gmail_email"],
        "send_as_aliases": aliases,
        "updated_at": account["updated_at"],
    }


def get_runtime_sync_status() -> dict[str, Any]:
    with _sync_runtime_lock:
        return dict(_sync_runtime)


def _sync_runtime_started(mode: str, selection: dict[str, Any]) -> None:
    with _sync_runtime_lock:
        _sync_runtime.update(
            {
                "running": True,
                "mode": mode,
                "started_at": _now_iso(),
                "finished_at": None,
                "selected_conversations": selection.get("selected_conversations", 0),
                "processed_conversations": 0,
                "current_conversation_id": None,
                "current_display_name": None,
                "selection_strategy": selection.get("selection_strategy"),
                "selection_cutoff": selection.get("selection_cutoff"),
                "last_error": None,
                "last_result": None,
            }
        )


def _sync_runtime_progress(processed_conversations: int, conv: Optional[sqlite3.Row] = None) -> None:
    with _sync_runtime_lock:
        _sync_runtime["processed_conversations"] = processed_conversations
        _sync_runtime["current_conversation_id"] = conv["conversation_id"] if conv else None
        _sync_runtime["current_display_name"] = conv["display_name"] if conv else None


def _sync_runtime_finished(result: Optional[dict[str, Any]] = None, error: Optional[str] = None) -> None:
    finished_at = _now_iso()
    with _sync_runtime_lock:
        _sync_runtime["running"] = False
        _sync_runtime["finished_at"] = finished_at
        _sync_runtime["current_conversation_id"] = None
        _sync_runtime["current_display_name"] = None
        _sync_runtime["last_error"] = error
        if result is not None:
            _sync_runtime["last_result"] = result
            if result.get("success"):
                _sync_runtime["last_completed_at"] = finished_at


def _default_sync_job_owner() -> str:
    return f"pid:{os.getpid()}"


def _sync_job_payload(mode: str, days: int, hours: Optional[int], verify_all: bool) -> dict[str, Any]:
    normalized_mode = (mode or "incremental").strip().lower()
    normalized_days = max(1, min(int(days or DEFAULT_BACKFILL_DAYS), 60))
    normalized_hours = _normalize_sync_hours(hours)
    return {
        "mode": normalized_mode,
        "days": normalized_days,
        "hours": normalized_hours,
        "verify_all": bool(verify_all),
    }


def _sync_job_dedupe_key(payload: dict[str, Any]) -> Optional[str]:
    mode = (payload.get("mode") or "").strip().lower()
    verify_all = bool(payload.get("verify_all"))
    days = max(1, min(int(payload.get("days") or DEFAULT_BACKFILL_DAYS), 60))
    hours = _normalize_sync_hours(payload.get("hours"))

    if mode == "incremental":
        return "sync:incremental"
    if mode == "startup_catchup":
        return "sync:startup_catchup"
    if mode == "backfill":
        if verify_all:
            return "sync:backfill:verify_all"
        if hours is not None:
            return f"sync:backfill:hours:{hours}"
        return f"sync:backfill:days:{days}"
    return None


def enqueue_sync_job(
    conn: sqlite3.Connection,
    *,
    mode: str,
    days: int,
    hours: Optional[int],
    verify_all: bool,
    dedupe: bool = True,
) -> dict[str, Any]:
    payload = _sync_job_payload(mode, days, hours, verify_all)
    dedupe_key = _sync_job_dedupe_key(payload) if dedupe else None
    payload_json = json.dumps(payload, sort_keys=True)
    now_iso = _now_iso()

    try:
        cursor = conn.execute(
            """INSERT INTO penguin_connect_jobs
               (job_type, queue_name, dedupe_key, payload_json, status, attempt_count, max_attempts, next_run_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)""",
            (
                SYNC_JOB_TYPE,
                SYNC_JOB_QUEUE,
                dedupe_key,
                payload_json,
                _sync_job_max_attempts(),
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        return {
            "enqueued": True,
            "job_id": cursor.lastrowid,
            "dedupe_key": dedupe_key,
            "payload": payload,
        }
    except sqlite3.IntegrityError:
        if not dedupe_key:
            raise
        existing = conn.execute(
            """SELECT id, status, attempt_count, next_run_at
               FROM penguin_connect_jobs
               WHERE dedupe_key = ?
                 AND status IN ('queued', 'leased')
               ORDER BY id DESC
               LIMIT 1""",
            (dedupe_key,),
        ).fetchone()
        if not existing:
            raise
        return {
            "enqueued": False,
            "job_id": existing["id"],
            "dedupe_key": dedupe_key,
            "job_status": existing["status"],
            "attempt_count": int(existing["attempt_count"] or 0),
            "next_run_at": existing["next_run_at"],
            "payload": payload,
        }


def _recover_expired_sync_job_leases(conn: sqlite3.Connection) -> int:
    now_iso = _now_iso()
    cursor = conn.execute(
        """UPDATE penguin_connect_jobs
           SET status = 'queued',
               lease_until = NULL,
               lease_owner = NULL,
               updated_at = ?
           WHERE job_type = ?
             AND status = 'leased'
             AND lease_until IS NOT NULL
             AND lease_until <= ?""",
        (now_iso, SYNC_JOB_TYPE, now_iso),
    )
    return int(cursor.rowcount or 0)


def _lease_next_sync_job(conn: sqlite3.Connection, owner: str) -> Optional[sqlite3.Row]:
    _recover_expired_sync_job_leases(conn)
    now_iso = _now_iso()
    row = conn.execute(
        """SELECT *
           FROM penguin_connect_jobs
           WHERE job_type = ?
             AND status = 'queued'
             AND next_run_at <= ?
           ORDER BY next_run_at ASC, id ASC
           LIMIT 1""",
        (SYNC_JOB_TYPE, now_iso),
    ).fetchone()
    if not row:
        return None

    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=_sync_job_lease_seconds())).isoformat()
    updated = conn.execute(
        """UPDATE penguin_connect_jobs
           SET status = 'leased',
               lease_owner = ?,
               lease_until = ?,
               started_at = COALESCE(started_at, ?),
               updated_at = ?
           WHERE id = ?
             AND status = 'queued'""",
        (owner, lease_until, now_iso, now_iso, row["id"]),
    )
    if updated.rowcount <= 0:
        return None
    return conn.execute("SELECT * FROM penguin_connect_jobs WHERE id = ?", (row["id"],)).fetchone()


def _mark_sync_job_succeeded(conn: sqlite3.Connection, job_id: int, result: dict[str, Any]) -> None:
    now_iso = _now_iso()
    conn.execute(
        """UPDATE penguin_connect_jobs
           SET status = 'succeeded',
               lease_until = NULL,
               lease_owner = NULL,
               last_error = NULL,
               result_json = ?,
               finished_at = ?,
               updated_at = ?
           WHERE id = ?""",
        (json.dumps(result), now_iso, now_iso, job_id),
    )


def _mark_sync_job_failed(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    *,
    error_text: str,
    result: Optional[dict[str, Any]] = None,
    retry_after_seconds: Optional[int] = None,
) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    attempt_count = int(job["attempt_count"] or 0) + 1
    max_attempts = max(1, int(job["max_attempts"] or _sync_job_max_attempts()))
    payload = json.loads(job["payload_json"] or "{}") if job["payload_json"] else {}
    failure_result = dict(result or {})
    failure_result.update(
        {
            "success": False,
            "error": error_text,
            "queue_job_id": int(job["id"]),
            "queue_job_attempt": attempt_count,
        }
    )

    if attempt_count >= max_attempts:
        conn.execute(
            """UPDATE penguin_connect_jobs
               SET status = 'failed',
                   attempt_count = ?,
                   lease_until = NULL,
                   lease_owner = NULL,
                   last_error = ?,
                   result_json = ?,
                   finished_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (attempt_count, error_text, json.dumps(failure_result), now_iso, now_iso, job["id"]),
        )
        failure_result["queue_job_status"] = "failed"
        failure_result["queue_job_retry_scheduled"] = False
        return failure_result

    retry_seconds = int(retry_after_seconds or 0)
    if retry_seconds <= 0:
        retry_seconds = _sync_job_retry_backoff_seconds(attempt_count)
    next_run_at = (now_dt + timedelta(seconds=retry_seconds)).isoformat()
    conn.execute(
        """UPDATE penguin_connect_jobs
           SET status = 'queued',
               attempt_count = ?,
               lease_until = NULL,
               lease_owner = NULL,
               last_error = ?,
               next_run_at = ?,
               result_json = ?,
               updated_at = ?
           WHERE id = ?""",
        (
            attempt_count,
            error_text,
            next_run_at,
            json.dumps(failure_result),
            now_iso,
            job["id"],
        ),
    )
    failure_result.update(
        {
            "queue_job_status": "queued",
            "queue_job_retry_scheduled": True,
            "queue_job_next_run_at": next_run_at,
            "queue_job_retry_after_seconds": retry_seconds,
            "mode": payload.get("mode"),
        }
    )
    return failure_result


def _load_sync_job_payload(job: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(job["payload_json"] or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return _sync_job_payload(
        payload.get("mode", "incremental"),
        payload.get("days", DEFAULT_BACKFILL_DAYS),
        payload.get("hours"),
        bool(payload.get("verify_all")),
    )


def run_sync_job_worker_once(conn: sqlite3.Connection, owner: Optional[str] = None) -> dict[str, Any]:
    owner = (owner or "").strip() or _default_sync_job_owner()
    job = _lease_next_sync_job(conn, owner)
    if not job:
        return {"success": True, "skipped": True, "reason": "queue_idle"}

    payload = _load_sync_job_payload(job)
    try:
        result = sync_conversations(
            conn,
            mode=payload["mode"],
            days=int(payload["days"]),
            hours=payload.get("hours"),
            verify_all=bool(payload.get("verify_all")),
        )
    except sqlite3.OperationalError as exc:
        result = _map_sync_sqlite_error(exc)
    except Exception as exc:
        result = {"success": False, "error": str(exc).strip() or exc.__class__.__name__}

    result = dict(result or {})
    result["queue_job_id"] = int(job["id"])
    result["queue_job_attempt"] = int(job["attempt_count"] or 0) + 1

    if result.get("success"):
        if result.get("skipped") and result.get("reason") == "gmail_rate_limited":
            retry_after = result.get("retry_after_seconds")
            queued = _mark_sync_job_failed(
                conn,
                job,
                error_text="gmail_rate_limited",
                result=result,
                retry_after_seconds=retry_after,
            )
            if queued.get("queue_job_status") == "failed":
                return queued
            queued.pop("error", None)
            queued.update(
                {
                    "success": True,
                    "skipped": True,
                    "reason": "gmail_rate_limited",
                    "retry_after_seconds": queued.get("queue_job_retry_after_seconds", retry_after),
                }
            )
            return queued
        _mark_sync_job_succeeded(conn, int(job["id"]), result)
        result["queue_job_status"] = "succeeded"
        return result

    error_text = (result.get("error") or "sync_failed").strip() or "sync_failed"
    return _mark_sync_job_failed(conn, job, error_text=error_text, result=result)


def _pending_sync_jobs_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """SELECT COUNT(*)
           FROM penguin_connect_jobs
           WHERE job_type = ?
             AND status IN ('queued', 'leased')""",
        (SYNC_JOB_TYPE,),
    ).fetchone()
    return int(row[0] if row else 0)


def get_sync_queue_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT status, COUNT(*) AS count
           FROM penguin_connect_jobs
           WHERE job_type = ?
           GROUP BY status""",
        (SYNC_JOB_TYPE,),
    ).fetchall()
    counts = {row["status"]: int(row["count"] or 0) for row in rows}

    next_row = conn.execute(
        """SELECT next_run_at
           FROM penguin_connect_jobs
           WHERE job_type = ?
             AND status = 'queued'
           ORDER BY next_run_at ASC, id ASC
           LIMIT 1""",
        (SYNC_JOB_TYPE,),
    ).fetchone()
    next_run_at = next_row["next_run_at"] if next_row else None
    return {
        "queued": counts.get("queued", 0),
        "leased": counts.get("leased", 0),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "next_run_at": next_run_at,
    }


def _build_gmail_service(gmail_email: str, keychain_service: Optional[str] = None):
    keychain_service = keychain_service or _keychain_service_name(gmail_email)
    raw = _read_keychain_secret(keychain_service, gmail_email)
    if not raw:
        return None, "gmail_not_connected"

    try:
        token_payload = json.loads(raw)
    except Exception:
        return None, "invalid_keychain_token_json"

    try:
        import google_auth_httplib2
        import httplib2
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials(
            token=token_payload.get("token"),
            refresh_token=token_payload.get("refresh_token"),
            token_uri=token_payload.get("token_uri") or "https://oauth2.googleapis.com/token",
            client_id=token_payload.get("client_id"),
            client_secret=token_payload.get("client_secret"),
            scopes=token_payload.get("scopes") or GMAIL_SCOPES,
        )

        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_payload["token"] = creds.token
            token_payload["expiry"] = creds.expiry.isoformat() if creds.expiry else token_payload.get("expiry")
            _write_keychain_secret(keychain_service, gmail_email, json.dumps(token_payload))

        http = httplib2.Http(timeout=_gmail_http_timeout_seconds())
        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http)
        service = build("gmail", "v1", http=authed_http, cache_discovery=False)
        return service, None
    except Exception:
        return None, "failed_to_initialize_gmail_service"


def _refresh_send_as_aliases(conn: sqlite3.Connection, gmail_service, gmail_email: str) -> tuple[list[str], str]:
    aliases: list[str] = []
    primary = gmail_email
    try:
        data = _gmail_execute(lambda: gmail_service.users().settings().sendAs().list(userId="me").execute())
        for row in data.get("sendAs", []):
            email_addr = _normalize_email(row.get("sendAsEmail"))
            if not email_addr:
                continue
            if row.get("isPrimary"):
                primary = email_addr
            if row.get("verificationStatus") in ("accepted", "verified") or row.get("isPrimary"):
                aliases.append(email_addr)
    except _GmailRetryableError:
        raise
    except Exception:
        aliases = [gmail_email]

    aliases = sorted(set(aliases + [gmail_email]))
    conn.execute(
        """UPDATE penguin_connect_accounts
           SET primary_send_as = ?, send_as_aliases = ?, updated_at = datetime('now')
           WHERE gmail_email = ?""",
        (primary, json.dumps(aliases), gmail_email),
    )
    return aliases, primary


def _sender_allowed(sender_email: str, gmail_email: str, send_as_aliases: list[str]) -> bool:
    sender = _normalize_email(sender_email)
    allowed = {_normalize_email(gmail_email)} | {_normalize_email(x) for x in (send_as_aliases or [])}
    return sender in allowed


def _resolve_display_name(conn: sqlite3.Connection, chat_name: str, participants: list[str]) -> str:
    if chat_name and not _looks_like_unresolved_handle(chat_name):
        return chat_name

    resolved: list[str] = []
    for participant in participants or []:
        p = (participant or "").strip()
        if not p:
            continue
        resolved.append(_lookup_contact_name(conn, p) or p)

    if not resolved:
        return chat_name or "iMessage Conversation"
    if len(resolved) == 1:
        return resolved[0]
    if len(resolved) <= 3:
        return ", ".join(resolved)
    return ", ".join(resolved[:3]) + f" +{len(resolved) - 3}"


def _create_alias_email(gmail_email: str, conversation_id: str, fresh: bool) -> tuple[str, str]:
    local, domain = gmail_email.split("@", 1)
    base = conversation_id[4:16]
    if fresh:
        suffix = f"{base}-{secrets.token_hex(3)}"
    else:
        suffix = base
    alias_local = f"{local}+am-{suffix}"
    return alias_local, f"{alias_local}@{domain}"


def _get_active_alias(conn: sqlite3.Connection, conversation_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM penguin_connect_aliases
           WHERE conversation_id = ? AND status = 'active'
           ORDER BY id DESC
           LIMIT 1""",
        (conversation_id,),
    ).fetchone()


def _ensure_active_alias(conn: sqlite3.Connection, gmail_email: str, conversation_id: str, fresh: bool = False) -> sqlite3.Row:
    alias_row = _get_active_alias(conn, conversation_id)
    if alias_row and not fresh:
        return alias_row

    if alias_row and fresh:
        conn.execute(
            "UPDATE penguin_connect_aliases SET status = 'disconnected', disconnected_at = datetime('now') WHERE id = ?",
            (alias_row["id"],),
        )

    alias_local, alias_email = _create_alias_email(gmail_email, conversation_id, fresh=fresh)
    conn.execute(
        """INSERT INTO penguin_connect_aliases
           (conversation_id, alias_email, alias_local_part, status, created_at)
           VALUES (?, ?, ?, 'active', datetime('now'))""",
        (conversation_id, alias_email, alias_local),
    )

    conn.execute(
        "UPDATE penguin_connect_conversations SET alias_email = ?, updated_at = datetime('now') WHERE conversation_id = ?",
        (alias_email, conversation_id),
    )
    return _get_active_alias(conn, conversation_id)


def ensure_conversations_discovered(conn: sqlite3.Connection, gmail_email: str) -> int:
    max_chats = int(os.environ.get("PENGUIN_CONNECT_CHAT_DISCOVERY_LIMIT", "500"))
    discovered = browse_imessage_chats(limit=max_chats)
    source_provider = _IMESSAGE_CHANNEL.provider
    if not discovered.get("available"):
        return 0

    count = 0
    for chat in discovered.get("chats", []):
        chat_id = chat.get("chat_id")
        if not chat_id:
            continue
        conversation_id = deterministic_conversation_id(gmail_email, chat_id, source_provider)
        legacy_conversation_id = _legacy_conversation_id(gmail_email, chat_id)
        participants = chat.get("participants") or []
        display_name = _resolve_display_name(conn, chat.get("name") or "", participants)

        existing = conn.execute(
            """SELECT conversation_id, status
               FROM penguin_connect_conversations
               WHERE conversation_id IN (?, ?)
               ORDER BY CASE WHEN conversation_id = ? THEN 0 ELSE 1 END
               LIMIT 1""",
            (conversation_id, legacy_conversation_id, conversation_id),
        ).fetchone()
        if existing and existing["conversation_id"] == legacy_conversation_id and legacy_conversation_id != conversation_id:
            _migrate_conversation_id(conn, legacy_conversation_id, conversation_id, source_provider)
            existing = conn.execute(
                "SELECT conversation_id, status FROM penguin_connect_conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        status = existing["status"] if existing else "active"

        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, display_name, chat_type,
                participants, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(conversation_id) DO UPDATE SET
                 source_provider = excluded.source_provider,
                 display_name = excluded.display_name,
                 chat_type = excluded.chat_type,
                 participants = excluded.participants,
                 updated_at = datetime('now')""",
            (
                gmail_email,
                source_provider,
                conversation_id,
                chat_id,
                display_name,
                chat.get("chat_type") or "group",
                json.dumps(participants),
                status,
            ),
        )

        if status == "active":
            alias_row = _ensure_active_alias(conn, gmail_email, conversation_id, fresh=False)
            conn.execute(
                "UPDATE penguin_connect_conversations SET alias_email = ? WHERE conversation_id = ?",
                (alias_row["alias_email"], conversation_id),
            )
        count += 1

    return count


def list_conversations(conn: sqlite3.Connection) -> dict[str, Any]:
    account = get_connected_account(conn)
    if not account:
        return {"connected": False, "conversations": []}

    # Avoid write-heavy discovery on every read request. Discover on-demand only
    # when this account has no cached conversations yet.
    try:
        existing_count = conn.execute(
            "SELECT COUNT(*) FROM penguin_connect_conversations WHERE gmail_email = ?",
            (account["gmail_email"],),
        ).fetchone()[0]
        if existing_count == 0:
            ensure_conversations_discovered(conn, account["gmail_email"])
    except sqlite3.OperationalError as exc:
        # If another writer is syncing, skip opportunistic discovery for this
        # request and return cached rows.
        msg = str(exc).lower()
        if "locked" not in msg and "busy" not in msg:
            raise

    rows = conn.execute(
        """SELECT c.conversation_id, c.source_provider, c.imessage_chat_id, c.display_name, c.chat_type,
                  c.participants, c.alias_email, c.status, c.gmail_thread_id,
                  c.last_synced_at, c.updated_at,
                  s.last_imessage_ts, s.last_gmail_ts, s.last_message_ts, s.initial_sync_completed_at
           FROM penguin_connect_conversations c
           LEFT JOIN penguin_connect_sync_state s ON s.conversation_id = c.conversation_id
           WHERE c.gmail_email = ?
           ORDER BY c.updated_at DESC""",
        (account["gmail_email"],),
    ).fetchall()

    conversations = []
    for row in rows:
        participants = []
        try:
            participants = json.loads(row["participants"] or "[]")
        except Exception:
            pass
        conversations.append(
            {
                "conversation_id": row["conversation_id"],
                "source_provider": row["source_provider"] or "imessage",
                "source_chat_id": row["imessage_chat_id"],
                "imessage_chat_id": row["imessage_chat_id"],
                "display_name": row["display_name"],
                "chat_type": row["chat_type"],
                "participants": participants,
                "alias_email": row["alias_email"],
                "status": row["status"],
                "gmail_thread_id": row["gmail_thread_id"],
                "last_imessage_ts": row["last_imessage_ts"],
                "last_gmail_ts": row["last_gmail_ts"],
                "last_message_ts": row["last_message_ts"],
                "initial_sync_completed_at": row["initial_sync_completed_at"],
                "bootstrap_complete": bool(row["initial_sync_completed_at"]),
                "last_synced_at": row["last_synced_at"],
                "updated_at": row["updated_at"],
            }
        )

    return {
        "connected": True,
        "gmail_email": account["gmail_email"],
        "conversations": conversations,
    }


def get_conversation_alias(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT c.conversation_id, c.status, c.alias_email, a.created_at
           FROM penguin_connect_conversations c
           LEFT JOIN penguin_connect_aliases a
             ON a.conversation_id = c.conversation_id AND a.status = 'active'
           WHERE c.conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    if not row:
        return {"found": False}

    return {
        "found": True,
        "conversation_id": row["conversation_id"],
        "status": row["status"],
        "alias_email": row["alias_email"],
        "created_at": row["created_at"],
    }


def get_conversation_messages(conn: sqlite3.Connection, conversation_id: str, limit: int = 200) -> dict[str, Any]:
    conv = conn.execute(
        "SELECT conversation_id, source_provider, display_name, status FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        return {"found": False, "messages": []}

    rows = conn.execute(
        """SELECT provider, provider_message_id, direction, sender_email, sender_name,
                  subject, body_text, message_timestamp, is_read, metadata,
                  gmail_message_id, gmail_thread_id
           FROM penguin_connect_messages
           WHERE conversation_id = ?
           ORDER BY message_timestamp DESC
           LIMIT ?""",
        (conversation_id, max(1, min(limit, 1000))),
    ).fetchall()

    messages = []
    for row in rows:
        metadata = {}
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except Exception:
            pass
        messages.append(
            {
                "provider": row["provider"],
                "provider_message_id": row["provider_message_id"],
                "direction": row["direction"],
                "sender_email": row["sender_email"],
                "sender_name": row["sender_name"],
                "subject": row["subject"],
                "body_text": row["body_text"],
                "message_timestamp": row["message_timestamp"],
                "is_read": bool(row["is_read"]),
                "metadata": metadata,
                "gmail_message_id": row["gmail_message_id"],
                "gmail_thread_id": row["gmail_thread_id"],
            }
        )

    return {
        "found": True,
        "conversation_id": conv["conversation_id"],
        "source_provider": conv["source_provider"] or "imessage",
        "display_name": conv["display_name"],
        "status": conv["status"],
        "messages": messages,
    }


def _gmail_header_map(payload: dict[str, Any]) -> dict[str, str]:
    headers = payload.get("headers") or []
    out = {}
    for h in headers:
        name = (h.get("name") or "").lower()
        if name:
            out[name] = h.get("value") or ""
    return out


def _decode_gmail_data_bytes(data: str) -> bytes:
    if not data:
        return b""
    if not isinstance(data, str):
        return b""
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode((data + padding).encode("utf-8"))
    except Exception:
        return b""


def _decode_gmail_data(data: str) -> str:
    return _decode_gmail_data_bytes(data).decode("utf-8", errors="replace")


def _extract_gmail_plain_text(payload: dict[str, Any]) -> str:
    mime = payload.get("mimeType") or ""
    body = payload.get("body") or {}

    if mime.startswith("text/plain"):
        return _decode_gmail_data(body.get("data") or "")

    for part in payload.get("parts") or []:
        text = _extract_gmail_plain_text(part)
        if text.strip():
            return text
    return ""


def _max_email_attachment_bytes() -> int:
    return _env_int(
        "PENGUIN_CONNECT_MAX_EMAIL_ATTACHMENT_BYTES",
        DEFAULT_MAX_EMAIL_ATTACHMENT_BYTES,
        1024,
        50 * 1024 * 1024,
    )


def _load_imessage_attachment_for_email(attachment: dict[str, Any]) -> Optional[tuple[str, str, str, bytes]]:
    file_path = (attachment.get("filename") or "").strip()
    if not file_path:
        return None
    path = Path(file_path).expanduser()
    if not path.exists() or not path.is_file():
        return None
    try:
        max_bytes = _max_email_attachment_bytes()
        if path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
    except Exception:
        return None

    transfer_name = (attachment.get("transfer_name") or "").strip()
    filename = transfer_name or path.name or "attachment"
    mime_type = (attachment.get("mime_type") or "").strip().lower()
    if not mime_type:
        guessed, _ = mimetypes.guess_type(filename)
        mime_type = (guessed or "application/octet-stream").lower()
    if "/" in mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"
    return filename, maintype, subtype, data


def _max_imessage_attachment_bytes() -> int:
    return _env_int(
        "PENGUIN_CONNECT_MAX_IMESSAGE_ATTACHMENT_BYTES",
        DEFAULT_MAX_IMESSAGE_ATTACHMENT_BYTES,
        1024,
        100 * 1024 * 1024,
    )


def _extract_gmail_attachment_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]):
        body = part.get("body") or {}
        filename = (part.get("filename") or "").strip()
        attachment_id = (body.get("attachmentId") or "").strip()
        inline_data = (body.get("data") or "").strip()
        if filename and (attachment_id or inline_data):
            size = body.get("size")
            try:
                size_int = int(size) if size is not None else 0
            except Exception:
                size_int = 0
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": (part.get("mimeType") or "").strip(),
                    "size": size_int,
                    "attachment_id": attachment_id,
                    "inline_data": inline_data,
                }
            )
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload or {})
    return attachments


def _extract_gmail_attachment_metadata(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "filename": attachment.get("filename") or "attachment",
            "mime_type": attachment.get("mime_type") or "",
            "size": int(attachment.get("size") or 0),
        }
        for attachment in _extract_gmail_attachment_parts(payload)
    ]


def _safe_attachment_filename(filename: str, fallback_index: int) -> str:
    candidate = Path((filename or "").strip()).name
    if not candidate:
        candidate = f"attachment-{fallback_index}"
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", candidate).strip(" .")
    if not safe:
        safe = f"attachment-{fallback_index}"
    return safe[:200]


def _stage_gmail_attachments_for_imessage(
    gmail_service,
    gmail_message_id: str,
    payload: dict[str, Any],
) -> tuple[list[str], dict[str, Any], Optional[Path]]:
    max_bytes = _max_imessage_attachment_bytes()
    staged_dir = Path(tempfile.mkdtemp(prefix="penguinconnect-gmail-attachments-"))
    staged_paths: list[str] = []
    forwarded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    attachments = _extract_gmail_attachment_parts(payload)
    for idx, attachment in enumerate(attachments, 1):
        filename = attachment.get("filename") or f"attachment-{idx}"
        mime_type = attachment.get("mime_type") or "application/octet-stream"
        declared_size = int(attachment.get("size") or 0)
        if declared_size > max_bytes:
            skipped.append({"filename": filename, "reason": "size_limit", "size": declared_size})
            continue

        content = _decode_gmail_data_bytes(attachment.get("inline_data") or "")
        if not content and attachment.get("attachment_id"):
            try:
                data = _gmail_execute(
                    lambda attachment_id=attachment.get("attachment_id"), gmail_message_id=gmail_message_id: gmail_service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=gmail_message_id, id=attachment_id)
                    .execute()
                )
            except Exception:
                skipped.append({"filename": filename, "reason": "download_failed", "size": declared_size})
                continue
            content = _decode_gmail_data_bytes((data or {}).get("data") or "")

        if not content:
            skipped.append({"filename": filename, "reason": "missing_data", "size": declared_size})
            continue
        if len(content) > max_bytes:
            skipped.append({"filename": filename, "reason": "size_limit", "size": len(content)})
            continue

        safe_name = _safe_attachment_filename(filename, idx)
        out_path = staged_dir / safe_name
        if out_path.exists():
            stem = out_path.stem or f"attachment-{idx}"
            suffix = out_path.suffix
            out_path = staged_dir / f"{stem}-{idx}{suffix}"
        try:
            out_path.write_bytes(content)
        except Exception:
            skipped.append({"filename": filename, "reason": "write_failed", "size": len(content)})
            continue

        staged_paths.append(str(out_path))
        forwarded.append({"filename": out_path.name, "mime_type": mime_type, "size": len(content)})

    if not staged_paths:
        shutil.rmtree(staged_dir, ignore_errors=True)
        return [], {"forwarded": [], "skipped": skipped}, None
    return staged_paths, {"forwarded": forwarded, "skipped": skipped}, staged_dir


def _provider_message_id_for_imessage(msg: dict[str, Any]) -> str:
    native_id = (msg.get("native_message_id") or "").strip()
    if native_id:
        return f"imessage:{native_id}"
    payload = f"{msg.get('timestamp')}::{msg.get('is_from_me')}::{msg.get('text') or ''}"
    return f"imessage:{hashlib.sha1(payload.encode('utf-8')).hexdigest()}"


def _build_import_email(
    conversation_id: str,
    alias_email: str,
    primary_email: str,
    display_name: str,
    msg: dict[str, Any],
    *,
    sender_name_override: Optional[str] = None,
    subject_display_name: Optional[str] = None,
    rfc_message_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[list[str]] = None,
    source_provider: str = "imessage",
) -> str:
    provider_label = _source_provider_label(source_provider)
    sender_name = sender_name_override or msg.get("push_name") or msg.get("handle") or display_name or provider_label
    subject = _provider_subject(source_provider, subject_display_name or display_name)
    body_lines = [msg.get("text") or ""]
    attachments = msg.get("attachments") or []
    if attachments:
        body_lines.append("")
        body_lines.append(f"[{len(attachments)} attachment(s) in {provider_label}]")

    email_msg = EmailMessage()
    email_msg["From"] = email.utils.formataddr((sender_name, alias_email))
    email_msg["To"] = primary_email
    email_msg["Subject"] = subject
    email_msg["Reply-To"] = alias_email
    email_msg["X-PenguinConnect-Conversation-ID"] = conversation_id
    email_msg["X-PenguinConnect-Source-Provider"] = _normalize_source_provider(source_provider)
    email_msg[PENGUINCONNECT_HEADER] = "imessage_to_email"
    normalized_msg_id = _normalize_rfc_message_id(rfc_message_id)
    if normalized_msg_id:
        email_msg["Message-ID"] = normalized_msg_id
    normalized_in_reply_to = _normalize_rfc_message_id(in_reply_to)
    if normalized_in_reply_to:
        email_msg["In-Reply-To"] = normalized_in_reply_to
    normalized_references = _normalize_rfc_message_id_list(references or [])
    if normalized_references:
        email_msg["References"] = " ".join(normalized_references)

    ts = _parse_iso(msg.get("timestamp"))
    if ts:
        email_msg["Date"] = email.utils.format_datetime(ts)

    email_msg.set_content("\n".join(body_lines).strip() or f"(empty {provider_label})")
    attached_count = 0
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        loaded = _load_imessage_attachment_for_email(attachment)
        if not loaded:
            continue
        filename, maintype, subtype, data = loaded
        email_msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        attached_count += 1
    skipped_count = max(0, len(attachments) - attached_count)
    if skipped_count:
        email_msg["X-PenguinConnect-Attachments-Skipped"] = str(skipped_count)

    raw = base64.urlsafe_b64encode(email_msg.as_bytes()).decode("utf-8")
    return raw


def _import_message_to_gmail(gmail_service, raw_message: str, gmail_thread_id: Optional[str], unread: bool):
    labels = ["INBOX"]
    if unread:
        labels.append("UNREAD")

    body: dict[str, Any] = {"raw": raw_message, "labelIds": labels}

    try:
        return _gmail_execute(
            lambda: gmail_service.users().messages().import_(
                userId="me",
                body=body,
                internalDateSource="dateHeader",
                neverMarkSpam=True,
            ).execute()
        ), None
    except _GmailRetryableError:
        raise
    except Exception as exc:
        error_text = str(exc).lower()
        if "threadid" in error_text or "thread id" in error_text:
            return None, "gmail_invalid_thread"
        return None, "gmail_import_failed"


def _get_gmail_message_raw(gmail_service, gmail_message_id: str) -> tuple[Optional[str], list[str], Optional[str]]:
    try:
        data = _gmail_execute(
            lambda gmail_message_id=gmail_message_id: gmail_service.users().messages().get(
                userId="me",
                id=gmail_message_id,
                format="raw",
            ).execute()
        )
        raw = data.get("raw")
        if not raw:
            return None, [], "gmail_message_raw_missing"
        return raw, list(data.get("labelIds") or []), None
    except _GmailRetryableError:
        raise
    except Exception:
        return None, [], "gmail_message_raw_fetch_failed"


def _filter_repair_label_ids(label_ids: list[str]) -> list[str]:
    blocked = {"TRASH", "DRAFT", "SPAM"}
    return [label for label in label_ids if (label or "").strip() and label not in blocked]


def _rewrite_raw_message_for_thread_repair(
    raw_message: str,
    new_rfc_message_id: str,
    in_reply_to: Optional[str],
    references: list[str],
    original_gmail_message_id: str,
) -> str:
    parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw_message + ("=" * (-len(raw_message) % 4))))
    if "Message-ID" in parsed:
        parsed.replace_header("Message-ID", new_rfc_message_id)
    else:
        parsed["Message-ID"] = new_rfc_message_id

    normalized_in_reply_to = _normalize_rfc_message_id(in_reply_to)
    if normalized_in_reply_to:
        if "In-Reply-To" in parsed:
            parsed.replace_header("In-Reply-To", normalized_in_reply_to)
        else:
            parsed["In-Reply-To"] = normalized_in_reply_to

    normalized_references = _normalize_rfc_message_id_list(references)
    if normalized_references:
        value = " ".join(normalized_references)
        if "References" in parsed:
            parsed.replace_header("References", value)
        else:
            parsed["References"] = value

    if PENGUINCONNECT_HEADER in parsed:
        parsed.replace_header(PENGUINCONNECT_HEADER, THREAD_REPAIR_HEADER_VALUE)
    else:
        parsed[PENGUINCONNECT_HEADER] = THREAD_REPAIR_HEADER_VALUE
    if "X-PenguinConnect-Repaired-From" in parsed:
        parsed.replace_header("X-PenguinConnect-Repaired-From", original_gmail_message_id)
    else:
        parsed["X-PenguinConnect-Repaired-From"] = original_gmail_message_id

    return base64.urlsafe_b64encode(parsed.as_bytes()).decode("utf-8")


def _insert_message_to_gmail(
    gmail_service,
    raw_message: str,
    gmail_thread_id: str,
    label_ids: list[str],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    body: dict[str, Any] = {
        "raw": raw_message,
        "labelIds": _filter_repair_label_ids(label_ids),
        "threadId": gmail_thread_id,
    }
    try:
        data = _gmail_execute(
            lambda: gmail_service.users().messages().insert(
                userId="me",
                body=body,
                internalDateSource="dateHeader",
            ).execute()
        )
        return data, None
    except _GmailRetryableError:
        raise
    except Exception:
        return None, "gmail_insert_failed"


def _trash_message_in_gmail(gmail_service, gmail_message_id: str) -> Optional[str]:
    try:
        _gmail_execute(
            lambda gmail_message_id=gmail_message_id: gmail_service.users().messages().trash(
                userId="me",
                id=gmail_message_id,
            ).execute()
        )
        return None
    except _GmailRetryableError:
        raise
    except Exception:
        return "gmail_trash_failed"


def _import_message_to_gmail_with_thread_recovery(
    gmail_service,
    raw_message: str,
    gmail_thread_id: Optional[str],
    unread: bool,
    parent_rfc_message_id: Optional[str],
) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[str]]:
    thread_id = gmail_thread_id
    if parent_rfc_message_id and not thread_id:
        thread_id = _resolve_gmail_thread_by_rfc_message_id(gmail_service, parent_rfc_message_id) or thread_id

    imported_data, import_error = _import_message_to_gmail(gmail_service, raw_message, thread_id, unread)
    if imported_data:
        return imported_data, None, thread_id or imported_data.get("threadId")

    if import_error == "gmail_invalid_thread":
        resolved = _resolve_gmail_thread_by_rfc_message_id(gmail_service, parent_rfc_message_id)
        if resolved and resolved != thread_id:
            imported_data, import_error = _import_message_to_gmail(gmail_service, raw_message, resolved, unread)
            if imported_data:
                return imported_data, None, resolved or imported_data.get("threadId")

        imported_data, import_error = _import_message_to_gmail(gmail_service, raw_message, None, unread)
        if imported_data:
            return imported_data, None, imported_data.get("threadId")

    return None, import_error or "gmail_import_failed", thread_id


def _repair_split_gmail_messages(
    conn: sqlite3.Connection,
    gmail_service,
    conversation_id: str,
    canonical_thread_id: Optional[str],
) -> int:
    canonical = (canonical_thread_id or "").strip()
    if not canonical or not _thread_is_bridge_owned(conn, conversation_id, canonical):
        return 0

    repaired = 0
    rows = conn.execute(
        """SELECT id, provider, provider_message_id, gmail_message_id, gmail_thread_id, metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND gmail_message_id IS NOT NULL
             AND gmail_thread_id IS NOT NULL
             AND gmail_thread_id != ?
           ORDER BY message_timestamp ASC, id ASC""",
        (conversation_id, canonical),
    ).fetchall()

    for row in rows:
        metadata = _load_metadata(row["metadata"])
        if metadata.get("gmail_repair_state") == "completed":
            continue

        original_gmail_message_id = (row["gmail_message_id"] or "").strip()
        if not original_gmail_message_id:
            continue

        parent_rfc_message_id, reference_chain = _load_conversation_rfc_context(
            conn,
            conversation_id,
            preferred_thread_id=canonical,
        )
        if not parent_rfc_message_id:
            continue

        raw_message, label_ids, raw_error = _get_gmail_message_raw(gmail_service, original_gmail_message_id)
        if raw_error or not raw_message:
            metadata["gmail_repair_state"] = "fetch_failed"
            metadata["gmail_repair_error"] = raw_error or "gmail_message_raw_fetch_failed"
            conn.execute("UPDATE penguin_connect_messages SET metadata = ? WHERE id = ?", (json.dumps(metadata), row["id"]))
            continue

        new_rfc_message_id = _build_repair_rfc_message_id(conversation_id, original_gmail_message_id)
        references = _append_reference_id(list(reference_chain), parent_rfc_message_id)
        repaired_raw = _rewrite_raw_message_for_thread_repair(
            raw_message,
            new_rfc_message_id,
            parent_rfc_message_id,
            references,
            original_gmail_message_id,
        )
        inserted, insert_error = _insert_message_to_gmail(gmail_service, repaired_raw, canonical, label_ids)
        if insert_error or not inserted:
            metadata["gmail_repair_state"] = "insert_failed"
            metadata["gmail_repair_error"] = insert_error or "gmail_insert_failed"
            conn.execute("UPDATE penguin_connect_messages SET metadata = ? WHERE id = ?", (json.dumps(metadata), row["id"]))
            continue

        new_gmail_message_id = (inserted.get("id") or "").strip()
        repaired_thread_id = (inserted.get("threadId") or canonical).strip() or canonical
        trash_error = _trash_message_in_gmail(gmail_service, original_gmail_message_id)
        metadata["gmail_original_message_id"] = original_gmail_message_id
        metadata["gmail_original_thread_id"] = row["gmail_thread_id"]
        metadata["repaired_from_rfc_message_id"] = metadata.get("rfc_message_id")
        metadata["rfc_message_id"] = new_rfc_message_id
        metadata["rfc_in_reply_to"] = parent_rfc_message_id
        metadata["rfc_references"] = references
        metadata["canonical_gmail_thread_id"] = repaired_thread_id
        metadata["thread_reconciled"] = False
        metadata["gmail_repaired_at"] = _now_iso()
        metadata["gmail_repair_state"] = "completed" if not trash_error else "cleanup_pending"
        metadata["gmail_repair_error"] = trash_error

        provider_message_id = row["provider_message_id"]
        if row["provider"] == "gmail" and new_gmail_message_id:
            provider_message_id = f"gmail:{new_gmail_message_id}"

        conn.execute(
            """UPDATE penguin_connect_messages
               SET provider_message_id = ?, gmail_message_id = ?, gmail_thread_id = ?, metadata = ?
               WHERE id = ?""",
            (
                provider_message_id,
                new_gmail_message_id or original_gmail_message_id,
                repaired_thread_id,
                json.dumps(metadata),
                row["id"],
            ),
        )
        repaired += 1

    return repaired


def _upsert_sync_state(
    conn: sqlite3.Connection,
    conversation_id: str,
    last_imessage_ts: Optional[str],
    last_gmail_ts: Optional[str],
    last_gmail_history_id: Optional[str],
):
    def _max_iso(existing: Optional[str], candidate: Optional[str]) -> Optional[str]:
        existing_dt = _parse_iso(existing)
        candidate_dt = _parse_iso(candidate)
        if existing_dt and candidate_dt:
            return existing if existing_dt >= candidate_dt else candidate
        if candidate_dt:
            return candidate
        if existing_dt:
            return existing
        return candidate or existing

    last_message_ts = _max_iso(last_imessage_ts, last_gmail_ts)
    existing = conn.execute(
        "SELECT last_imessage_ts, last_gmail_ts, last_message_ts, last_gmail_history_id FROM penguin_connect_sync_state WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if existing:
        last_imessage_ts = _max_iso(existing["last_imessage_ts"], last_imessage_ts)
        last_gmail_ts = _max_iso(existing["last_gmail_ts"], last_gmail_ts)
        last_message_ts = _max_iso(existing["last_message_ts"], _max_iso(last_imessage_ts, last_gmail_ts))
        if not last_gmail_history_id:
            last_gmail_history_id = existing["last_gmail_history_id"]

    conn.execute(
        """INSERT INTO penguin_connect_sync_state
           (conversation_id, last_imessage_ts, last_gmail_ts, last_message_ts, last_gmail_history_id, last_synced_at, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(conversation_id) DO UPDATE SET
             last_imessage_ts = excluded.last_imessage_ts,
             last_gmail_ts = excluded.last_gmail_ts,
             last_message_ts = excluded.last_message_ts,
             last_gmail_history_id = excluded.last_gmail_history_id,
             last_synced_at = datetime('now'),
             updated_at = datetime('now')""",
        (conversation_id, last_imessage_ts, last_gmail_ts, last_message_ts, last_gmail_history_id),
    )


def _mark_conversation_bootstrapped(conn: sqlite3.Connection, conversation_id: str):
    completed_at = _now_iso()
    conn.execute(
        """INSERT INTO penguin_connect_sync_state
           (conversation_id, initial_sync_completed_at, last_synced_at, updated_at)
           VALUES (?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(conversation_id) DO UPDATE SET
             initial_sync_completed_at = COALESCE(
               penguin_connect_sync_state.initial_sync_completed_at,
               excluded.initial_sync_completed_at
             ),
             last_synced_at = datetime('now'),
             updated_at = datetime('now')""",
        (conversation_id, completed_at),
    )


def _retry_pending_imessage_to_gmail(
    conn: sqlite3.Connection,
    gmail_service,
    conv: sqlite3.Row,
    gmail_write_pause_seconds: float = 0.0,
) -> tuple[int, Optional[str]]:
    imported = 0
    now_dt = datetime.now(timezone.utc)
    thread_id = _resolve_canonical_gmail_thread_id(conn, conv["conversation_id"], conv["gmail_thread_id"]) or conv[
        "gmail_thread_id"
    ]
    parent_rfc_message_id, reference_chain = _load_conversation_rfc_context(
        conn, conv["conversation_id"], preferred_thread_id=thread_id
    )
    processed = 0
    page_size = 1000
    last_id = 0

    while processed < 500:
        rows = conn.execute(
            """SELECT id, provider_message_id, sender_name, subject, body_text, message_timestamp,
                      is_read, metadata, gmail_thread_id
               FROM penguin_connect_messages
               WHERE conversation_id = ?
                 AND provider = 'imessage'
                 AND direction = 'imessage_to_email'
                 AND gmail_message_id IS NULL
                 AND id > ?
               ORDER BY id ASC
               LIMIT ?""",
            (conv["conversation_id"], last_id, page_size),
        ).fetchall()
        if not rows:
            break

        for row in rows:
            last_id = row["id"]
            metadata = _load_metadata(row["metadata"])
            if _delivery_status("imessage_to_email", None, metadata) in {
                "delivered",
                "failed_permanent",
                "blocked",
                "ignored",
            }:
                continue
            if not _should_attempt_delivery_retry(metadata, now_dt):
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET metadata = ?
                       WHERE conversation_id = ? AND provider_message_id = ?""",
                    (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                )
                continue

            msg = {
                "text": row["body_text"] or "",
                "timestamp": row["message_timestamp"],
                "push_name": row["sender_name"] or conv["display_name"] or "iMessage",
                "attachments": metadata.get("attachments"),
            }
            source_provider = _conversation_source_provider(conv)
            subject_name = _strip_provider_subject(row["subject"], source_provider) or conv["display_name"] or "Conversation"
            rfc_message_id = _normalize_rfc_message_id(metadata.get("rfc_message_id")) or _build_bridge_rfc_message_id(
                conv["conversation_id"], row["provider_message_id"]
            )
            in_reply_to = _normalize_rfc_message_id(metadata.get("rfc_in_reply_to")) or parent_rfc_message_id
            references = _normalize_rfc_message_id_list(metadata.get("rfc_references"))
            if not references:
                references = list(reference_chain)
            references = _append_reference_id(references, in_reply_to)
            metadata["rfc_message_id"] = rfc_message_id
            metadata["rfc_in_reply_to"] = in_reply_to
            metadata["rfc_references"] = references
            unread = not bool(row["is_read"])
            raw_email = _build_import_email(
                conv["conversation_id"],
                conv["alias_email"],
                conv["gmail_email"],
                subject_name,
                msg,
                sender_name_override=row["sender_name"] or None,
                subject_display_name=subject_name,
                rfc_message_id=rfc_message_id,
                in_reply_to=in_reply_to,
                references=references,
                source_provider=source_provider,
            )
            imported_data, import_error, recovered_thread_id = _import_message_to_gmail_with_thread_recovery(
                gmail_service,
                raw_email,
                row["gmail_thread_id"] or thread_id,
                unread,
                in_reply_to,
            )
            _sleep_after_gmail_write(gmail_write_pause_seconds)
            if import_error or not imported_data:
                metadata = _mark_delivery_failure(metadata, "import_result", import_error or "gmail_import_failed", now_dt)
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET metadata = ?
                       WHERE conversation_id = ? AND provider_message_id = ?""",
                    (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                )
            else:
                imported += 1
                gmail_msg_id = imported_data.get("id")
                thread_id = recovered_thread_id or imported_data.get("threadId") or row["gmail_thread_id"] or thread_id
                metadata = _mark_delivery_success(metadata, "import_result", "gmail_import_ok")
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET gmail_message_id = ?, gmail_thread_id = ?, metadata = ?
                       WHERE conversation_id = ? AND provider_message_id = ?""",
                    (gmail_msg_id, thread_id, json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                )
                parent_rfc_message_id = rfc_message_id
                reference_chain = _append_reference_id(references, rfc_message_id)

            processed += 1
            if processed >= 500:
                break

        if len(rows) < page_size:
            break

    return imported, thread_id


def _sync_conversation_imessage_to_gmail(
    conn: sqlite3.Connection,
    gmail_service,
    conv: sqlite3.Row,
    mode: str,
    days: int,
    hours: Optional[int] = None,
    cutoff_iso: Optional[str] = None,
    verify_all: bool = False,
) -> dict[str, Any]:
    state = conn.execute(
        "SELECT * FROM penguin_connect_sync_state WHERE conversation_id = ?",
        (conv["conversation_id"],),
    ).fetchone()

    cutoff = _parse_iso(cutoff_iso) or _sync_window_cutoff(days, hours)
    since = None
    if verify_all:
        since = FULL_IMESSAGE_SYNC_SINCE
    elif mode == "backfill":
        since = cutoff.isoformat()
    elif state and state["last_imessage_ts"]:
        since = state["last_imessage_ts"]
    else:
        since = cutoff.isoformat()

    stored = 0
    gmail_write_pause_seconds = _sync_gmail_write_pause_seconds(mode, verify_all)
    imported, thread_id = _retry_pending_imessage_to_gmail(
        conn,
        gmail_service,
        conv,
        gmail_write_pause_seconds=gmail_write_pause_seconds,
    )
    last_ts = state["last_imessage_ts"] if state else None
    thread_id = _resolve_canonical_gmail_thread_id(conn, conv["conversation_id"], thread_id or conv["gmail_thread_id"]) or thread_id
    parent_rfc_message_id, reference_chain = _load_conversation_rfc_context(
        conn, conv["conversation_id"], preferred_thread_id=thread_id
    )

    next_since = since
    batch_size = 500
    saw_messages = False
    unread_count = _get_imessage_unread_count(conv["imessage_chat_id"])

    while True:
        messages = fetch_imessage_messages(conv["imessage_chat_id"], limit=batch_size, since=next_since)
        if not messages:
            break
        saw_messages = True
        messages = sorted(messages, key=lambda m: m.get("timestamp") or "")

        for msg in messages:
            ts = msg.get("timestamp")
            text = msg.get("text") or ""
            if not ts or (not text and not msg.get("attachments")):
                continue
            sender_name, subject_name = _resolve_imessage_sender_and_subject(conn, conv, msg)

            provider_id = _provider_message_id_for_imessage(msg)
            existing = conn.execute(
                """SELECT gmail_message_id, gmail_thread_id, metadata
                   FROM penguin_connect_messages
                   WHERE conversation_id = ? AND provider_message_id = ?
                   LIMIT 1""",
                (conv["conversation_id"], provider_id),
            ).fetchone()
            if existing and existing["gmail_message_id"]:
                thread_id = existing["gmail_thread_id"] or thread_id
                last_ts = max(last_ts or ts, ts)
                continue
            if existing:
                # Existing pending messages are handled by the retry queue with backoff.
                last_ts = max(last_ts or ts, ts)
                continue

            is_from_me = 1 if msg.get("is_from_me") else 0
            source_provider = _conversation_source_provider(conv)
            unread = False
            if not is_from_me:
                if unread_count is None:
                    unread = True
                else:
                    unread = unread_count > 0

            rfc_message_id = _build_bridge_rfc_message_id(conv["conversation_id"], provider_id)
            in_reply_to = parent_rfc_message_id
            references = _append_reference_id(list(reference_chain), in_reply_to)

            metadata = {
                "imessage_chat_id": conv["imessage_chat_id"],
                "native_message_id": msg.get("native_message_id"),
                "is_from_me": bool(is_from_me),
                "attachments": msg.get("attachments"),
                "rfc_message_id": rfc_message_id,
                "rfc_in_reply_to": in_reply_to,
                "rfc_references": references,
                "delivery_status": "pending",
                "retry_count": 0,
                "max_retries": _retry_max_retries(),
            }
            cursor = conn.execute(
                """INSERT OR IGNORE INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction,
                    sender_email, sender_name, subject, body_text, message_timestamp,
                    is_read, metadata)
                   VALUES (?, 'imessage', ?, 'imessage_to_email', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conv["conversation_id"],
                    provider_id,
                    conv["alias_email"],
                    sender_name,
                    _provider_subject(source_provider, subject_name),
                    text[:20000],
                    ts,
                    0 if unread else 1,
                    json.dumps(metadata),
                ),
            )
            if cursor.rowcount <= 0:
                last_ts = max(last_ts or ts, ts)
                continue
            stored += 1

            raw_email = _build_import_email(
                conv["conversation_id"],
                conv["alias_email"],
                conv["gmail_email"],
                subject_name,
                msg,
                sender_name_override=sender_name,
                subject_display_name=subject_name,
                rfc_message_id=rfc_message_id,
                in_reply_to=in_reply_to,
                references=references,
                source_provider=source_provider,
            )
            imported_data, import_error, recovered_thread_id = _import_message_to_gmail_with_thread_recovery(
                gmail_service,
                raw_email,
                thread_id,
                unread,
                in_reply_to,
            )
            _sleep_after_gmail_write(gmail_write_pause_seconds)
            if not import_error and imported_data:
                imported += 1
                gmail_msg_id = imported_data.get("id")
                thread_id = recovered_thread_id or imported_data.get("threadId") or thread_id
                metadata = _mark_delivery_success(metadata, "import_result", "gmail_import_ok")
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET gmail_message_id = ?, gmail_thread_id = ?, metadata = ?
                       WHERE conversation_id = ? AND provider_message_id = ?""",
                    (gmail_msg_id, thread_id, json.dumps(metadata), conv["conversation_id"], provider_id),
                )
                parent_rfc_message_id = rfc_message_id
                reference_chain = _append_reference_id(references, rfc_message_id)
            else:
                metadata = _mark_delivery_failure(
                    metadata,
                    "import_result",
                    import_error or "gmail_import_failed",
                    datetime.now(timezone.utc),
                )
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET metadata = ?
                       WHERE conversation_id = ? AND provider_message_id = ?""",
                    (json.dumps(metadata), conv["conversation_id"], provider_id),
                )

            last_ts = max(last_ts or ts, ts)

        if not verify_all or len(messages) < batch_size:
            break
        batch_last_ts = messages[-1].get("timestamp")
        if not batch_last_ts or batch_last_ts == next_since:
            break
        next_since = batch_last_ts

    if not saw_messages:
        if thread_id and thread_id != conv["gmail_thread_id"]:
            conn.execute(
                "UPDATE penguin_connect_conversations SET gmail_thread_id = ?, last_synced_at = datetime('now') WHERE conversation_id = ?",
                (thread_id, conv["conversation_id"]),
            )
        _upsert_sync_state(
            conn,
            conv["conversation_id"],
            None if verify_all else since,
            None,
            None,
        )
        return {
            "imessage_imported": 0,
            "gmail_imported": imported,
            "gmail_write_pause_seconds": gmail_write_pause_seconds,
        }

    canonical_thread_id = _resolve_canonical_gmail_thread_id(conn, conv["conversation_id"], thread_id or conv["gmail_thread_id"])
    if canonical_thread_id and canonical_thread_id != conv["gmail_thread_id"]:
        conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ?, last_synced_at = datetime('now') WHERE conversation_id = ?",
            (canonical_thread_id, conv["conversation_id"]),
        )

    _upsert_sync_state(conn, conv["conversation_id"], last_ts, None, None)

    if unread_count == 0:
        _mark_conversation_gmail_read(conn, gmail_service, conv["conversation_id"])

    return {
        "imessage_imported": stored,
        "gmail_imported": imported,
        "gmail_write_pause_seconds": gmail_write_pause_seconds,
    }


def _mark_conversation_gmail_read(conn: sqlite3.Connection, gmail_service, conversation_id: str):
    rows = conn.execute(
        """SELECT gmail_message_id FROM penguin_connect_messages
           WHERE conversation_id = ? AND gmail_message_id IS NOT NULL AND is_read = 0""",
        (conversation_id,),
    ).fetchall()

    for row in rows:
        msg_id = row["gmail_message_id"]
        try:
            _gmail_execute(
                lambda msg_id=msg_id: gmail_service.users().messages().modify(
                    userId="me",
                    id=msg_id,
                    body={"removeLabelIds": ["UNREAD"]},
                ).execute()
            )
        except Exception:
            pass

    conn.execute(
        "UPDATE penguin_connect_messages SET is_read = 1 WHERE conversation_id = ? AND gmail_message_id IS NOT NULL",
        (conversation_id,),
    )


def _extract_alias_recipients(headers: dict[str, str]) -> list[str]:
    values = [
        headers.get("delivered-to") or "",
        headers.get("to") or "",
        headers.get("cc") or "",
        headers.get("x-original-to") or "",
        headers.get("x-forwarded-to") or "",
    ]
    recipients: list[str] = []
    for _name, addr in email.utils.getaddresses(values):
        normalized = _normalize_email(addr)
        if normalized:
            recipients.append(normalized)
    return recipients


def _get_gmail_mailbox_history_id(gmail_service) -> Optional[str]:
    data = _gmail_execute(lambda: gmail_service.users().getProfile(userId="me").execute())
    history_id = (data.get("historyId") or "").strip()
    return history_id or None


def _list_recent_gmail_alias_activity(
    conn: sqlite3.Connection,
    gmail_service,
    gmail_email: str,
    conversations: list[sqlite3.Row],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    alias_lookup = {
        _normalize_email(conv["alias_email"]): conv["conversation_id"]
        for conv in conversations
        if conv["alias_email"]
    }
    if not alias_lookup:
        return {}, {}

    poll_state = _get_poll_state(conn, gmail_email)
    start_history_id = (poll_state["last_gmail_history_id"] or "").strip() if poll_state else ""
    if not start_history_id:
        current_history_id = _get_gmail_mailbox_history_id(gmail_service)
        if current_history_id:
            _upsert_poll_state(conn, gmail_email, last_gmail_history_id=current_history_id)
        return {}, {"history_initialized": bool(current_history_id)}

    recent_by_conversation: dict[str, dict[str, Any]] = {}
    latest_history_id = start_history_id
    page_token = None

    while True:
        try:
            data = _gmail_execute(
                lambda page_token=page_token: gmail_service.users().history().list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    maxResults=100,
                    pageToken=page_token,
                ).execute()
            )
        except Exception as exc:
            if _extract_gmail_error_status(exc) == 404:
                current_history_id = _get_gmail_mailbox_history_id(gmail_service)
                if current_history_id:
                    _upsert_poll_state(conn, gmail_email, last_gmail_history_id=current_history_id)
                return {}, {"history_reset": True}
            raise

        latest_history_id = (data.get("historyId") or latest_history_id or "").strip() or latest_history_id
        for history_row in data.get("history") or []:
            history_id = (history_row.get("id") or "").strip()
            if history_id:
                latest_history_id = history_id
            for added in history_row.get("messagesAdded") or []:
                message = added.get("message") or {}
                message_id = message.get("id")
                if not message_id:
                    continue
                metadata = _gmail_execute(
                    lambda message_id=message_id: gmail_service.users().messages().get(
                        userId="me",
                        id=message_id,
                        format="metadata",
                        metadataHeaders=["To", "Cc", "Delivered-To", "X-Original-To", "X-Forwarded-To"],
                    ).execute()
                )
                headers = _gmail_header_map(metadata.get("payload") or {})
                conversation_id = None
                for recipient in _extract_alias_recipients(headers):
                    conversation_id = alias_lookup.get(recipient)
                    if conversation_id:
                        break
                if not conversation_id:
                    continue
                message_ts = _iso_from_gmail_internal_date(metadata.get("internalDate"))
                existing = recent_by_conversation.get(conversation_id)
                if not existing:
                    recent_by_conversation[conversation_id] = {
                        "last_message_at": message_ts,
                        "message_count": 1,
                    }
                    continue
                existing["last_message_at"] = max(existing["last_message_at"], message_ts)
                existing["message_count"] += 1

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    if latest_history_id and latest_history_id != start_history_id:
        _upsert_poll_state(conn, gmail_email, last_gmail_history_id=latest_history_id)
    return recent_by_conversation, {"last_gmail_history_id": latest_history_id}


def _list_gmail_messages_to_alias(gmail_service, alias_email: str, after_iso: str) -> list[dict[str, Any]]:
    after_dt = _parse_iso(after_iso) or (datetime.now(timezone.utc) - timedelta(days=DEFAULT_BACKFILL_DAYS))
    after_epoch = int(after_dt.timestamp())
    query = f"to:{alias_email} after:{after_epoch}"

    result: list[dict[str, Any]] = []
    page_token = None
    while True:
        params = {
            "userId": "me",
            "q": query,
            "maxResults": 100,
            "includeSpamTrash": False,
        }
        if page_token:
            params["pageToken"] = page_token

        data = _gmail_execute(lambda params=params: gmail_service.users().messages().list(**params).execute())
        result.extend(data.get("messages", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return result


def _parse_sender_email(from_header: str) -> str:
    _, addr = email.utils.parseaddr(from_header or "")
    return _normalize_email(addr)


def _retry_pending_gmail_to_imessage(
    conn: sqlite3.Connection,
    conv: sqlite3.Row,
    gmail_service=None,
) -> int:
    retried = 0
    now_dt = datetime.now(timezone.utc)
    processed = 0
    page_size = 1000
    last_id = 0

    while processed < 500:
        rows = conn.execute(
            """SELECT id, provider_message_id, body_text, metadata
               FROM penguin_connect_messages
               WHERE conversation_id = ?
                 AND provider = 'gmail'
                 AND direction = 'email_to_imessage'
                 AND id > ?
               ORDER BY id ASC
               LIMIT ?""",
            (conv["conversation_id"], last_id, page_size),
        ).fetchall()
        if not rows:
            break

        for row in rows:
            last_id = row["id"]
            metadata = _load_metadata(row["metadata"])
            status = _delivery_status("email_to_imessage", None, metadata)
            if status in {"delivered", "blocked", "ignored", "failed_permanent"}:
                continue

            if not _should_attempt_delivery_retry(metadata, now_dt):
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET metadata = ?
                       WHERE conversation_id = ? AND provider_message_id = ?""",
                    (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                )
                continue

            body_text = (row["body_text"] or "").strip()
            attachment_paths: list[str] = []
            staged_dir: Optional[Path] = None
            attachment_meta = metadata.get("attachments") if isinstance(metadata.get("attachments"), list) else []
            gmail_message_id = (
                metadata.get("gmail_message_id")
                or (row["provider_message_id"] or "").replace("gmail:", "", 1)
            )

            if gmail_service and gmail_message_id and attachment_meta:
                try:
                    full = _gmail_execute(
                        lambda gmail_message_id=gmail_message_id: gmail_service.users().messages().get(
                            userId="me",
                            id=gmail_message_id,
                            format="full",
                        ).execute()
                    )
                    attachment_paths, attachment_delivery, staged_dir = _stage_gmail_attachments_for_imessage(
                        gmail_service,
                        gmail_message_id,
                        full.get("payload") or {},
                    )
                    metadata["attachments_forwarded"] = attachment_delivery.get("forwarded", [])
                    metadata["attachments_skipped"] = attachment_delivery.get("skipped", [])
                except Exception:
                    metadata["attachments_skipped"] = [{"reason": "download_failed"}]

            if not body_text and not attachment_paths:
                if attachment_meta:
                    metadata = _mark_delivery_failure(
                        metadata,
                        "send_result",
                        "gmail_attachment_download_failed",
                        now_dt,
                    )
                    conn.execute(
                        """UPDATE penguin_connect_messages
                           SET metadata = ?
                           WHERE conversation_id = ? AND provider_message_id = ?""",
                        (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                    )
                continue

            ok, error = _send_to_source_conversation(
                conv,
                body_text,
                attachment_paths=attachment_paths,
            )
            if staged_dir:
                shutil.rmtree(staged_dir, ignore_errors=True)
            if ok:
                metadata = _mark_delivery_success(
                    metadata,
                    "send_result",
                    f"{_conversation_source_provider(conv)}_ok",
                )
            else:
                metadata = _mark_delivery_failure(
                    metadata,
                    "send_result",
                    error or f"{_conversation_source_provider(conv)}_failed",
                    now_dt,
                )
            conn.execute(
                """UPDATE penguin_connect_messages
                   SET metadata = ?
                   WHERE conversation_id = ? AND provider_message_id = ?""",
                (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
            )
            if ok:
                retried += 1

            processed += 1
            if processed >= 500:
                break

        if len(rows) < page_size:
            break

    return retried


def _sync_conversation_gmail_to_imessage(
    conn: sqlite3.Connection,
    gmail_service,
    conv: sqlite3.Row,
    gmail_email: str,
    allowed_senders: list[str],
    days: int,
    hours: Optional[int] = None,
    cutoff_iso: Optional[str] = None,
    verify_all: bool = False,
) -> dict[str, Any]:
    state = conn.execute(
        "SELECT * FROM penguin_connect_sync_state WHERE conversation_id = ?",
        (conv["conversation_id"],),
    ).fetchone()

    cutoff = _parse_iso(cutoff_iso) or _sync_window_cutoff(days, hours)
    if verify_all:
        since = FULL_GMAIL_SYNC_SINCE
    else:
        since = (state["last_gmail_ts"] if state and state["last_gmail_ts"] else cutoff.isoformat())
    converted = _retry_pending_gmail_to_imessage(conn, conv, gmail_service=gmail_service)
    blocked = 0
    last_gmail_ts = state["last_gmail_ts"] if state else since
    history_id = state["last_gmail_history_id"] if state else None
    canonical_thread_id = _resolve_canonical_gmail_thread_id(conn, conv["conversation_id"], conv["gmail_thread_id"])
    observed_thread_ids: list[str] = []

    messages = _list_gmail_messages_to_alias(gmail_service, conv["alias_email"], since)
    if not messages:
        canonical_thread_id = _resolve_canonical_gmail_thread_id(
            conn, conv["conversation_id"], canonical_thread_id or conv["gmail_thread_id"]
        )
        if canonical_thread_id:
            _apply_canonical_thread_reconciliation(conn, conv["conversation_id"], canonical_thread_id)
        _upsert_sync_state(conn, conv["conversation_id"], None, None if verify_all else since, None)
        return {"email_to_imessage": converted, "blocked_sender_count": blocked}

    message_ids = [m.get("id") for m in messages if m.get("id")]
    for message_id in message_ids:
        exists = conn.execute(
            """SELECT 1 FROM penguin_connect_messages
               WHERE conversation_id = ?
                 AND (provider_message_id = ? OR gmail_message_id = ?)
               LIMIT 1""",
            (conv["conversation_id"], f"gmail:{message_id}", message_id),
        ).fetchone()
        if exists:
            continue

        full = _gmail_execute(
            lambda message_id=message_id: gmail_service.users().messages().get(
                userId="me",
                id=message_id,
                format="full",
            ).execute()
        )
        payload = full.get("payload") or {}
        headers = _gmail_header_map(payload)
        from_header = headers.get("from") or ""
        sender = _parse_sender_email(from_header)
        message_ts = _iso_from_gmail_internal_date(full.get("internalDate"))
        label_ids = full.get("labelIds") or []
        thread_id = full.get("threadId")
        if thread_id:
            observed_thread_ids.append(thread_id)
        rfc_message_id = _normalize_rfc_message_id(headers.get("message-id"))
        rfc_in_reply_to = _normalize_rfc_message_id(headers.get("in-reply-to"))
        rfc_references = _normalize_rfc_message_id_list(headers.get("references"))
        rfc_references = _append_reference_id(rfc_references, rfc_in_reply_to)
        attachment_meta = _extract_gmail_attachment_metadata(payload)

        if not _sender_allowed(sender, gmail_email, allowed_senders):
            blocked += 1
            conn.execute(
                """INSERT OR IGNORE INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction,
                    sender_email, sender_name, subject, body_text, message_timestamp,
                    is_read, metadata, gmail_message_id, gmail_thread_id)
                   VALUES (?, 'gmail', ?, 'email_to_imessage', ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    conv["conversation_id"],
                    f"gmail:{message_id}",
                    sender,
                    from_header,
                    headers.get("subject") or "",
                    "",
                    message_ts,
                    json.dumps(
                        {
                            "blocked": True,
                            "delivery_status": "blocked",
                            "reason": "sender_not_connected_gmail",
                            "gmail_message_id": message_id,
                            "gmail_thread_id": thread_id,
                            "rfc_message_id": rfc_message_id,
                            "rfc_in_reply_to": rfc_in_reply_to,
                            "rfc_references": rfc_references,
                            "attachments": attachment_meta,
                            "labels": label_ids,
                            "retry_count": 0,
                            "max_retries": _retry_max_retries(),
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        # Ignore bridge-generated inbound imports.
        bridge_header = (headers.get(PENGUINCONNECT_HEADER.lower()) or "").strip()
        if bridge_header in {"imessage_to_email", THREAD_REPAIR_HEADER_VALUE}:
            conn.execute(
                """INSERT OR IGNORE INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction,
                    sender_email, sender_name, subject, body_text, message_timestamp,
                    is_read, metadata, gmail_message_id, gmail_thread_id)
                   VALUES (?, 'gmail', ?, 'email_to_imessage', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conv["conversation_id"],
                    f"gmail:{message_id}",
                    sender,
                    from_header,
                    headers.get("subject") or "",
                    (full.get("snippet") or "").strip()[:20000],
                    message_ts,
                    0 if "UNREAD" in label_ids else 1,
                    json.dumps(
                        {
                            "ignored": True,
                            "delivery_status": "ignored",
                            "reason": "bridge_generated_message" if bridge_header == "imessage_to_email" else "thread_repair_clone",
                            "gmail_message_id": message_id,
                            "gmail_thread_id": thread_id,
                            "rfc_message_id": rfc_message_id,
                            "rfc_in_reply_to": rfc_in_reply_to,
                            "rfc_references": rfc_references,
                            "attachments": attachment_meta,
                            "labels": label_ids,
                            "retry_count": 0,
                            "max_retries": _retry_max_retries(),
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        attachment_paths: list[str] = []
        attachment_delivery: dict[str, Any] = {"forwarded": [], "skipped": []}
        staged_dir: Optional[Path] = None
        if attachment_meta:
            attachment_paths, attachment_delivery, staged_dir = _stage_gmail_attachments_for_imessage(
                gmail_service,
                message_id,
                payload,
            )

        body_text = _extract_gmail_plain_text(payload).strip()
        if not body_text:
            body_text = (full.get("snippet") or "").strip()
        if not body_text and attachment_meta and not attachment_paths:
            preview = [a.get("filename") or a.get("mime_type") or "attachment" for a in attachment_meta[:3]]
            suffix = f" (+{len(attachment_meta) - 3} more)" if len(attachment_meta) > 3 else ""
            body_text = f"[Email attachment] {', '.join(preview)}{suffix}"
        if not body_text and not attachment_paths:
            conn.execute(
                """INSERT OR IGNORE INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction,
                    sender_email, sender_name, subject, body_text, message_timestamp,
                    is_read, metadata, gmail_message_id, gmail_thread_id)
                   VALUES (?, 'gmail', ?, 'email_to_imessage', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conv["conversation_id"],
                    f"gmail:{message_id}",
                    sender,
                    from_header,
                    headers.get("subject") or "",
                    "",
                    message_ts,
                    0 if "UNREAD" in label_ids else 1,
                    json.dumps(
                        {
                            "ignored": True,
                            "delivery_status": "ignored",
                            "reason": "empty_email_body",
                            "gmail_message_id": message_id,
                            "gmail_thread_id": thread_id,
                            "rfc_message_id": rfc_message_id,
                            "rfc_in_reply_to": rfc_in_reply_to,
                            "rfc_references": rfc_references,
                            "attachments": attachment_meta,
                            "attachments_forwarded": attachment_delivery.get("forwarded", []),
                            "attachments_skipped": attachment_delivery.get("skipped", []),
                            "labels": label_ids,
                            "retry_count": 0,
                            "max_retries": _retry_max_retries(),
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        delivery_body_text, quoted_context_count = _compose_imessage_delivery_body(
            conn,
            conv["conversation_id"],
            body_text,
            rfc_in_reply_to,
            rfc_references,
        )
        provider_message_id = f"gmail:{message_id}"
        ok, error = _send_to_source_conversation(
            conv,
            delivery_body_text,
            attachment_paths=attachment_paths,
        )
        if staged_dir:
            shutil.rmtree(staged_dir, ignore_errors=True)
        meta = {
            "gmail_message_id": message_id,
            "gmail_thread_id": thread_id,
            "rfc_message_id": rfc_message_id,
            "rfc_in_reply_to": rfc_in_reply_to,
            "rfc_references": rfc_references,
            "attachments": attachment_meta,
            "attachments_forwarded": attachment_delivery.get("forwarded", []),
            "attachments_skipped": attachment_delivery.get("skipped", []),
            "source_body_text": body_text,
            "quoted_context_count": quoted_context_count,
            "labels": label_ids,
            "retry_count": 0,
            "max_retries": _retry_max_retries(),
            "delivery_status": "pending",
        }
        source_provider = _conversation_source_provider(conv)
        if ok:
            meta = _mark_delivery_success(meta, "send_result", f"{source_provider}_ok")
            converted += 1
        else:
            meta = _mark_delivery_failure(
                meta,
                "send_result",
                error or f"{source_provider}_failed",
                datetime.now(timezone.utc),
            )

        conn.execute(
            """INSERT OR IGNORE INTO penguin_connect_messages
               (conversation_id, provider, provider_message_id, direction,
                sender_email, sender_name, subject, body_text, message_timestamp,
                is_read, metadata, gmail_message_id, gmail_thread_id)
               VALUES (?, 'gmail', ?, 'email_to_imessage', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conv["conversation_id"],
                provider_message_id,
                sender,
                from_header,
                headers.get("subject") or "",
                delivery_body_text[:20000],
                message_ts,
                0 if "UNREAD" in label_ids else 1,
                json.dumps(meta),
                message_id,
                thread_id,
            ),
        )

        last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
        history_id = full.get("historyId") or history_id

    canonical_thread_id = _resolve_canonical_gmail_thread_id(
        conn,
        conv["conversation_id"],
        canonical_thread_id or conv["gmail_thread_id"],
        observed_thread_ids,
    )
    if canonical_thread_id:
        _apply_canonical_thread_reconciliation(conn, conv["conversation_id"], canonical_thread_id)

    _upsert_sync_state(conn, conv["conversation_id"], None, last_gmail_ts, history_id)
    return {"email_to_imessage": converted, "blocked_sender_count": blocked}


def _sync_conversations_unlocked(
    conn: sqlite3.Connection,
    mode: str = "incremental",
    days: int = DEFAULT_BACKFILL_DAYS,
    hours: Optional[int] = None,
    verify_all: bool = False,
) -> dict[str, Any]:
    mode = (mode or "incremental").strip().lower()
    if mode not in SYNC_MODES:
        return {"success": False, "error": "invalid_mode", "allowed_modes": sorted(SYNC_MODES)}

    days = max(1, min(int(days or DEFAULT_BACKFILL_DAYS), 60))
    hours = _normalize_sync_hours(hours)
    account = get_connected_account(conn)
    if not account:
        return {"success": False, "error": "gmail_not_connected"}

    gmail_email = account["gmail_email"]
    ensure_conversations_discovered(conn, gmail_email)

    gmail_service, err = _build_gmail_service(gmail_email, account["keychain_service"])
    if err or not gmail_service:
        return {"success": False, "error": err or "gmail_not_connected"}

    pause = _active_gmail_rate_limit_pause(conn, gmail_email)
    if pause:
        return _gmail_rate_limit_skip_result(
            mode,
            days,
            hours,
            gmail_email,
            pause["retry_after_seconds"],
            pause["paused_until"],
        )

    try:
        send_as_aliases, primary_send_as = _refresh_send_as_aliases(conn, gmail_service, gmail_email)
        conversations, selection = _select_conversations_for_sync(
            conn,
            gmail_email,
            mode,
            days,
            hours,
            verify_all=verify_all,
            gmail_service=gmail_service,
        )
    except _GmailRetryableError as exc:
        paused_until = _set_gmail_rate_limit_pause(conn, gmail_email, exc.retry_after_seconds)
        return _gmail_rate_limit_skip_result(
            mode,
            days,
            hours,
            gmail_email,
            exc.retry_after_seconds,
            paused_until,
        )

    stats = {
        "success": True,
        "mode": mode,
        "days": days,
        "hours": hours,
        "verify_all": bool(verify_all),
        "gmail_email": gmail_email,
        "primary_send_as": primary_send_as,
        "send_as_aliases": send_as_aliases,
        "gmail_backfill_write_pause_seconds": _sync_gmail_write_pause_seconds(mode, verify_all),
        "discovered_conversations": selection["discovered_conversations"],
        "selected_conversations": selection["selected_conversations"],
        "selection_strategy": selection["selection_strategy"],
        "imessage_imported": 0,
        "gmail_imported": 0,
        "email_to_imessage": 0,
        "blocked_sender_count": 0,
        "gmail_thread_repairs": 0,
    }
    if selection.get("selection_cutoff"):
        stats["selection_cutoff"] = selection["selection_cutoff"]
    if selection.get("selection_reason"):
        stats["selection_reason"] = selection["selection_reason"]
    for key in (
        "queued_conversations",
        "selection_limit",
        "pending_bootstrap_conversations",
        "bootstrapped_conversations",
        "hot_conversations",
        "hot_imessage_conversations",
        "hot_gmail_conversations",
        "gmail_history_initialized",
        "gmail_history_reset",
    ):
        if key in selection:
            stats[key] = selection[key]
    stats["failed_conversations"] = 0
    stats["conversation_errors"] = []
    _sync_runtime_started(mode, selection)

    try:
        total = len(conversations)
        for index, conv in enumerate(conversations, start=1):
            display = conv["display_name"] or conv["conversation_id"]
            print(f"[PenguinConnect] Sync {mode} {index}/{total}: {display}")
            _sync_runtime_progress(index - 1, conv)
            try:
                imsg = _sync_conversation_imessage_to_gmail(
                    conn,
                    gmail_service,
                    conv,
                    mode,
                    days,
                    hours=hours,
                    cutoff_iso=selection.get("selection_cutoff"),
                    verify_all=verify_all,
                )
                gsync = _sync_conversation_gmail_to_imessage(
                    conn,
                    gmail_service,
                    conv,
                    gmail_email,
                    send_as_aliases,
                    days,
                    hours=hours,
                    cutoff_iso=selection.get("selection_cutoff"),
                    verify_all=verify_all,
                )
                canonical_thread_id = _resolve_canonical_gmail_thread_id(
                    conn, conv["conversation_id"], conv["gmail_thread_id"]
                )
                repaired = _repair_split_gmail_messages(conn, gmail_service, conv["conversation_id"], canonical_thread_id)

                stats["imessage_imported"] += imsg.get("imessage_imported", 0)
                stats["gmail_imported"] += imsg.get("gmail_imported", 0)
                stats["email_to_imessage"] += gsync.get("email_to_imessage", 0)
                stats["blocked_sender_count"] += gsync.get("blocked_sender_count", 0)
                stats["gmail_thread_repairs"] += repaired
                if mode in {"backfill", "startup_catchup"}:
                    _mark_conversation_bootstrapped(conn, conv["conversation_id"])

                conn.execute(
                    "UPDATE penguin_connect_conversations SET last_synced_at = datetime('now') WHERE conversation_id = ?",
                    (conv["conversation_id"],),
                )
                conn.commit()
                print(
                    "[PenguinConnect] Sync "
                    f"{mode} {index}/{total} complete: "
                    f"imessage_imported={imsg.get('imessage_imported', 0)} "
                    f"gmail_imported={imsg.get('gmail_imported', 0)} "
                    f"email_to_imessage={gsync.get('email_to_imessage', 0)} "
                    f"repairs={repaired}"
                )
            except _GmailRetryableError as exc:
                conn.rollback()
                paused_until = _set_gmail_rate_limit_pause(conn, gmail_email, exc.retry_after_seconds)
                stats.update(
                    {
                        "skipped": True,
                        "reason": "gmail_rate_limited",
                        "retry_after_seconds": exc.retry_after_seconds,
                        "rate_limited_until": paused_until,
                    }
                )
                _sync_runtime_finished(result=stats)
                return stats
            except sqlite3.OperationalError:
                conn.rollback()
                raise
            except Exception as exc:
                conn.rollback()
                error = str(exc).strip() or exc.__class__.__name__
                stats["failed_conversations"] += 1
                stats["conversation_errors"].append(
                    {
                        "conversation_id": conv["conversation_id"],
                        "display_name": conv["display_name"],
                        "error": error,
                    }
                )
                print(f"[PenguinConnect] Sync {mode} {index}/{total} failed for {display}: {error}")
            finally:
                _sync_runtime_progress(index, None)
    except Exception as exc:
        _sync_runtime_finished(error=str(exc).strip() or exc.__class__.__name__)
        raise

    _clear_gmail_rate_limit_pause(conn, gmail_email)
    _sync_runtime_finished(result=stats)
    return stats


def sync_conversations(
    conn: sqlite3.Connection,
    mode: str = "incremental",
    days: int = DEFAULT_BACKFILL_DAYS,
    hours: Optional[int] = None,
    verify_all: bool = False,
) -> dict[str, Any]:
    with _sync_lock:
        return _sync_conversations_unlocked(conn, mode=mode, days=days, hours=hours, verify_all=verify_all)


def get_sync_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    directions = {
        "imessage_to_gmail": {
            "retry_queue_count": 0,
            "failed_permanent_count": 0,
            "failed_with_error_count": 0,
            "blocked_count": 0,
            "ignored_count": 0,
            "delivered_count": 0,
            "_oldest_pending_dt": None,
        },
        "gmail_to_imessage": {
            "retry_queue_count": 0,
            "failed_permanent_count": 0,
            "failed_with_error_count": 0,
            "blocked_count": 0,
            "ignored_count": 0,
            "delivered_count": 0,
            "_oldest_pending_dt": None,
        },
    }
    map_direction = {
        "imessage_to_email": "imessage_to_gmail",
        "email_to_imessage": "gmail_to_imessage",
    }

    rows = conn.execute(
        """SELECT direction, gmail_message_id, message_timestamp, metadata
           FROM penguin_connect_messages
           WHERE direction IN ('imessage_to_email', 'email_to_imessage')"""
    ).fetchall()

    for row in rows:
        key = map_direction.get(row["direction"])
        if not key:
            continue
        bucket = directions[key]
        metadata = _load_metadata(row["metadata"])
        status = _delivery_status(row["direction"], row["gmail_message_id"], metadata)

        if status == "pending":
            bucket["retry_queue_count"] += 1
            pending_dt = (
                _parse_iso(metadata.get("first_failed_at"))
                or _parse_iso(metadata.get("next_retry_at"))
                or _parse_iso(row["message_timestamp"])
            )
            oldest = bucket["_oldest_pending_dt"]
            if pending_dt and (oldest is None or pending_dt < oldest):
                bucket["_oldest_pending_dt"] = pending_dt
        elif status == "failed_permanent":
            bucket["failed_permanent_count"] += 1
        elif status == "blocked":
            bucket["blocked_count"] += 1
        elif status == "ignored":
            bucket["ignored_count"] += 1
        elif status == "delivered":
            bucket["delivered_count"] += 1

        if metadata.get("last_error"):
            bucket["failed_with_error_count"] += 1

    for bucket in directions.values():
        oldest = bucket.pop("_oldest_pending_dt")
        if oldest:
            bucket["oldest_pending_at"] = oldest.isoformat()
            bucket["oldest_pending_age_seconds"] = max(0, int((now_dt - oldest).total_seconds()))
        else:
            bucket["oldest_pending_at"] = None
            bucket["oldest_pending_age_seconds"] = None

    return {
        "generated_at": now_dt.isoformat(),
        "retry_policy": {
            "base_seconds": _retry_base_seconds(),
            "max_backoff_seconds": _retry_max_backoff_seconds(),
            "max_retries": _retry_max_retries(),
        },
        "durable_queue": get_sync_queue_metrics(conn),
        "directions": directions,
        "totals": {
            "retry_queue_count": (
                directions["imessage_to_gmail"]["retry_queue_count"]
                + directions["gmail_to_imessage"]["retry_queue_count"]
            ),
            "failed_permanent_count": (
                directions["imessage_to_gmail"]["failed_permanent_count"]
                + directions["gmail_to_imessage"]["failed_permanent_count"]
            ),
            "failed_with_error_count": (
                directions["imessage_to_gmail"]["failed_with_error_count"]
                + directions["gmail_to_imessage"]["failed_with_error_count"]
            ),
        },
    }


def disconnect_conversation(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
    conv = conn.execute(
        "SELECT conversation_id FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        return {"success": False, "error": "conversation_not_found"}

    conn.execute(
        "UPDATE penguin_connect_aliases SET status = 'disconnected', disconnected_at = datetime('now') WHERE conversation_id = ? AND status = 'active'",
        (conversation_id,),
    )
    deleted_messages = conn.execute(
        "DELETE FROM penguin_connect_messages WHERE conversation_id = ?",
        (conversation_id,),
    ).rowcount
    conn.execute("DELETE FROM penguin_connect_sync_state WHERE conversation_id = ?", (conversation_id,))
    conn.execute(
        """UPDATE penguin_connect_conversations
           SET status = 'disconnected', alias_email = NULL, gmail_thread_id = NULL,
               last_synced_at = NULL, updated_at = datetime('now')
           WHERE conversation_id = ?""",
        (conversation_id,),
    )
    return {
        "success": True,
        "conversation_id": conversation_id,
        "messages_deleted": deleted_messages,
        "status": "disconnected",
    }


def reconnect_conversation(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
    conv = conn.execute(
        "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        return {"success": False, "error": "conversation_not_found"}

    alias_row = _ensure_active_alias(conn, conv["gmail_email"], conversation_id, fresh=True)
    # Reconnected conversations need a fresh bootstrap before incremental sync resumes.
    conn.execute("DELETE FROM penguin_connect_sync_state WHERE conversation_id = ?", (conversation_id,))
    conn.execute(
        """UPDATE penguin_connect_conversations
           SET status = 'active', alias_email = ?, gmail_thread_id = NULL,
               last_synced_at = NULL, updated_at = datetime('now')
           WHERE conversation_id = ?""",
        (alias_row["alias_email"], conversation_id),
    )

    return {
        "success": True,
        "conversation_id": conversation_id,
        "alias_email": alias_row["alias_email"],
        "status": "active",
    }


def send_manual_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    sender_email: str,
    body_text: str,
) -> dict[str, Any]:
    conv = conn.execute(
        "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        return {"success": False, "error": "conversation_not_found"}
    if conv["status"] != "active":
        return {"success": False, "error": "conversation_disconnected"}

    account = conn.execute(
        "SELECT * FROM penguin_connect_accounts WHERE gmail_email = ? LIMIT 1",
        (conv["gmail_email"],),
    ).fetchone()
    if not account:
        return {"success": False, "error": "gmail_not_connected"}

    send_as = []
    try:
        send_as = json.loads(account["send_as_aliases"] or "[]")
    except Exception:
        send_as = []

    if not _sender_allowed(sender_email, conv["gmail_email"], send_as):
        return {
            "success": False,
            "error": "sender_not_connected_gmail",
            "status_code": 403,
        }

    source_provider = _conversation_source_provider(conv)
    provider_id = f"manual:{hashlib.sha1(f'{sender_email}:{_now_iso()}:{body_text}'.encode('utf-8')).hexdigest()}"
    ok, err = _send_to_source_conversation(conv, body_text)
    if not ok:
        return {"success": False, "error": err or f"failed_to_send_{source_provider}"}

    conn.execute(
        """INSERT OR IGNORE INTO penguin_connect_messages
           (conversation_id, provider, provider_message_id, direction,
            sender_email, sender_name, subject, body_text, message_timestamp,
            is_read, metadata)
           VALUES (?, 'manual', ?, 'manual_to_imessage', ?, ?, ?, ?, ?, 1, ?)""",
        (
            conversation_id,
            provider_id,
            _normalize_email(sender_email),
            sender_email,
            _provider_subject(source_provider, conv["display_name"] or "Conversation"),
            body_text[:20000],
            _now_iso(),
            json.dumps(
                {
                    "security_gate": "passed",
                    "dispatch": source_provider,
                }
            ),
        ),
    )
    return {"success": True, "conversation_id": conversation_id}


def _map_sync_sqlite_error(exc: sqlite3.OperationalError) -> dict[str, Any]:
    msg = str(exc).lower()
    if "unable to open database file" in msg:
        return {"success": False, "error": "imessage_db_unreadable"}
    if "locked" in msg or "busy" in msg:
        return {"success": False, "error": "database_busy_retry"}
    return {"success": False, "error": "database_error"}


def _initial_sync_bootstrapped(conn: sqlite3.Connection) -> bool:
    count = conn.execute(
        "SELECT COUNT(*) FROM penguin_connect_sync_state WHERE initial_sync_completed_at IS NOT NULL"
    ).fetchone()[0]
    return count > 0


def _skip_until_initial_backfill(conn: sqlite3.Connection, mode: str) -> Optional[dict[str, Any]]:
    if _initial_sync_bootstrapped(conn):
        return None
    return {
        "success": True,
        "mode": mode,
        "skipped": True,
        "reason": "initial_backfill_required",
    }


def run_startup_catchup() -> dict[str, Any]:
    from db import get_connection

    conn = get_connection()
    try:
        try:
            enqueue_result = enqueue_sync_job(
                conn,
                mode="startup_catchup",
                days=DEFAULT_BACKFILL_DAYS,
                hours=None,
                verify_all=False,
                dedupe=True,
            )
            result = run_sync_job_worker_once(conn, owner="startup")
            if result.get("reason") == "queue_idle" and not enqueue_result.get("enqueued"):
                result = {
                    "success": True,
                    "mode": "startup_catchup",
                    "skipped": True,
                    "reason": "queue_busy",
                    "queue_job_id": enqueue_result.get("job_id"),
                }
            result.setdefault("queue_job_id", enqueue_result.get("job_id"))
            result["queue_enqueued"] = bool(enqueue_result.get("enqueued"))
            result["queue_pending_jobs"] = _pending_sync_jobs_count(conn)
        except sqlite3.OperationalError as exc:
            return _map_sync_sqlite_error(exc)
        conn.commit()
        return result
    finally:
        conn.close()


def run_incremental_sync() -> dict[str, Any]:
    from db import get_connection

    conn = get_connection()
    try:
        try:
            enqueue_result = enqueue_sync_job(
                conn,
                mode="incremental",
                days=DEFAULT_BACKFILL_DAYS,
                hours=None,
                verify_all=False,
                dedupe=True,
            )
            result = run_sync_job_worker_once(conn, owner="watcher")
            if result.get("reason") == "queue_idle" and not enqueue_result.get("enqueued"):
                result = {
                    "success": True,
                    "mode": "incremental",
                    "skipped": True,
                    "reason": "queue_busy",
                    "queue_job_id": enqueue_result.get("job_id"),
                }
            result.setdefault("queue_job_id", enqueue_result.get("job_id"))
            result["queue_enqueued"] = bool(enqueue_result.get("enqueued"))
            result["queue_pending_jobs"] = _pending_sync_jobs_count(conn)
        except sqlite3.OperationalError as exc:
            return _map_sync_sqlite_error(exc)
        conn.commit()
        return result
    finally:
        conn.close()
