"""Local macOS PenguinConnect bridge: messaging channels <-> Gmail (conversation-centric)."""

from __future__ import annotations

import base64
from email import policy
import email.utils
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
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

from action_log import log_action, message_fingerprint
from browse_sources import (
    list_apple_messages_chat_routes as _list_apple_messages_chat_routes,
    resolve_apple_messages_chat as _resolve_apple_messages_chat_route,
)
from channels import get_channel_adapter
from conversation_exclusions import (
    apply_excluded_chats_to_account,
    build_excluded_chat_entry,
    is_chat_excluded,
    load_excluded_chats,
)
from db import schedule_next_full_verify_at
from quoted_content import extract_latest_email_text

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
_incremental_sync_lock = threading.Lock()
_backfill_sync_lock = threading.Lock()
_contacts_refresh_lock = threading.Lock()
_conversation_sync_state_lock = threading.Lock()
_active_conversation_syncs: dict[str, dict[str, Any]] = {}
DEFAULT_RETRY_BASE_SECONDS = 30
DEFAULT_RETRY_MAX_BACKOFF_SECONDS = 900
DEFAULT_MAX_RETRIES = 8
DEFAULT_IMESSAGE_GLOBAL_RETRY_CONVERSATIONS_PER_RUN = 10
DEFAULT_GMAIL_TO_SOURCE_MAX_RETRIES = 3
DEFAULT_MESSAGE_ID_DOMAIN = "penguinconnect.local"
MAX_REFERENCE_CHAIN = 20
RFC_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")
DEFAULT_MAX_EMAIL_ATTACHMENT_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_IMESSAGE_ATTACHMENT_BYTES = 25 * 1024 * 1024
THREAD_REPAIR_HEADER_VALUE = "thread_repair"
DELIVERY_ERROR_HEADER_VALUE = "delivery_error_notice"
DELIVERY_REJECTION_HEADER_VALUE = "delivery_rejection_notice"
MAX_SYNC_WINDOW_HOURS = 24 * 60
DEFAULT_GMAIL_HTTP_TIMEOUT_SECONDS = 60
MIN_GMAIL_HTTP_TIMEOUT_SECONDS = 5
MAX_GMAIL_HTTP_TIMEOUT_SECONDS = 300
DEFAULT_INCREMENTAL_CONVERSATIONS_PER_RUN = 1
DEFAULT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN = 5
MAX_INCREMENTAL_CONVERSATIONS_PER_RUN = 20
DEFAULT_INCREMENTAL_ACTIVITY_WINDOW_MINUTES = 360
MAX_INCREMENTAL_ACTIVITY_WINDOW_MINUTES = 24 * 60
DEFAULT_GMAIL_ACTIVITY_BACKSTOP_HOURS = 24
DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES = 250
DEFAULT_GMAIL_API_MAX_RETRIES = 3
DEFAULT_GMAIL_API_MAX_BACKOFF_SECONDS = 30
DEFAULT_GMAIL_RATE_LIMIT_PAUSE_SECONDS = 120
DEFAULT_GMAIL_BACKFILL_WRITE_PAUSE_SECONDS = 0.15
MAX_GMAIL_BACKFILL_WRITE_PAUSE_SECONDS = 5.0
DEFAULT_CONTACT_REFRESH_MINUTES_MIN = 30
DEFAULT_CONTACT_REFRESH_MINUTES_MAX = 60
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
_USE_DEFAULT_DISCOVERY_LIMIT = object()
_IMESSAGE_CHANNEL = get_channel_adapter("imessage")
_MARKDOWN_LINK_RE = re.compile(r"(?<!\!)\[([^\]\n]+)\]\((https?://[^\s)]+)\)")


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
        "active_runs": 0,
        "active_modes": [],
        "_runs": {},
    }


_sync_runtime = _new_sync_runtime_state()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _sync_lane(mode: str) -> str:
    normalized_mode = (mode or "incremental").strip().lower()
    return "incremental" if normalized_mode == "incremental" else "backfill"


def _sync_lane_lock(mode: str) -> threading.Lock:
    return _incremental_sync_lock if _sync_lane(mode) == "incremental" else _backfill_sync_lock


def _new_sync_run_id(mode: str) -> str:
    return f"{_sync_lane(mode)}:{threading.get_ident()}:{time.monotonic_ns()}"


def _parse_participants_json(raw_value: Optional[str]) -> list[str]:
    try:
        parsed = json.loads(raw_value or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [(item or "").strip() for item in parsed if isinstance(item, str) and (item or "").strip()]


def _normalize_source_provider(value: Optional[str]) -> str:
    return (value or "imessage").strip().lower() or "imessage"


def _parse_message_metadata(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    try:
        parsed = json.loads(raw_value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_provider_label(source_provider: Optional[str]) -> str:
    normalized = _normalize_source_provider(source_provider)
    try:
        adapter = get_channel_adapter(normalized)
    except KeyError:
        adapter = None
    if adapter:
        label = getattr(adapter, "provider_label", "")
        if label and normalized == getattr(adapter, "provider", normalized):
            return label
    if normalized == "sms":
        return "SMS"
    if normalized == "rcs":
        return "RCS"
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


def _conversation_log_fields(conv: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": (conv["conversation_id"] or "").strip(),
        "display_name": (conv["display_name"] or "").strip(),
        "source_provider": _conversation_source_provider(conv),
        "source_chat_id": _conversation_source_chat_id(conv),
        "chat_type": (conv["chat_type"] or "").strip().lower() or None,
        "conversation_gmail_thread_id": (conv["gmail_thread_id"] or "").strip() or None,
    }


def _print_sync_terminal_summary(mode: str, summary_type: str, **fields: Any) -> None:
    parts = [f"{key}={value}" for key, value in fields.items() if value is not None]
    suffix = " ".join(parts) if parts else "no_details"
    print(f"[PenguinConnect] Sync {mode} {summary_type}: {suffix}")


def _provider_subject(source_provider: Optional[str], display_name: Optional[str]) -> str:
    return f"{_source_provider_label(source_provider)} · {(display_name or '').strip() or 'Conversation'}"


def _strip_provider_subject(subject: Optional[str], source_provider: Optional[str]) -> str:
    normalized_subject = (subject or "").strip()
    prefix = f"{_source_provider_label(source_provider)} · "
    if normalized_subject.startswith(prefix):
        return normalized_subject[len(prefix) :].strip()
    return normalized_subject


def _rewrite_markdown_links_for_source_message(message_text: str) -> str:
    normalized = (message_text or "").strip()
    if not normalized:
        return ""

    def _replace(match: re.Match[str]) -> str:
        label = " ".join((match.group(1) or "").split())
        url = (match.group(2) or "").strip()
        if not label or label.casefold() == url.casefold():
            return url
        return f"{label}: {url}"

    return _MARKDOWN_LINK_RE.sub(_replace, normalized)


def _send_to_source_conversation(
    conv: sqlite3.Row | dict[str, Any],
    message_text: str,
    *,
    attachment_paths: Optional[list[str]] = None,
    action_context: Optional[dict[str, Any]] = None,
) -> tuple[bool, Optional[str]]:
    message_text = _rewrite_markdown_links_for_source_message(message_text)
    source_provider = _conversation_source_provider(conv)
    source_chat_id = _conversation_source_chat_id(conv)
    base_fields = {
        **_conversation_log_fields(conv),
        **message_fingerprint(message_text),
        "attachment_count": len(attachment_paths or []),
    }
    if action_context:
        base_fields.update(action_context)
    log_action("source_send_attempt", **base_fields)
    if source_provider == "imessage":
        ok, error = send_imessage(source_chat_id, message_text, attachment_paths=attachment_paths)
    else:
        adapter = _source_adapter_for_provider(source_provider)
        ok, error = adapter.send_message(source_chat_id, message_text, attachment_paths=attachment_paths)
    log_action("source_send_result", ok=ok, error=error, **base_fields)
    return ok, error


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


def _startup_catchup_conversations_per_run() -> Optional[int]:
    raw = (os.environ.get("PENGUIN_CONNECT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN") or "").strip()
    if not raw:
        return DEFAULT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN
    try:
        value = int(raw)
    except Exception:
        return DEFAULT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN
    if value <= 0:
        return None
    return value


def _imessage_global_retry_conversations_per_run() -> int:
    return DEFAULT_IMESSAGE_GLOBAL_RETRY_CONVERSATIONS_PER_RUN


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


def _gmail_activity_backstop_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=DEFAULT_GMAIL_ACTIVITY_BACKSTOP_HOURS)


def _recent_activity_sort_value(value: Optional[str]) -> datetime:
    dt = _parse_iso(value)
    return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)


def _sync_due_sort_value(conv: sqlite3.Row) -> datetime:
    for key in ("last_synced_at", "last_message_ts", "last_gmail_ts", "last_imessage_ts", "created_at", "updated_at"):
        dt = _parse_iso(conv[key] if key in conv.keys() else None)
        if dt:
            return dt
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _full_verify_due_sort_value(conv: sqlite3.Row) -> datetime:
    dt = _parse_iso(conv["next_full_verify_at"] if "next_full_verify_at" in conv.keys() else None)
    return dt or datetime.max.replace(tzinfo=timezone.utc)


def _conversation_requires_full_verify(conv: sqlite3.Row, *, now_dt: Optional[datetime] = None) -> bool:
    if not conv["initial_sync_completed_at"]:
        return False
    due_dt = _parse_iso(conv["next_full_verify_at"])
    if not due_dt:
        return False
    return due_dt <= (now_dt or datetime.now(timezone.utc))


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
                  s.pending_gmail_activity_at,
                  s.initial_sync_completed_at,
                  s.next_full_verify_at,
                  s.full_verify_completed_at,
                  s.last_synced_at AS sync_state_last_synced_at
           FROM penguin_connect_conversations c
           LEFT JOIN penguin_connect_sync_state s ON s.conversation_id = c.conversation_id
           WHERE c.gmail_email = ?
             AND c.status = 'active'
             AND COALESCE(c.exclude_from_sync, 0) = 0
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
        now_dt = datetime.now(timezone.utc)
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
        verify_due: list[sqlite3.Row] = []
        hot_gmail_ids: set[str] = set()
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
            gmail_ts = _max_iso_value(conv["pending_gmail_activity_at"], gmail_ts)
            has_hot_gmail = bool(
                gmail_ts and _recent_activity_sort_value(gmail_ts) > _recent_activity_sort_value(conv["last_gmail_ts"])
            )

            if has_hot_imessage:
                hot_imessage += 1
            if has_hot_gmail:
                hot_gmail += 1
                hot_gmail_ids.add(conv["conversation_id"])

            if _conversation_requires_full_verify(conv, now_dt=now_dt):
                verify_due.append(conv)

            if has_hot_imessage or has_hot_gmail:
                hot.append(conv)
            elif not conv["initial_sync_completed_at"]:
                pending.append(conv)
            else:
                round_robin.append(conv)

        hot.sort(
            key=lambda conv: (
                0 if conv["conversation_id"] in hot_gmail_ids else 1,
                _sync_due_sort_value(conv),
                conv["conversation_id"],
            )
        )
        pending.sort(key=lambda conv: (_sync_due_sort_value(conv), conv["conversation_id"]))
        round_robin.sort(key=lambda conv: (_sync_due_sort_value(conv), conv["conversation_id"]))
        verify_due.sort(key=lambda conv: (_full_verify_due_sort_value(conv), _sync_due_sort_value(conv), conv["conversation_id"]))

        queued = hot + pending + round_robin
        limit = _incremental_conversations_per_run()
        selected = queued[:limit]
        verify_due_ids = {conv["conversation_id"] for conv in verify_due}
        selected_ids = {conv["conversation_id"] for conv in selected}
        selected_verify_ids = [conv["conversation_id"] for conv in selected if conv["conversation_id"] in verify_due_ids]
        if verify_due and not selected_verify_ids:
            for conv in verify_due:
                if conv["conversation_id"] in selected_ids:
                    continue
                selected.append(conv)
                selected_verify_ids = [conv["conversation_id"]]
                break
        selection["queued_conversations"] = len(queued)
        selection["selected_conversations"] = len(selected)
        selection["selection_limit"] = limit
        selection["pending_bootstrap_conversations"] = len(pending) + sum(1 for conv in hot if not conv["initial_sync_completed_at"])
        selection["bootstrapped_conversations"] = len(conversations) - selection["pending_bootstrap_conversations"]
        selection["hot_conversations"] = len(hot)
        selection["hot_imessage_conversations"] = hot_imessage
        selection["hot_gmail_conversations"] = hot_gmail
        selection["pending_full_verify_conversations"] = len(verify_due)
        selection["scheduled_full_verify_selected"] = len(selected_verify_ids)
        selection["selection_cutoff"] = hot_cutoff_iso
        if gmail_meta.get("history_initialized"):
            selection["gmail_history_initialized"] = True
        if gmail_meta.get("history_reset"):
            selection["gmail_history_reset"] = True
        if selected_verify_ids and not hot and not pending:
            selection["selection_strategy"] = "scheduled_full_verify_due"
        elif selected_verify_ids:
            selection["selection_strategy"] = "activity_prioritized_with_scheduled_verify"
        elif hot:
            selection["selection_strategy"] = "activity_prioritized_round_robin"
        elif pending:
            selection["selection_strategy"] = "pending_bootstrap_round_robin"
        else:
            selection["selection_strategy"] = "round_robin_oldest_synced"
        if selected_verify_ids:
            selection["verify_all_conversation_ids"] = selected_verify_ids
        return selected, selection

    if mode == "startup_catchup":
        now_dt = datetime.now(timezone.utc)
        selected = [conv for conv in conversations if not conv["initial_sync_completed_at"]]
        hot_cutoff_iso = _incremental_activity_cutoff().isoformat()
        verify_due = [
            conv
            for conv in conversations
            if _conversation_requires_full_verify(conv, now_dt=now_dt)
        ]
        if selected:
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
            limit = _startup_catchup_conversations_per_run()
            selected = queued if limit is None else queued[:limit]
            selection["queued_conversations"] = len(queued)
            selection["selected_conversations"] = len(selected)
            selection["selection_limit"] = len(queued) if limit is None else limit
            selection["bootstrapped_conversations"] = len(conversations) - len(queued)
            selection["pending_bootstrap_conversations"] = len(queued)
            selection["pending_full_verify_conversations"] = len(verify_due)
            selection["selection_cutoff"] = hot_cutoff_iso
            selection["selection_strategy"] = (
                "pending_bootstrap_recent_imessage_activity" if hot else "pending_bootstrap_round_robin"
            )
            return selected, selection

        verify_due.sort(key=lambda conv: (_full_verify_due_sort_value(conv), _sync_due_sort_value(conv), conv["conversation_id"]))
        limit = _startup_catchup_conversations_per_run()
        selected = verify_due if limit is None else verify_due[:limit]
        selection["queued_conversations"] = len(verify_due)
        selection["selected_conversations"] = len(selected)
        selection["selection_limit"] = len(verify_due) if limit is None else limit
        selection["bootstrapped_conversations"] = len(conversations)
        selection["pending_bootstrap_conversations"] = 0
        selection["pending_full_verify_conversations"] = len(verify_due)
        selection["selection_strategy"] = "scheduled_full_verify_due" if selected else "startup_idle"
        if selected:
            selection["verify_all_conversation_ids"] = [conv["conversation_id"] for conv in selected]
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


def _max_iso_value(existing: Optional[str], candidate: Optional[str]) -> Optional[str]:
    existing_dt = _parse_iso(existing)
    candidate_dt = _parse_iso(candidate)
    if existing_dt and candidate_dt:
        return existing if existing_dt >= candidate_dt else candidate
    if candidate_dt:
        return candidate
    if existing_dt:
        return existing
    return candidate or existing


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


def _gmail_to_source_max_retries() -> int:
    return _env_int(
        "PENGUIN_CONNECT_GMAIL_TO_SOURCE_MAX_RETRIES",
        DEFAULT_GMAIL_TO_SOURCE_MAX_RETRIES,
        1,
        10,
    )


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


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    try:
        return record[key]
    except Exception:
        return default


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


def _mark_delivery_failed_permanent(
    metadata: dict[str, Any],
    result_key: str,
    error: str,
    now_dt: datetime,
) -> dict[str, Any]:
    metadata = _apply_retry_defaults(metadata)
    metadata["delivery_status"] = "failed_permanent"
    metadata["next_retry_at"] = None
    metadata["last_retry_at"] = now_dt.isoformat()
    metadata["first_failed_at"] = metadata.get("first_failed_at") or now_dt.isoformat()
    metadata["last_error"] = (error or "delivery_failed").strip()
    metadata[result_key] = metadata["last_error"]
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


def _name_contains_unresolved_handle_component(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return False
    parts = [part.strip() for part in candidate.split(",") if part.strip()]
    if len(parts) <= 1:
        return _looks_like_unresolved_handle(candidate)
    return any(_looks_like_unresolved_handle(part) for part in parts)


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


def _apple_messages_route_rank(service_name: str) -> int:
    normalized = (service_name or "").strip().lower()
    if normalized == "imessage":
        return 0
    if normalized == "rcs":
        return 1
    if normalized == "sms":
        return 2
    return 3


def _normalize_apple_messages_thread_participant(value: str) -> str:
    participant = (value or "").strip()
    if not participant:
        return ""
    if "@" in participant and not participant.startswith("+"):
        return participant.lower()
    phone_key = _normalize_phone(participant)
    return phone_key or participant.lower()


def _apple_messages_thread_key(chat: dict[str, Any]) -> str:
    chat_type = (chat.get("chat_type") or "").strip().lower()
    if chat_type == "dm":
        participants = chat.get("participants") or []
        for participant in participants:
            normalized = _normalize_apple_messages_thread_participant(participant)
            if normalized:
                return f"dm:{normalized}"
        normalized = _normalize_apple_messages_thread_participant(chat.get("chat_identifier") or "")
        if normalized:
            return f"dm:{normalized}"
    chat_id = (chat.get("chat_id") or chat.get("chat_identifier") or "").strip()
    return f"chat:{chat_id}"


def _apple_messages_conversation_provider(chat: dict[str, Any]) -> str:
    chat_type = (chat.get("chat_type") or "").strip().lower()
    if chat_type == "dm":
        return "apple_messages"
    return _normalize_source_provider(chat.get("source_provider") or _IMESSAGE_CHANNEL.provider)


def _select_active_apple_messages_chat(chats: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        chats,
        key=lambda chat: (
            _recent_activity_sort_value(chat.get("last_message_at")),
            -_apple_messages_route_rank(chat.get("service") or chat.get("source_provider") or ""),
            chat.get("chat_id") or "",
        ),
        reverse=True,
    )[0]


def _load_conversations_by_ids(conn: sqlite3.Connection, conversation_ids: set[str]) -> list[sqlite3.Row]:
    ids = [conversation_id for conversation_id in conversation_ids if conversation_id]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(
        f"""SELECT conversation_id, status, source_provider, imessage_chat_id,
                   imessage_chat_identifier, imessage_service_name, gmail_thread_id,
                   display_name, chat_type, updated_at
              FROM penguin_connect_conversations
             WHERE conversation_id IN ({placeholders})""",
        ids,
    ).fetchall()


def _merge_sync_state_into_target(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
    source_state = conn.execute(
        """SELECT last_imessage_ts, last_gmail_ts, last_gmail_history_id,
                  pending_gmail_activity_at, initial_sync_completed_at
           FROM penguin_connect_sync_state
           WHERE conversation_id = ?""",
        (source_id,),
    ).fetchone()
    if not source_state:
        return

    _upsert_sync_state(
        conn,
        target_id,
        source_state["last_imessage_ts"],
        source_state["last_gmail_ts"],
        source_state["last_gmail_history_id"],
        pending_gmail_activity_at=source_state["pending_gmail_activity_at"],
    )
    if source_state["initial_sync_completed_at"]:
        conn.execute(
            """UPDATE penguin_connect_sync_state
               SET initial_sync_completed_at = CASE
                 WHEN initial_sync_completed_at IS NULL THEN ?
                 WHEN initial_sync_completed_at <= ? THEN initial_sync_completed_at
                 ELSE ?
               END
               WHERE conversation_id = ?""",
            (
                source_state["initial_sync_completed_at"],
                source_state["initial_sync_completed_at"],
                source_state["initial_sync_completed_at"],
                target_id,
            ),
        )


def _merge_conversation_into_target(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
    if not source_id or not target_id or source_id == target_id:
        return

    source_row = conn.execute(
        "SELECT gmail_thread_id, status FROM penguin_connect_conversations WHERE conversation_id = ? LIMIT 1",
        (source_id,),
    ).fetchone()
    if not source_row:
        return

    target_active_alias = _get_active_alias(conn, target_id)
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
    _merge_sync_state_into_target(conn, source_id, target_id)
    if source_row["gmail_thread_id"]:
        conn.execute(
            """UPDATE penguin_connect_conversations
               SET gmail_thread_id = COALESCE(gmail_thread_id, ?)
               WHERE conversation_id = ?""",
            (source_row["gmail_thread_id"], target_id),
        )
    if (source_row["status"] or "").strip().lower() == "active":
        conn.execute(
            "UPDATE penguin_connect_conversations SET status = 'active' WHERE conversation_id = ?",
            (target_id,),
        )
    conn.execute("DELETE FROM penguin_connect_conversations WHERE conversation_id = ?", (source_id,))


def _conversation_row_matches_apple_messages_chat(
    row: sqlite3.Row,
    *,
    chat_id: str,
    legacy_chat_id: str,
    service_name: str,
    source_provider: str,
    allow_dm_unified: bool,
) -> bool:
    existing_chat_id = (row["imessage_chat_id"] or "").strip()
    existing_identifier = (row["imessage_chat_identifier"] or "").strip() or existing_chat_id
    existing_service_name = (row["imessage_service_name"] or "").strip()
    existing_provider = _normalize_source_provider(row["source_provider"] or "imessage")
    existing_chat_type = (row["chat_type"] or "").strip().lower()

    if existing_chat_id == chat_id:
        return True
    if allow_dm_unified and existing_chat_type == "dm" and existing_identifier == legacy_chat_id:
        return True
    if existing_service_name and service_name and existing_service_name == service_name and existing_identifier == legacy_chat_id:
        return True
    if existing_provider in {"sms", "rcs"} and existing_provider == source_provider and existing_identifier == legacy_chat_id:
        return True
    if existing_identifier == legacy_chat_id and legacy_chat_id:
        existing_route = _resolve_apple_messages_chat_route(legacy_chat_id)
        return bool(existing_route and not existing_route.get("ambiguous") and existing_route.get("guid") == chat_id)
    return False


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
        log_action("gmail_connect_result", gmail_email=gmail_email, success=False, error="invalid_gmail_email")
        return {"success": False, "error": "invalid_gmail_email"}

    required = ["token", "refresh_token", "client_id", "client_secret"]
    missing = [k for k in required if not token_json.get(k)]
    if missing:
        log_action(
            "gmail_connect_result",
            gmail_email=gmail_email,
            success=False,
            error=f"missing_token_fields:{','.join(missing)}",
        )
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
        log_action("gmail_connect_result", gmail_email=gmail_email, success=False, error="failed_to_store_keychain_token")
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

    result = {
        "success": True,
        "gmail_email": gmail_email,
        "keychain_service": service_name,
        "primary_send_as": primary,
        "send_as_aliases": send_as,
    }
    log_action(
        "gmail_connect_result",
        gmail_email=gmail_email,
        success=True,
        primary_send_as=primary,
        send_as_alias_count=len(send_as),
    )
    return result


def get_connected_account(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM penguin_connect_accounts
           WHERE status = 'connected'
           ORDER BY updated_at DESC, id DESC
           LIMIT 1"""
    ).fetchone()


def refresh_conversation_exclusions(conn: sqlite3.Connection, gmail_email: str) -> dict[str, Any]:
    exclusions = load_excluded_chats()
    result = apply_excluded_chats_to_account(conn, gmail_email, exclusions=exclusions)
    result["configured_exclusions"] = len(exclusions)
    return result


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
        status = dict(_sync_runtime)
        status.pop("_runs", None)
        return status


def _sync_runtime_apply_summary_locked() -> None:
    runs = list(_sync_runtime.get("_runs", {}).values())
    _sync_runtime["active_runs"] = len(runs)
    _sync_runtime["active_modes"] = sorted({run["mode"] for run in runs})
    if not runs:
        _sync_runtime["running"] = False
        _sync_runtime["current_conversation_id"] = None
        _sync_runtime["current_display_name"] = None
        return

    _sync_runtime["last_error"] = None
    _sync_runtime["last_result"] = None
    if len(runs) == 1:
        run = runs[0]
        _sync_runtime.update(
            {
                "running": True,
                "mode": run["mode"],
                "started_at": run["started_at"],
                "finished_at": None,
                "selected_conversations": run["selected_conversations"],
                "processed_conversations": run["processed_conversations"],
                "current_conversation_id": run["current_conversation_id"],
                "current_display_name": run["current_display_name"],
                "selection_strategy": run["selection_strategy"],
                "selection_cutoff": run["selection_cutoff"],
            }
        )
        return

    started_ats = [run["started_at"] for run in runs if run.get("started_at")]
    _sync_runtime.update(
        {
            "running": True,
            "mode": "multiple",
            "started_at": min(started_ats) if started_ats else None,
            "finished_at": None,
            "selected_conversations": sum(int(run.get("selected_conversations") or 0) for run in runs),
            "processed_conversations": sum(int(run.get("processed_conversations") or 0) for run in runs),
            "current_conversation_id": None,
            "current_display_name": None,
            "selection_strategy": "multiple",
            "selection_cutoff": None,
        }
    )


def _sync_runtime_started(mode: str, selection: dict[str, Any]) -> str:
    run_id = _new_sync_run_id(mode)
    with _sync_runtime_lock:
        runs = _sync_runtime.setdefault("_runs", {})
        runs[run_id] = {
            "mode": mode,
            "started_at": _now_iso(),
            "selected_conversations": selection.get("selected_conversations", 0),
            "processed_conversations": 0,
            "current_conversation_id": None,
            "current_display_name": None,
            "selection_strategy": selection.get("selection_strategy"),
            "selection_cutoff": selection.get("selection_cutoff"),
        }
        _sync_runtime_apply_summary_locked()
    return run_id


def _sync_runtime_progress(run_id: str, processed_conversations: int, conv: Optional[sqlite3.Row] = None) -> None:
    with _sync_runtime_lock:
        runs = _sync_runtime.setdefault("_runs", {})
        run = runs.get(run_id)
        if not run:
            return
        run["processed_conversations"] = processed_conversations
        run["current_conversation_id"] = conv["conversation_id"] if conv else None
        run["current_display_name"] = conv["display_name"] if conv else None
        _sync_runtime_apply_summary_locked()


def _sync_runtime_finished(run_id: str, result: Optional[dict[str, Any]] = None, error: Optional[str] = None) -> None:
    finished_at = _now_iso()
    with _sync_runtime_lock:
        runs = _sync_runtime.setdefault("_runs", {})
        run = runs.pop(run_id, None)
        if result is not None:
            _sync_runtime["last_result"] = result
            if result.get("success"):
                _sync_runtime["last_completed_at"] = finished_at
        _sync_runtime["last_error"] = error
        if runs:
            _sync_runtime_apply_summary_locked()
            return
        if run:
            _sync_runtime["mode"] = run["mode"]
            _sync_runtime["started_at"] = run["started_at"]
            _sync_runtime["selected_conversations"] = run["selected_conversations"]
            _sync_runtime["processed_conversations"] = run["processed_conversations"]
            _sync_runtime["selection_strategy"] = run["selection_strategy"]
            _sync_runtime["selection_cutoff"] = run["selection_cutoff"]
        _sync_runtime["running"] = False
        _sync_runtime["finished_at"] = finished_at
        _sync_runtime["current_conversation_id"] = None
        _sync_runtime["current_display_name"] = None
        _sync_runtime["active_runs"] = 0
        _sync_runtime["active_modes"] = []


def _try_acquire_conversation_sync(conversation_id: str, *, run_id: str, mode: str) -> tuple[bool, Optional[dict[str, Any]]]:
    with _conversation_sync_state_lock:
        existing = _active_conversation_syncs.get(conversation_id)
        if existing:
            return False, dict(existing)
        _active_conversation_syncs[conversation_id] = {
            "run_id": run_id,
            "mode": mode,
            "lane": _sync_lane(mode),
            "started_at": _now_iso(),
        }
        return True, None


def _release_conversation_sync(conversation_id: str, *, run_id: str) -> None:
    with _conversation_sync_state_lock:
        existing = _active_conversation_syncs.get(conversation_id)
        if existing and existing.get("run_id") == run_id:
            _active_conversation_syncs.pop(conversation_id, None)


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


def _lease_next_sync_job(
    conn: sqlite3.Connection,
    owner: str,
    *,
    dedupe_key: Optional[str] = None,
) -> Optional[sqlite3.Row]:
    _recover_expired_sync_job_leases(conn)
    now_iso = _now_iso()
    if dedupe_key:
        row = conn.execute(
            """SELECT *
               FROM penguin_connect_jobs
               WHERE job_type = ?
                 AND dedupe_key = ?
                 AND status = 'queued'
                 AND next_run_at <= ?
               ORDER BY next_run_at ASC, id ASC
               LIMIT 1""",
            (SYNC_JOB_TYPE, dedupe_key, now_iso),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT *
               FROM penguin_connect_jobs
               WHERE job_type = ?
                 AND status = 'queued'
                 AND next_run_at <= ?
               ORDER BY CASE json_extract(payload_json, '$.mode')
                          WHEN 'incremental' THEN 0
                          WHEN 'backfill' THEN 1
                          WHEN 'startup_catchup' THEN 2
                          ELSE 3
                        END ASC,
                        next_run_at ASC,
                        id ASC
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


def _matching_sync_job_leased(conn: sqlite3.Connection, dedupe_key: str) -> bool:
    row = conn.execute(
        """SELECT 1
           FROM penguin_connect_jobs
           WHERE job_type = ?
             AND dedupe_key = ?
             AND status = 'leased'
           LIMIT 1""",
        (SYNC_JOB_TYPE, dedupe_key),
    ).fetchone()
    return bool(row)


def run_sync_job_worker_once(
    conn: sqlite3.Connection,
    owner: Optional[str] = None,
    *,
    dedupe_key: Optional[str] = None,
) -> dict[str, Any]:
    owner = (owner or "").strip() or _default_sync_job_owner()
    job = _lease_next_sync_job(conn, owner, dedupe_key=dedupe_key)
    if not job:
        if dedupe_key and _matching_sync_job_leased(conn, dedupe_key):
            return {"success": True, "skipped": True, "reason": "queue_busy"}
        return {"success": True, "skipped": True, "reason": "queue_idle"}

    payload = _load_sync_job_payload(job)
    # Persist the lease before entering sync work so other DB writers are not blocked
    # while this worker waits on the in-process sync lock.
    conn.commit()
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


def _allowed_sender_emails(gmail_email: str, send_as_aliases: list[str]) -> set[str]:
    allowed = {_normalize_email(gmail_email)}
    for alias in send_as_aliases or []:
        if not isinstance(alias, str):
            continue
        normalized = _normalize_email(alias)
        if normalized:
            allowed.add(normalized)
    return allowed


def _sender_allowed(sender_email: str, gmail_email: str, send_as_aliases: list[str]) -> bool:
    return _normalize_email(sender_email) in _allowed_sender_emails(gmail_email, send_as_aliases)


def _friendly_email_sender_name(sender_name: Optional[str], sender_email: Optional[str], *, own_sender: bool) -> str:
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


def _participant_display_name(conn: sqlite3.Connection, participants: list[str]) -> str:
    resolved: list[str] = []
    for participant in participants or []:
        p = (participant or "").strip()
        if not p:
            continue
        resolved.append(_lookup_contact_name(conn, p) or p)

    if not resolved:
        return ""
    if len(resolved) == 1:
        return resolved[0]
    if len(resolved) <= 3:
        return ", ".join(resolved)
    return ", ".join(resolved[:3]) + f" +{len(resolved) - 3}"


def _normalize_source_chat_name(chat_name: str, *, chat_identifier: str = "", chat_id: str = "") -> str:
    candidate = (chat_name or "").strip()
    if not candidate:
        return ""
    if candidate in {(chat_identifier or "").strip(), (chat_id or "").strip()}:
        return ""
    if _looks_like_unresolved_handle(candidate):
        return ""
    return candidate


def _preferred_group_display_name(source_name: str, participant_name: str) -> str:
    normalized_source = (source_name or "").strip()
    normalized_participant = (participant_name or "").strip()
    if not normalized_source:
        return normalized_participant
    if not normalized_participant or normalized_source == normalized_participant:
        return normalized_source
    if _name_contains_unresolved_handle_component(normalized_source) and not _name_contains_unresolved_handle_component(
        normalized_participant
    ):
        return normalized_participant
    return normalized_source


def _resolve_display_name(
    conn: sqlite3.Connection,
    chat_name: str,
    participants: list[str],
    *,
    chat_type: str = "group",
    existing_display_name: str = "",
    chat_identifier: str = "",
    chat_id: str = "",
) -> str:
    source_name = _normalize_source_chat_name(
        chat_name,
        chat_identifier=chat_identifier,
        chat_id=chat_id,
    )
    participant_name = _participant_display_name(conn, participants)
    if (chat_type or "").strip().lower() == "group":
        if source_name:
            preferred_source_name = _preferred_group_display_name(source_name, participant_name)
            if preferred_source_name:
                return preferred_source_name
        existing_name = _normalize_source_chat_name(existing_display_name)
        preferred_existing_name = _preferred_group_display_name(existing_name, participant_name)
        if preferred_existing_name:
            return preferred_existing_name
        if participant_name:
            return participant_name

    if source_name:
        return source_name

    if participant_name:
        return participant_name
    if existing_display_name:
        return existing_display_name
    if chat_name:
        return chat_name
    return "iMessage Conversation"


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


def _refresh_contact_display_names(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT conversation_id, display_name, chat_type, participants
           FROM penguin_connect_conversations
           WHERE status = 'active' AND COALESCE(exclude_from_sync, 0) = 0"""
    ).fetchall()
    updated = 0
    for row in rows:
        participants = _parse_participants_json(row["participants"])
        if not participants:
            continue
        refreshed_name = _resolve_display_name(
            conn,
            "",
            participants,
            chat_type=row["chat_type"] or "group",
            existing_display_name=row["display_name"] or "",
        )
        if not refreshed_name or refreshed_name == (row["display_name"] or "").strip():
            continue
        conn.execute(
            """UPDATE penguin_connect_conversations
               SET display_name = ?, updated_at = datetime('now')
               WHERE conversation_id = ?""",
            (refreshed_name, row["conversation_id"]),
        )
        updated += 1
    return updated


def refresh_contacts_and_repair_display_names() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "import_contacts.py"
    with _contacts_refresh_lock:
        with _incremental_sync_lock:
            with _backfill_sync_lock:
                completed_at = _now_iso()
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    error = (result.stderr or result.stdout or "").strip() or "contact_import_failed"
                    log_action("contacts_refresh_result", success=False, error=error)
                    return {"success": False, "error": error, "completed_at": completed_at}

                from db import get_connection

                conn = get_connection()
                try:
                    display_names_updated = _refresh_contact_display_names(conn)
                    contacts_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
                    conn.commit()
                finally:
                    conn.close()

    stdout_lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    summary = stdout_lines[-1] if stdout_lines else None
    refresh_result = {
        "success": True,
        "completed_at": completed_at,
        "contacts_count": contacts_count,
        "display_names_updated": display_names_updated,
        "summary": summary,
    }
    log_action("contacts_refresh_result", **refresh_result)
    return refresh_result


def ensure_conversations_discovered(
    conn: sqlite3.Connection,
    gmail_email: str,
    *,
    max_chats: int | None | object = _USE_DEFAULT_DISCOVERY_LIMIT,
) -> int:
    if max_chats is _USE_DEFAULT_DISCOVERY_LIMIT:
        discovery_limit: int | None = int(os.environ.get("PENGUIN_CONNECT_CHAT_DISCOVERY_LIMIT", "500"))
    else:
        discovery_limit = None if max_chats is None else int(max_chats)
    log_action(
        "conversation_discovery_started",
        gmail_email=gmail_email,
        discovery_limit=discovery_limit if discovery_limit is not None else "all",
    )
    discovered = browse_imessage_chats(limit=discovery_limit)
    if not discovered.get("available"):
        log_action(
            "conversation_discovery_result",
            gmail_email=gmail_email,
            success=False,
            discovered_count=0,
            reason=discovered.get("reason") or "not_available",
        )
        return 0

    exclusions = load_excluded_chats()
    grouped_chats: dict[str, list[dict[str, Any]]] = {}
    ordered_thread_keys: list[str] = []
    for chat in discovered.get("chats", []):
        chat_id = (chat.get("chat_id") or "").strip()
        if not chat_id:
            continue
        thread_key = _apple_messages_thread_key(chat)
        if thread_key not in grouped_chats:
            ordered_thread_keys.append(thread_key)
            grouped_chats[thread_key] = []
        grouped_chats[thread_key].append(chat)

    count = 0
    for thread_key in ordered_thread_keys:
        thread_chats = grouped_chats[thread_key]
        active_chat = _select_active_apple_messages_chat(thread_chats)
        chat_id = (active_chat.get("chat_id") or "").strip()
        if not chat_id:
            continue

        source_provider = _apple_messages_conversation_provider(active_chat)
        service_name = (active_chat.get("service") or "").strip()
        legacy_chat_id = (active_chat.get("chat_identifier") or chat_id).strip()
        participants = active_chat.get("participants") or []
        chat_type = active_chat.get("chat_type") or "group"
        excluded = is_chat_excluded(active_chat, exclusions=exclusions, gmail_email=gmail_email)
        conversation_source_key = thread_key if source_provider == "apple_messages" else chat_id
        conversation_id = deterministic_conversation_id(gmail_email, conversation_source_key, source_provider)

        candidate_ids = {conversation_id}
        for thread_chat in thread_chats:
            route_chat_id = (thread_chat.get("chat_id") or "").strip()
            if not route_chat_id:
                continue
            route_provider = _normalize_source_provider(thread_chat.get("source_provider") or _IMESSAGE_CHANNEL.provider)
            route_identifier = (thread_chat.get("chat_identifier") or route_chat_id).strip()
            candidate_ids.add(deterministic_conversation_id(gmail_email, route_chat_id, route_provider))
            candidate_ids.add(deterministic_conversation_id(gmail_email, route_identifier, "imessage"))
            candidate_ids.add(_legacy_conversation_id(gmail_email, route_identifier))

        existing_rows = _load_conversations_by_ids(conn, candidate_ids)
        existing_by_id = {row["conversation_id"]: row for row in existing_rows}
        unique_key_row = conn.execute(
            """SELECT *
               FROM penguin_connect_conversations
               WHERE gmail_email = ? AND source_provider = ? AND imessage_chat_id = ?
               LIMIT 1""",
            (gmail_email, source_provider, chat_id),
        ).fetchone()
        if unique_key_row and unique_key_row["conversation_id"] not in existing_by_id:
            existing_rows.append(unique_key_row)
            existing_by_id[unique_key_row["conversation_id"]] = unique_key_row
        active_route_conversation_id = deterministic_conversation_id(
            gmail_email,
            chat_id,
            _normalize_source_provider(active_chat.get("source_provider") or _IMESSAGE_CHANNEL.provider),
        )

        primary_row = existing_by_id.get(conversation_id) or existing_by_id.get(active_route_conversation_id)
        if primary_row is None and existing_rows:
            active_rows = [row for row in existing_rows if (row["status"] or "").strip().lower() == "active"]
            primary_row = sorted(
                active_rows or existing_rows,
                key=lambda row: (
                    row["updated_at"] or "",
                    row["conversation_id"],
                ),
                reverse=True,
            )[0]

        if primary_row and primary_row["conversation_id"] != conversation_id and conversation_id not in existing_by_id:
            if _conversation_row_matches_apple_messages_chat(
                primary_row,
                chat_id=chat_id,
                legacy_chat_id=legacy_chat_id,
                service_name=service_name,
                source_provider=source_provider,
                allow_dm_unified=source_provider == "apple_messages",
            ):
                _migrate_conversation_id(conn, primary_row["conversation_id"], conversation_id, source_provider)
                existing_rows = _load_conversations_by_ids(conn, candidate_ids | {conversation_id})
                existing_by_id = {row["conversation_id"]: row for row in existing_rows}

        statuses = [(row["status"] or "").strip().lower() for row in existing_rows]
        status = "active" if "active" in statuses or not statuses else existing_rows[0]["status"]
        if any(key in active_chat for key in ("source_display_name", "room_name", "display_name")):
            explicit_source_name = (
                active_chat.get("source_display_name")
                or active_chat.get("room_name")
                or active_chat.get("display_name")
                or ""
            )
        else:
            explicit_source_name = active_chat.get("name") or ""
        display_name = _resolve_display_name(
            conn,
            explicit_source_name,
            participants,
            chat_type=chat_type,
            existing_display_name=primary_row["display_name"] if primary_row else "",
            chat_identifier=legacy_chat_id,
            chat_id=chat_id,
        )

        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, imessage_chat_id, imessage_chat_identifier,
                imessage_service_name, display_name, chat_type, participants, status, exclude_from_sync, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(conversation_id) DO UPDATE SET
                 source_provider = excluded.source_provider,
                 imessage_chat_id = excluded.imessage_chat_id,
                 imessage_chat_identifier = excluded.imessage_chat_identifier,
                 imessage_service_name = excluded.imessage_service_name,
                 display_name = excluded.display_name,
                 chat_type = excluded.chat_type,
                 participants = excluded.participants,
                 status = excluded.status,
                 exclude_from_sync = excluded.exclude_from_sync,
                 updated_at = datetime('now')""",
            (
                gmail_email,
                source_provider,
                conversation_id,
                chat_id,
                legacy_chat_id,
                service_name,
                display_name,
                chat_type,
                json.dumps(participants),
                status,
                1 if excluded else 0,
            ),
        )

        for existing_row in existing_rows:
            if existing_row["conversation_id"] == conversation_id:
                continue
            if source_provider != "apple_messages" and not _conversation_row_matches_apple_messages_chat(
                existing_row,
                chat_id=chat_id,
                legacy_chat_id=legacy_chat_id,
                service_name=service_name,
                source_provider=source_provider,
                allow_dm_unified=False,
            ):
                continue
            _merge_conversation_into_target(conn, existing_row["conversation_id"], conversation_id)

        if status == "active" and not excluded:
            alias_row = _ensure_active_alias(conn, gmail_email, conversation_id, fresh=False)
            conn.execute(
                "UPDATE penguin_connect_conversations SET alias_email = ? WHERE conversation_id = ?",
                (alias_row["alias_email"], conversation_id),
            )
        count += 1

    log_action(
        "conversation_discovery_result",
        gmail_email=gmail_email,
        success=True,
        discovered_count=count,
        source_chat_count=len(discovered.get("chats", [])),
        grouped_thread_count=len(grouped_chats),
    )
    return count


def self_heal_conversation_cache(conn: sqlite3.Connection, gmail_email: str) -> dict[str, Any]:
    log_action("conversation_self_heal_sweep_started", gmail_email=gmail_email)
    before_count = conn.execute(
        "SELECT COUNT(*) FROM penguin_connect_conversations WHERE gmail_email = ?",
        (gmail_email,),
    ).fetchone()[0]
    discovered = ensure_conversations_discovered(conn, gmail_email, max_chats=None)
    after_count = conn.execute(
        "SELECT COUNT(*) FROM penguin_connect_conversations WHERE gmail_email = ?",
        (gmail_email,),
    ).fetchone()[0]
    unresolved_legacy_rows = conn.execute(
        """SELECT COUNT(*)
           FROM penguin_connect_conversations
           WHERE gmail_email = ?
             AND source_provider IN ('imessage', 'sms', 'rcs')
             AND (imessage_chat_id IS NULL OR instr(imessage_chat_id, ';') = 0)""",
        (gmail_email,),
    ).fetchone()[0]
    result = {
        "success": True,
        "gmail_email": gmail_email,
        "before_count": int(before_count or 0),
        "after_count": int(after_count or 0),
        "swept_conversations": int(discovered or 0),
        "legacy_non_guid_rows_remaining": int(unresolved_legacy_rows or 0),
    }
    log_action("conversation_self_heal_sweep_result", **result)
    return result


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

    refresh_conversation_exclusions(conn, account["gmail_email"])

    rows = conn.execute(
        """SELECT c.conversation_id, c.source_provider, c.imessage_chat_id, c.imessage_chat_identifier,
                  c.imessage_service_name, c.display_name, c.chat_type, c.exclude_from_sync,
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
                "imessage_chat_identifier": row["imessage_chat_identifier"],
                "imessage_service_name": row["imessage_service_name"],
                "display_name": row["display_name"],
                "chat_type": row["chat_type"],
                "participants": participants,
                "alias_email": row["alias_email"],
                "status": row["status"],
                "excluded": bool(row["exclude_from_sync"]),
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
        """SELECT c.conversation_id, c.status, c.exclude_from_sync, c.alias_email, a.created_at
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
        "excluded": bool(row["exclude_from_sync"]),
        "alias_email": row["alias_email"],
        "created_at": row["created_at"],
    }


def get_conversation_messages(conn: sqlite3.Connection, conversation_id: str, limit: int = 200) -> dict[str, Any]:
    conv = conn.execute(
        """SELECT conversation_id, gmail_email, source_provider, display_name, status, exclude_from_sync
           FROM penguin_connect_conversations
           WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    if not conv:
        return {"found": False, "messages": []}

    account = conn.execute(
        "SELECT send_as_aliases FROM penguin_connect_accounts WHERE gmail_email = ? LIMIT 1",
        (conv["gmail_email"],),
    ).fetchone()
    own_sender_emails = _allowed_sender_emails(conv["gmail_email"], [])
    if account:
        try:
            aliases = json.loads(account["send_as_aliases"] or "[]")
        except Exception:
            aliases = []
        own_sender_emails = _allowed_sender_emails(conv["gmail_email"], aliases)

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
        is_own_imessage_message = bool(metadata.get("is_from_me"))
        is_own_gmail_message = (
            row["direction"] in {"email_to_imessage", "manual_to_imessage"}
            and _normalize_email(row["sender_email"]) in own_sender_emails
        )
        sender_name = row["sender_name"]
        if is_own_imessage_message:
            sender_name = "Me"
        elif is_own_gmail_message:
            sender_name = _friendly_email_sender_name(sender_name, row["sender_email"], own_sender=True)
        messages.append(
            {
                "provider": row["provider"],
                "provider_message_id": row["provider_message_id"],
                "direction": row["direction"],
                "sender_email": row["sender_email"],
                "sender_name": sender_name,
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
        "excluded": bool(conv["exclude_from_sync"]),
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


def _extract_gmail_body_variants(payload: dict[str, Any]) -> tuple[str, str]:
    plain_text = ""
    html_text = ""

    def visit(part: dict[str, Any]) -> None:
        nonlocal plain_text, html_text

        mime = part.get("mimeType") or ""
        body = part.get("body") or {}
        data = body.get("data") or ""
        if data:
            if mime.startswith("text/plain") and not plain_text:
                plain_text = _decode_gmail_data(data)
            elif mime.startswith("text/html") and not html_text:
                html_text = _decode_gmail_data(data)

        for child in part.get("parts") or []:
            if plain_text and html_text:
                break
            visit(child)

    visit(payload or {})
    return plain_text, html_text


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


def _render_message_text_html(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return "<p>(empty message)</p>"
    paragraphs = [segment.strip("\n") for segment in normalized.split("\n\n")]
    html_parts: list[str] = []
    for paragraph in paragraphs:
        lines = paragraph.split("\n")
        escaped_lines = [html.escape(line) for line in lines]
        html_parts.append(f"<p>{'<br>'.join(escaped_lines)}</p>")
    return "".join(html_parts)


def _format_reply_header(timestamp: Optional[str], sender_name: Optional[str]) -> str:
    sender = (sender_name or "").strip() or "Someone"
    parsed = _parse_iso(timestamp)
    if not parsed:
        return f"On a previous message, {sender} wrote:"
    local_ts = parsed.astimezone()
    return f"On {local_ts.strftime('%b %-d, %Y at %-I:%M %p')}, {sender} wrote:"


def _normalize_rendered_message_body(metadata: Optional[dict[str, Any]], body_text: Optional[str]) -> tuple[str, str]:
    metadata = metadata if isinstance(metadata, dict) else {}
    html_body = (metadata.get("email_body_html") or "").strip()
    plain_body = (metadata.get("email_body_plain") or "").strip()
    normalized_body_text = (body_text or "").strip()
    if not plain_body:
        plain_body = normalized_body_text
    if not html_body:
        html_body = _render_message_text_html(plain_body)
    return plain_body, html_body


def _build_nested_reply_bodies(
    *,
    body_text: str,
    attachment_note: Optional[str],
    quoted_plain: Optional[str],
    quoted_html: Optional[str],
    quoted_header: Optional[str],
) -> tuple[str, str]:
    base_plain_parts = [body_text.strip()] if body_text.strip() else []
    if attachment_note:
        if base_plain_parts:
            base_plain_parts.append("")
        base_plain_parts.append(attachment_note)
    plain_body = "\n".join(base_plain_parts).strip() or "(empty message)"

    html_parts = [_render_message_text_html(body_text)]
    if attachment_note:
        html_parts.append(f"<p>{html.escape(attachment_note)}</p>")
    html_body = "".join(html_parts).strip() or "<p>(empty message)</p>"

    normalized_quoted_plain = (quoted_plain or "").strip()
    normalized_quoted_html = (quoted_html or "").strip()
    normalized_header = (quoted_header or "").strip()
    if normalized_quoted_plain and normalized_quoted_html and normalized_header:
        plain_body = f"{plain_body}\n\n{normalized_header}\n{normalized_quoted_plain}".strip()
        html_body = (
            f"{html_body}<br><div class=\"gmail_quote\">"
            f"<div class=\"gmail_attr\" style=\"font-size: 13px; color: #5f6368; margin-bottom: 8px;\">"
            f"{html.escape(normalized_header)}</div>"
            "<blockquote class=\"gmail_quote\" "
            "style=\"margin: 0 0 0 0.8ex; border-left: 1px solid #ccc; padding-left: 1ex;\">"
            f"{normalized_quoted_html}</blockquote></div>"
        )
    return plain_body, html_body


def _find_prior_imessage_email_row(
    conn: sqlite3.Connection,
    conversation_id: str,
    message_timestamp: Optional[str],
    provider_message_id: str,
) -> Optional[sqlite3.Row]:
    if not message_timestamp:
        return None
    return conn.execute(
        """SELECT provider_message_id, sender_name, body_text, message_timestamp, metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND direction = 'imessage_to_email'
             AND provider = 'imessage'
             AND (
                 message_timestamp < ?
                 OR (message_timestamp = ? AND provider_message_id < ?)
             )
           ORDER BY message_timestamp DESC, provider_message_id DESC
           LIMIT 1""",
        (conversation_id, message_timestamp, message_timestamp, provider_message_id),
    ).fetchone()


def _quoted_reply_context_for_imessage_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    message_timestamp: Optional[str],
    provider_message_id: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    prior_row = _find_prior_imessage_email_row(conn, conversation_id, message_timestamp, provider_message_id)
    if not prior_row:
        return None, None, None
    prior_metadata = _parse_message_metadata(prior_row["metadata"])
    quoted_plain, quoted_html = _normalize_rendered_message_body(prior_metadata, prior_row["body_text"])
    quoted_header = _format_reply_header(prior_row["message_timestamp"], prior_row["sender_name"])
    return quoted_plain, quoted_html, quoted_header


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
    quoted_plain: Optional[str] = None,
    quoted_html: Optional[str] = None,
    quoted_header: Optional[str] = None,
) -> str:
    provider_label = _source_provider_label(source_provider)
    sender_name = sender_name_override or msg.get("push_name") or msg.get("handle") or display_name or provider_label
    subject = _provider_subject(source_provider, subject_display_name or display_name)
    body_text = (msg.get("text") or "").strip()
    attachments = msg.get("attachments") or []
    attachment_note = f"[{len(attachments)} attachment(s) in {provider_label}]" if attachments else None
    plain_body, html_body = _build_nested_reply_bodies(
        body_text=body_text,
        attachment_note=attachment_note,
        quoted_plain=quoted_plain,
        quoted_html=quoted_html,
        quoted_header=quoted_header,
    )

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

    email_msg.set_content(plain_body or f"(empty {provider_label})")
    email_msg.add_alternative(html_body, subtype="html")
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


def _build_gmail_delivery_error_email(
    conv: sqlite3.Row | dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[str, str]:
    failed_body = (row["body_text"] or "").strip()
    attachments = metadata.get("attachments") if isinstance(metadata.get("attachments"), list) else []
    body_lines = [
        "Following message ran into errors sending:",
        "",
        failed_body or "(empty message)",
    ]
    if attachments:
        body_lines.extend(["", f"[{len(attachments)} attachment(s) may not have been delivered]"])

    notice_rfc_message_id = _build_bridge_rfc_message_id(
        conv["conversation_id"],
        f"{row['provider_message_id']}:delivery-error",
    )
    in_reply_to = _normalize_rfc_message_id(metadata.get("rfc_message_id"))
    references = _normalize_rfc_message_id_list(metadata.get("rfc_references") or [])
    if in_reply_to:
        references = _append_reference_id(references, in_reply_to)

    email_msg = EmailMessage()
    email_msg["From"] = email.utils.formataddr(("PENGUIN_CONNECT", conv["alias_email"]))
    email_msg["To"] = conv["gmail_email"]
    email_msg["Subject"] = (row["subject"] or _provider_subject(_conversation_source_provider(conv), conv["display_name"])).strip()
    email_msg["Reply-To"] = conv["alias_email"]
    email_msg["X-PenguinConnect-Conversation-ID"] = conv["conversation_id"]
    email_msg["X-PenguinConnect-Source-Provider"] = _normalize_source_provider(_conversation_source_provider(conv))
    email_msg[PENGUINCONNECT_HEADER] = DELIVERY_ERROR_HEADER_VALUE
    email_msg["Message-ID"] = notice_rfc_message_id
    if in_reply_to:
        email_msg["In-Reply-To"] = in_reply_to
    if references:
        email_msg["References"] = " ".join(references)
    email_msg.set_content("\n".join(body_lines))
    raw = base64.urlsafe_b64encode(email_msg.as_bytes()).decode("utf-8")
    return raw, notice_rfc_message_id


def _build_gmail_delivery_rejection_email(
    conv: sqlite3.Row | dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[str, str]:
    rejected_body = (row["body_text"] or "").strip()
    attachments = metadata.get("attachments") if isinstance(metadata.get("attachments"), list) else []
    reason = (metadata.get("reason") or "").strip()
    body_lines = [
        "PenguinConnect rejected this email reply and did not send it to the source chat.",
        "",
    ]
    if reason == "ambiguous_email_body":
        body_lines.extend(
            [
                "Reason: the reply body could not be confirmed as net-new text only.",
                "Please simplify the reply and resend if you still want it delivered.",
                "",
            ]
        )
    else:
        body_lines.extend(["Reason: the reply could not be delivered safely.", ""])
    body_lines.append(rejected_body or "(empty message)")
    if attachments:
        body_lines.extend(["", f"[{len(attachments)} attachment(s) were not delivered]"])

    notice_rfc_message_id = _build_bridge_rfc_message_id(
        conv["conversation_id"],
        f"{row['provider_message_id']}:delivery-rejection",
    )
    in_reply_to = _normalize_rfc_message_id(metadata.get("rfc_message_id"))
    references = _normalize_rfc_message_id_list(metadata.get("rfc_references") or [])
    if in_reply_to:
        references = _append_reference_id(references, in_reply_to)

    email_msg = EmailMessage()
    email_msg["From"] = email.utils.formataddr(("PENGUIN_CONNECT", conv["alias_email"]))
    email_msg["To"] = conv["gmail_email"]
    email_msg["Subject"] = (row["subject"] or _provider_subject(_conversation_source_provider(conv), conv["display_name"])).strip()
    email_msg["Reply-To"] = conv["alias_email"]
    email_msg["X-PenguinConnect-Conversation-ID"] = conv["conversation_id"]
    email_msg["X-PenguinConnect-Source-Provider"] = _normalize_source_provider(_conversation_source_provider(conv))
    email_msg[PENGUINCONNECT_HEADER] = DELIVERY_REJECTION_HEADER_VALUE
    email_msg["Message-ID"] = notice_rfc_message_id
    if in_reply_to:
        email_msg["In-Reply-To"] = in_reply_to
    if references:
        email_msg["References"] = " ".join(references)
    email_msg.set_content("\n".join(body_lines))
    raw = base64.urlsafe_b64encode(email_msg.as_bytes()).decode("utf-8")
    return raw, notice_rfc_message_id


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
    pending_gmail_activity_at: Optional[str] = None,
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
        """SELECT last_imessage_ts, last_gmail_ts, last_message_ts,
                  last_gmail_history_id, pending_gmail_activity_at
           FROM penguin_connect_sync_state
           WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    if existing:
        last_imessage_ts = _max_iso(existing["last_imessage_ts"], last_imessage_ts)
        last_gmail_ts = _max_iso(existing["last_gmail_ts"], last_gmail_ts)
        last_message_ts = _max_iso(existing["last_message_ts"], _max_iso(last_imessage_ts, last_gmail_ts))
        if not last_gmail_history_id:
            last_gmail_history_id = existing["last_gmail_history_id"]
        pending_gmail_activity_at = _max_iso(existing["pending_gmail_activity_at"], pending_gmail_activity_at)

    conn.execute(
        """INSERT INTO penguin_connect_sync_state
           (conversation_id, last_imessage_ts, last_gmail_ts, last_message_ts,
            last_gmail_history_id, pending_gmail_activity_at, last_synced_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(conversation_id) DO UPDATE SET
             last_imessage_ts = excluded.last_imessage_ts,
             last_gmail_ts = excluded.last_gmail_ts,
             last_message_ts = excluded.last_message_ts,
             last_gmail_history_id = excluded.last_gmail_history_id,
             pending_gmail_activity_at = excluded.pending_gmail_activity_at,
             last_synced_at = datetime('now'),
             updated_at = datetime('now')""",
        (
            conversation_id,
            last_imessage_ts,
            last_gmail_ts,
            last_message_ts,
            last_gmail_history_id,
            pending_gmail_activity_at,
        ),
    )


def _conversation_has_materialized_imessage_history(conn: sqlite3.Connection, conversation_id: str) -> bool:
    row = conn.execute(
        """SELECT 1
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND direction = 'imessage_to_email'
             AND gmail_message_id IS NOT NULL
           LIMIT 1""",
        (conversation_id,),
    ).fetchone()
    return bool(row)


def _mark_conversation_bootstrapped(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    empty_verified: bool = False,
):
    completed_at = _now_iso()
    existing = conn.execute(
        """SELECT initial_sync_completed_at, initial_sync_empty_verified_at, next_full_verify_at, full_verify_completed_at
           FROM penguin_connect_sync_state
           WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    next_full_verify_at = None
    if not existing or not existing["initial_sync_completed_at"]:
        next_full_verify_at = (
            existing["next_full_verify_at"]
            if existing and existing["next_full_verify_at"]
            else schedule_next_full_verify_at(conversation_id, base_iso=completed_at)
        )

    conn.execute(
        """INSERT INTO penguin_connect_sync_state
           (conversation_id, initial_sync_completed_at, initial_sync_empty_verified_at, next_full_verify_at, last_synced_at, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(conversation_id) DO UPDATE SET
             initial_sync_completed_at = COALESCE(
               penguin_connect_sync_state.initial_sync_completed_at,
               excluded.initial_sync_completed_at
             ),
             initial_sync_empty_verified_at = COALESCE(
               penguin_connect_sync_state.initial_sync_empty_verified_at,
               excluded.initial_sync_empty_verified_at
             ),
             next_full_verify_at = COALESCE(
               penguin_connect_sync_state.next_full_verify_at,
               excluded.next_full_verify_at
             ),
             last_synced_at = datetime('now'),
             updated_at = datetime('now')""",
        (conversation_id, completed_at, completed_at if empty_verified else None, next_full_verify_at),
    )


def _ensure_full_verify_schedule(conn: sqlite3.Connection, gmail_email: str) -> int:
    rows = conn.execute(
        """SELECT s.conversation_id, s.initial_sync_completed_at, s.full_verify_completed_at
           FROM penguin_connect_sync_state s
           JOIN penguin_connect_conversations c ON c.conversation_id = s.conversation_id
           WHERE c.gmail_email = ?
             AND c.status = 'active'
             AND COALESCE(c.exclude_from_sync, 0) = 0
             AND s.initial_sync_completed_at IS NOT NULL
             AND s.next_full_verify_at IS NULL""",
        (gmail_email,),
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    for row in rows:
        base_iso = row["full_verify_completed_at"] or row["initial_sync_completed_at"] or _now_iso()
        next_full_verify_at = schedule_next_full_verify_at(row["conversation_id"], base_iso=base_iso)
        conn.execute(
            """UPDATE penguin_connect_sync_state
               SET next_full_verify_at = ?,
                   updated_at = datetime('now')
               WHERE conversation_id = ?""",
            (next_full_verify_at, row["conversation_id"]),
        )
        updated += 1
    return updated


def _mark_conversation_full_verify_completed(conn: sqlite3.Connection, conversation_id: str):
    completed_at = _now_iso()
    next_full_verify_at = schedule_next_full_verify_at(conversation_id, base_iso=completed_at)
    conn.execute(
        """INSERT INTO penguin_connect_sync_state
           (conversation_id, full_verify_completed_at, next_full_verify_at, last_synced_at, updated_at)
           VALUES (?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(conversation_id) DO UPDATE SET
             full_verify_completed_at = excluded.full_verify_completed_at,
             next_full_verify_at = excluded.next_full_verify_at,
             last_synced_at = datetime('now'),
             updated_at = datetime('now')""",
        (conversation_id, completed_at, next_full_verify_at),
    )


def _conversation_needs_initial_bootstrap(state: Optional[sqlite3.Row]) -> bool:
    return not bool(state and state["initial_sync_completed_at"])


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
                conn.commit()
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
            quoted_plain, quoted_html, quoted_header = _quoted_reply_context_for_imessage_message(
                conn,
                conv["conversation_id"],
                row["message_timestamp"],
                row["provider_message_id"],
            )
            plain_body, html_body = _build_nested_reply_bodies(
                body_text=row["body_text"] or "",
                attachment_note=None,
                quoted_plain=quoted_plain,
                quoted_html=quoted_html,
                quoted_header=quoted_header,
            )
            metadata["email_body_plain"] = plain_body
            metadata["email_body_html"] = html_body
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
                quoted_plain=quoted_plain,
                quoted_html=quoted_html,
                quoted_header=quoted_header,
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
                log_action(
                    "imessage_to_gmail_retry_result",
                    success=False,
                    error=import_error or "gmail_import_failed",
                    provider_message_id=row["provider_message_id"],
                    source_message_timestamp=row["message_timestamp"],
                    gmail_thread_id=row["gmail_thread_id"] or thread_id,
                    **_conversation_log_fields(conv),
                    **message_fingerprint(row["body_text"] or ""),
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
                log_action(
                    "imessage_to_gmail_retry_result",
                    success=True,
                    provider_message_id=row["provider_message_id"],
                    gmail_message_id=gmail_msg_id,
                    gmail_thread_id=thread_id,
                    source_message_timestamp=row["message_timestamp"],
                    **_conversation_log_fields(conv),
                    **message_fingerprint(row["body_text"] or ""),
                )

            conn.commit()
            processed += 1
            if processed >= 500:
                break

        if len(rows) < page_size:
            break

    return imported, thread_id


def _retry_pending_imessage_to_gmail_globally(
    conn: sqlite3.Connection,
    gmail_service,
    gmail_email: str,
    *,
    mode: str,
    run_id: Optional[str] = None,
    verify_all: bool = False,
) -> dict[str, int]:
    limit = _imessage_global_retry_conversations_per_run()
    if limit <= 0:
        return {
            "retried_conversations": 0,
            "imported_messages": 0,
            "skipped_locked_conversations": 0,
        }

    rows = conn.execute(
        """SELECT c.*
           FROM penguin_connect_conversations c
           JOIN (
             SELECT m.conversation_id,
                    MIN(COALESCE(json_extract(m.metadata, '$.first_failed_at'), m.message_timestamp, '')) AS oldest_pending_at,
                    MIN(m.id) AS oldest_pending_id
             FROM penguin_connect_messages m
             JOIN penguin_connect_conversations c2 ON c2.conversation_id = m.conversation_id
             WHERE c2.gmail_email = ?
               AND c2.status = 'active'
               AND COALESCE(c2.exclude_from_sync, 0) = 0
               AND m.provider = 'imessage'
               AND m.direction = 'imessage_to_email'
               AND m.gmail_message_id IS NULL
               AND COALESCE(json_extract(m.metadata, '$.delivery_status'), 'pending') NOT IN
                 ('delivered', 'failed_permanent', 'blocked', 'ignored')
             GROUP BY m.conversation_id
           ) pending ON pending.conversation_id = c.conversation_id
           ORDER BY pending.oldest_pending_at ASC, pending.oldest_pending_id ASC
           LIMIT ?""",
        (gmail_email, limit),
    ).fetchall()
    if not rows:
        return {
            "retried_conversations": 0,
            "imported_messages": 0,
            "skipped_locked_conversations": 0,
        }

    imported_messages = 0
    retried_conversations = 0
    skipped_locked_conversations = 0
    gmail_write_pause_seconds = _sync_gmail_write_pause_seconds(mode, verify_all)

    for conv in rows:
        conversation_run_id = run_id or _new_sync_run_id(mode)
        acquired, _existing_lock = _try_acquire_conversation_sync(
            conv["conversation_id"],
            run_id=conversation_run_id,
            mode=mode,
        )
        if not acquired:
            skipped_locked_conversations += 1
            continue
        try:
            imported, thread_id = _retry_pending_imessage_to_gmail(
                conn,
                gmail_service,
                conv,
                gmail_write_pause_seconds=gmail_write_pause_seconds,
            )
            canonical_thread_id = (
                _resolve_canonical_gmail_thread_id(
                    conn,
                    conv["conversation_id"],
                    thread_id or conv["gmail_thread_id"],
                )
                or thread_id
            )
            if canonical_thread_id and canonical_thread_id != conv["gmail_thread_id"]:
                conn.execute(
                    """UPDATE penguin_connect_conversations
                       SET gmail_thread_id = ?, last_synced_at = datetime('now')
                       WHERE conversation_id = ?""",
                    (canonical_thread_id, conv["conversation_id"]),
                )
            elif imported:
                conn.execute(
                    """UPDATE penguin_connect_conversations
                       SET last_synced_at = datetime('now')
                       WHERE conversation_id = ?""",
                    (conv["conversation_id"],),
                )
            conn.commit()
            imported_messages += imported
            retried_conversations += 1
        finally:
            _release_conversation_sync(conv["conversation_id"], run_id=conversation_run_id)

    return {
        "retried_conversations": retried_conversations,
        "imported_messages": imported_messages,
        "skipped_locked_conversations": skipped_locked_conversations,
    }

def _apple_messages_chat_routes_for_conversation(conv: sqlite3.Row | dict[str, Any]) -> list[str]:
    active_chat_id = (conv["imessage_chat_id"] or "").strip()
    if not active_chat_id:
        return []

    source_provider = _conversation_source_provider(conv)
    chat_type = (conv["chat_type"] or "").strip().lower()
    if source_provider != "apple_messages" or chat_type != "dm":
        return [active_chat_id]

    routes = _list_apple_messages_chat_routes(active_chat_id)
    ordered_routes: list[str] = []
    seen: set[str] = set()

    def add_route(chat_id: str) -> None:
        normalized = (chat_id or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        ordered_routes.append(normalized)

    add_route(active_chat_id)
    for route in routes:
        add_route((route.get("guid") or route.get("chat_id") or "").strip())
    return ordered_routes


def _fetch_apple_messages_messages_for_conversation(
    conv: sqlite3.Row | dict[str, Any],
    *,
    limit: int,
    since: Optional[str],
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 50), 1000))
    route_ids = _apple_messages_chat_routes_for_conversation(conv)
    if not route_ids:
        return []

    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for chat_id in route_ids:
        for message in fetch_imessage_messages(chat_id, limit=safe_limit, since=since):
            routed_message = dict(message)
            routed_message["chat_id"] = chat_id
            provider_message_id = _provider_message_id_for_imessage(routed_message)
            if provider_message_id in seen_ids:
                continue
            seen_ids.add(provider_message_id)
            merged.append(routed_message)

    merged.sort(key=lambda message: message.get("timestamp") or "")
    if len(merged) > safe_limit:
        merged = merged[:safe_limit]
    return merged


def _get_apple_messages_unread_count_for_conversation(conv: sqlite3.Row | dict[str, Any]) -> Optional[int]:
    route_ids = _apple_messages_chat_routes_for_conversation(conv)
    if not route_ids:
        return None

    total = 0
    for chat_id in route_ids:
        count = _get_imessage_unread_count(chat_id)
        if count is None:
            return None
        total += max(0, int(count))
    return total


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
    elif _conversation_needs_initial_bootstrap(state):
        since = FULL_IMESSAGE_SYNC_SINCE
    elif mode == "backfill":
        since = cutoff.isoformat()
    elif state and state["last_imessage_ts"]:
        since = state["last_imessage_ts"]
    else:
        since = cutoff.isoformat()

    stored = 0
    gmail_write_pause_seconds = _sync_gmail_write_pause_seconds(mode, verify_all)
    imported = 0
    thread_id = None
    if not verify_all:
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
    eligible_message_count = 0
    full_history_checked = since == FULL_IMESSAGE_SYNC_SINCE
    unread_count = _get_apple_messages_unread_count_for_conversation(conv)

    while True:
        messages = _fetch_apple_messages_messages_for_conversation(conv, limit=batch_size, since=next_since)
        if not messages:
            break
        saw_messages = True
        messages = sorted(messages, key=lambda m: m.get("timestamp") or "")

        for msg in messages:
            ts = msg.get("timestamp")
            text = msg.get("text") or ""
            if not ts or (not text and not msg.get("attachments")):
                continue
            eligible_message_count += 1
            sender_name, subject_name = _resolve_imessage_sender_and_subject(conn, conv, msg)
            source_provider = _conversation_source_provider(conv)
            desired_subject = _provider_subject(source_provider, subject_name)

            provider_id = _provider_message_id_for_imessage(msg)
            existing = conn.execute(
                """SELECT gmail_message_id, gmail_thread_id, sender_name, subject, metadata
                   FROM penguin_connect_messages
                   WHERE conversation_id = ? AND provider_message_id = ?
                   LIMIT 1""",
                (conv["conversation_id"], provider_id),
            ).fetchone()
            if existing and verify_all:
                stored_sender_name = (existing["sender_name"] or "").strip()
                stored_subject = (existing["subject"] or "").strip()
                if stored_sender_name != sender_name or stored_subject != desired_subject:
                    conn.execute(
                        """UPDATE penguin_connect_messages
                           SET sender_name = ?, subject = ?
                           WHERE conversation_id = ? AND provider_message_id = ?""",
                        (sender_name, desired_subject, conv["conversation_id"], provider_id),
                    )
                    conn.commit()
            if existing and existing["gmail_message_id"]:
                thread_id = existing["gmail_thread_id"] or thread_id
                last_ts = max(last_ts or ts, ts)
                continue
            if existing:
                # Existing pending messages are handled by the retry queue with backoff.
                last_ts = max(last_ts or ts, ts)
                continue

            is_from_me = 1 if msg.get("is_from_me") else 0
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
                "imessage_chat_id": msg.get("chat_id") or conv["imessage_chat_id"],
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
            quoted_plain, quoted_html, quoted_header = _quoted_reply_context_for_imessage_message(
                conn,
                conv["conversation_id"],
                ts,
                provider_id,
            )
            attachment_note = (
                f"[{len(msg.get('attachments') or [])} attachment(s) in {_source_provider_label(source_provider)}]"
                if msg.get("attachments")
                else None
            )
            plain_body, html_body = _build_nested_reply_bodies(
                body_text=text,
                attachment_note=attachment_note,
                quoted_plain=quoted_plain,
                quoted_html=quoted_html,
                quoted_header=quoted_header,
            )
            metadata["email_body_plain"] = plain_body
            metadata["email_body_html"] = html_body
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
                    desired_subject,
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
            # Persist the pending row before the remote Gmail import so this
            # conversation does not hold the SQLite writer lock while waiting
            # on network calls or Gmail backoff sleeps.
            conn.commit()

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
                quoted_plain=quoted_plain,
                quoted_html=quoted_html,
                quoted_header=quoted_header,
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
                log_action(
                    "imessage_to_gmail_message",
                    success=True,
                    provider_message_id=provider_id,
                    gmail_message_id=gmail_msg_id,
                    gmail_thread_id=thread_id,
                    source_message_timestamp=ts,
                    is_from_me=bool(is_from_me),
                    unread=bool(unread),
                    attachment_count=len(msg.get("attachments") or []),
                    **_conversation_log_fields(conv),
                    **message_fingerprint(text),
                )
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
                log_action(
                    "imessage_to_gmail_message",
                    success=False,
                    error=import_error or "gmail_import_failed",
                    provider_message_id=provider_id,
                    gmail_thread_id=thread_id,
                    source_message_timestamp=ts,
                    is_from_me=bool(is_from_me),
                    attachment_count=len(msg.get("attachments") or []),
                    **_conversation_log_fields(conv),
                    **message_fingerprint(text),
                )

            conn.commit()
            last_ts = max(last_ts or ts, ts)

        if not verify_all or len(messages) < batch_size:
            break
        batch_last_ts = messages[-1].get("timestamp")
        if not batch_last_ts or batch_last_ts == next_since:
            break
        next_since = batch_last_ts

    if verify_all:
        retried_imported, retried_thread_id = _retry_pending_imessage_to_gmail(
            conn,
            gmail_service,
            conv,
            gmail_write_pause_seconds=gmail_write_pause_seconds,
        )
        imported += retried_imported
        thread_id = (
            _resolve_canonical_gmail_thread_id(
                conn,
                conv["conversation_id"],
                retried_thread_id or thread_id or conv["gmail_thread_id"],
            )
            or retried_thread_id
            or thread_id
        )

    if not saw_messages:
        canonical_thread_id = thread_id
    else:
        canonical_thread_id = _resolve_canonical_gmail_thread_id(
            conn,
            conv["conversation_id"],
            thread_id or conv["gmail_thread_id"],
        )
    if canonical_thread_id and canonical_thread_id != conv["gmail_thread_id"]:
        conn.execute(
            "UPDATE penguin_connect_conversations SET gmail_thread_id = ?, last_synced_at = datetime('now') WHERE conversation_id = ?",
            (canonical_thread_id, conv["conversation_id"]),
        )

    _upsert_sync_state(conn, conv["conversation_id"], last_ts if saw_messages else (None if verify_all else since), None, None)

    _reconcile_conversation_gmail_read_state(conn, gmail_service, conv["conversation_id"], unread_count)

    bootstrap_empty_verified = full_history_checked and eligible_message_count == 0
    bootstrap_ready = bootstrap_empty_verified or _conversation_has_materialized_imessage_history(
        conn,
        conv["conversation_id"],
    )

    return {
        "imessage_imported": stored if saw_messages else 0,
        "gmail_imported": imported,
        "gmail_write_pause_seconds": gmail_write_pause_seconds,
        "bootstrap_empty_verified": bootstrap_empty_verified,
        "bootstrap_ready": bootstrap_ready,
    }


def _set_gmail_message_read_state(gmail_service, gmail_message_id: str, *, unread: bool) -> bool:
    body = {"addLabelIds": ["UNREAD"]} if unread else {"removeLabelIds": ["UNREAD"]}
    try:
        _gmail_execute(
            lambda: gmail_service.users().messages().modify(
                userId="me",
                id=gmail_message_id,
                body=body,
            ).execute()
        )
        return True
    except Exception:
        return False


def _reconcile_conversation_gmail_read_state(
    conn: sqlite3.Connection,
    gmail_service,
    conversation_id: str,
    unread_count: Optional[int],
):
    if unread_count is None:
        return

    rows = conn.execute(
        """SELECT id, gmail_message_id, message_timestamp, is_read, metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND provider = 'imessage'
             AND direction = 'imessage_to_email'
             AND gmail_message_id IS NOT NULL
           ORDER BY message_timestamp DESC, id DESC""",
        (conversation_id,),
    ).fetchall()

    unread_slots = max(0, int(unread_count))
    target_ids: set[int] = set()
    for row in rows:
        metadata = _load_metadata(row["metadata"])
        if metadata.get("is_from_me"):
            continue
        if len(target_ids) >= unread_slots:
            break
        target_ids.add(int(row["id"]))

    for row in rows:
        row_id = int(row["id"])
        should_be_unread = row_id in target_ids
        is_unread = not bool(row["is_read"])
        if should_be_unread == is_unread:
            continue
        if not _set_gmail_message_read_state(
            gmail_service,
            row["gmail_message_id"],
            unread=should_be_unread,
        ):
            continue
        conn.execute(
            "UPDATE penguin_connect_messages SET is_read = ? WHERE id = ?",
            (0 if should_be_unread else 1, row_id),
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
    for value in values:
        raw_value = (value or "").strip()
        if not raw_value:
            continue
        for _name, addr in email.utils.getaddresses([raw_value]):
            normalized = _normalize_email(addr)
            if normalized:
                recipients.append(normalized)
    return recipients


def _get_gmail_mailbox_history_id(gmail_service) -> Optional[str]:
    data = _gmail_execute(lambda: gmail_service.users().getProfile(userId="me").execute())
    history_id = (data.get("historyId") or "").strip()
    return history_id or None


def _merge_recent_gmail_activity(
    existing: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = {conversation_id: dict(row) for conversation_id, row in existing.items()}
    for conversation_id, row in candidate.items():
        candidate_ts = row.get("last_message_at")
        if not merged.get(conversation_id):
            merged[conversation_id] = {
                "last_message_at": candidate_ts,
                "message_count": int(row.get("message_count") or 0),
            }
            continue
        current = merged[conversation_id]
        current["last_message_at"] = _max_iso_value(current.get("last_message_at"), candidate_ts)
        current["message_count"] = int(current.get("message_count") or 0) + int(row.get("message_count") or 0)
    return merged


def _record_pending_gmail_activity(
    conn: sqlite3.Connection,
    recent_by_conversation: dict[str, dict[str, Any]],
) -> dict[str, int]:
    recorded = 0
    advanced = 0
    for conversation_id, row in recent_by_conversation.items():
        activity_at = row.get("last_message_at")
        if not activity_at:
            continue
        existing = conn.execute(
            """SELECT pending_gmail_activity_at
               FROM penguin_connect_sync_state
               WHERE conversation_id = ?""",
            (conversation_id,),
        ).fetchone()
        previous_pending = existing["pending_gmail_activity_at"] if existing else None
        next_pending = _max_iso_value(previous_pending, activity_at)
        conn.execute(
            """INSERT INTO penguin_connect_sync_state
               (conversation_id, pending_gmail_activity_at, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(conversation_id) DO UPDATE SET
                 pending_gmail_activity_at = CASE
                   WHEN penguin_connect_sync_state.pending_gmail_activity_at IS NULL THEN excluded.pending_gmail_activity_at
                   WHEN excluded.pending_gmail_activity_at IS NULL THEN penguin_connect_sync_state.pending_gmail_activity_at
                   WHEN penguin_connect_sync_state.pending_gmail_activity_at >= excluded.pending_gmail_activity_at
                     THEN penguin_connect_sync_state.pending_gmail_activity_at
                   ELSE excluded.pending_gmail_activity_at
                 END,
                 updated_at = datetime('now')""",
            (conversation_id, activity_at),
        )
        if next_pending != previous_pending:
            recorded += 1
            if previous_pending:
                advanced += 1
            log_action(
                "gmail_pending_activity_recorded",
                conversation_id=conversation_id,
                pending_gmail_activity_at=next_pending,
                previous_pending_gmail_activity_at=previous_pending,
                message_count=int(row.get("message_count") or 0),
            )
    return {
        "recorded": recorded,
        "advanced": advanced,
    }


def _clear_pending_gmail_activity_if_caught_up(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    force: bool = False,
) -> bool:
    row = conn.execute(
        """SELECT last_gmail_ts, pending_gmail_activity_at
           FROM penguin_connect_sync_state
           WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    if not row or not row["pending_gmail_activity_at"]:
        return False
    if not force and _recent_activity_sort_value(row["last_gmail_ts"]) < _recent_activity_sort_value(
        row["pending_gmail_activity_at"]
    ):
        return False
    conn.execute(
        """UPDATE penguin_connect_sync_state
           SET pending_gmail_activity_at = NULL,
               updated_at = datetime('now')
           WHERE conversation_id = ?""",
        (conversation_id,),
    )
    log_action(
        "gmail_pending_activity_cleared",
        conversation_id=conversation_id,
        previous_pending_gmail_activity_at=row["pending_gmail_activity_at"],
        last_gmail_ts=row["last_gmail_ts"],
        force=force,
    )
    return True


def _list_recent_sent_alias_mailbox_activity(
    gmail_service,
    conversations: list[sqlite3.Row],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    alias_lookup = {
        _normalize_email(conv["alias_email"]): conv["conversation_id"]
        for conv in conversations
        if conv["alias_email"]
    }
    if not alias_lookup:
        return {}, {}

    last_gmail_lookup = {
        conv["conversation_id"]: conv["last_gmail_ts"]
        for conv in conversations
        if conv["conversation_id"]
    }
    after_dt = _gmail_activity_backstop_cutoff()
    query = f"in:sent after:{int(after_dt.timestamp())}"
    recent_by_conversation: dict[str, dict[str, Any]] = {}
    page_token = None
    scanned_messages = 0

    while scanned_messages < DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES:
        params = {
            "userId": "me",
            "q": query,
            "maxResults": 100,
            "includeSpamTrash": False,
        }
        if page_token:
            params["pageToken"] = page_token

        data = _gmail_execute(lambda params=params: gmail_service.users().messages().list(**params).execute())
        payload = data if isinstance(data, dict) else {}
        for message in payload.get("messages", []) or []:
            message_id = message.get("id")
            if not message_id:
                continue
            scanned_messages += 1
            try:
                metadata = _gmail_execute(
                    lambda message_id=message_id: gmail_service.users().messages().get(
                        userId="me",
                        id=message_id,
                        format="metadata",
                        metadataHeaders=["To", "Cc", "Delivered-To", "X-Original-To", "X-Forwarded-To"],
                    ).execute()
                )
            except Exception as exc:
                if _extract_gmail_error_status(exc) == 404:
                    log_action("gmail_sent_backstop_message_missing", gmail_message_id=message_id)
                    if scanned_messages >= DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES:
                        break
                    continue
                raise
            if _gmail_to_source_ignore_reason(metadata.get("labelIds") or []):
                if scanned_messages >= DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES:
                    break
                continue
            headers = _gmail_header_map(metadata.get("payload") or {})
            conversation_id = None
            for recipient in _extract_alias_recipients(headers):
                conversation_id = alias_lookup.get(recipient)
                if conversation_id:
                    break
            if not conversation_id:
                if scanned_messages >= DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES:
                    break
                continue
            message_ts = _iso_from_gmail_internal_date(metadata.get("internalDate"))
            if _recent_activity_sort_value(message_ts) <= _recent_activity_sort_value(last_gmail_lookup.get(conversation_id)):
                if scanned_messages >= DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES:
                    break
                continue
            existing = recent_by_conversation.get(conversation_id)
            if not existing:
                recent_by_conversation[conversation_id] = {
                    "last_message_at": message_ts,
                    "message_count": 1,
                }
            else:
                existing["last_message_at"] = _max_iso_value(existing.get("last_message_at"), message_ts)
                existing["message_count"] = int(existing.get("message_count") or 0) + 1
            if scanned_messages >= DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES:
                break

        if scanned_messages >= DEFAULT_GMAIL_ACTIVITY_BACKSTOP_MAX_MESSAGES:
            break
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return recent_by_conversation, {
        "gmail_activity_backstop_cutoff": after_dt.isoformat(),
        "gmail_activity_backstop_scanned_messages": scanned_messages,
        "gmail_activity_backstop_matches": len(recent_by_conversation),
    }


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

    recent_by_conversation: dict[str, dict[str, Any]] = {}
    poll_state = _get_poll_state(conn, gmail_email)
    start_history_id = (poll_state["last_gmail_history_id"] or "").strip() if poll_state else ""
    latest_history_id = start_history_id
    meta: dict[str, Any] = {}
    history_initialized = False
    history_reset = False

    if not start_history_id:
        current_history_id = _get_gmail_mailbox_history_id(gmail_service)
        if current_history_id:
            _upsert_poll_state(conn, gmail_email, last_gmail_history_id=current_history_id)
            conn.commit()
            latest_history_id = current_history_id
        history_initialized = bool(current_history_id)
    else:
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
                        conn.commit()
                        latest_history_id = current_history_id
                    history_reset = True
                    break
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
                    try:
                        metadata = _gmail_execute(
                            lambda message_id=message_id: gmail_service.users().messages().get(
                                userId="me",
                                id=message_id,
                                format="metadata",
                                metadataHeaders=["To", "Cc", "Delivered-To", "X-Original-To", "X-Forwarded-To"],
                            ).execute()
                        )
                    except Exception as exc:
                        if _extract_gmail_error_status(exc) == 404:
                            log_action(
                                "gmail_history_message_missing",
                                gmail_email=gmail_email,
                                gmail_message_id=message_id,
                                gmail_history_id=history_id,
                            )
                            continue
                        raise
                    headers = _gmail_header_map(metadata.get("payload") or {})
                    if _gmail_to_source_ignore_reason(metadata.get("labelIds") or []):
                        continue
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
                    existing["last_message_at"] = _max_iso_value(existing.get("last_message_at"), message_ts)
                    existing["message_count"] = int(existing.get("message_count") or 0) + 1

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        if latest_history_id and latest_history_id != start_history_id and not history_reset:
            _upsert_poll_state(conn, gmail_email, last_gmail_history_id=latest_history_id)
            conn.commit()

    if history_initialized:
        meta["history_initialized"] = True
    if history_reset:
        meta["history_reset"] = True
    if latest_history_id:
        meta["last_gmail_history_id"] = latest_history_id

    if history_initialized or history_reset or not recent_by_conversation:
        mailbox_recent, mailbox_meta = _list_recent_sent_alias_mailbox_activity(gmail_service, conversations)
        recent_by_conversation = _merge_recent_gmail_activity(recent_by_conversation, mailbox_recent)
        if mailbox_recent:
            meta["gmail_activity_backstop_used"] = True
        for key in (
            "gmail_activity_backstop_cutoff",
            "gmail_activity_backstop_scanned_messages",
            "gmail_activity_backstop_matches",
        ):
            if mailbox_meta.get(key) is not None:
                meta[key] = mailbox_meta[key]

    pending_stats = _record_pending_gmail_activity(conn, recent_by_conversation)
    detected_messages = sum(int(row.get("message_count") or 0) for row in recent_by_conversation.values())
    if (
        recent_by_conversation
        or pending_stats["recorded"]
        or meta.get("history_initialized")
        or meta.get("history_reset")
        or meta.get("gmail_activity_backstop_used")
    ):
        log_action(
            "gmail_alias_activity_scan_result",
            gmail_email=gmail_email,
            detected_conversations=len(recent_by_conversation),
            detected_messages=detected_messages,
            pending_activity_recorded=pending_stats["recorded"],
            pending_activity_advanced=pending_stats["advanced"],
            history_initialized=bool(meta.get("history_initialized")),
            history_reset=bool(meta.get("history_reset")),
            gmail_activity_backstop_used=bool(meta.get("gmail_activity_backstop_used")),
            gmail_activity_backstop_matches=meta.get("gmail_activity_backstop_matches"),
            gmail_activity_backstop_scanned_messages=meta.get("gmail_activity_backstop_scanned_messages"),
            last_gmail_history_id=meta.get("last_gmail_history_id"),
        )
    return recent_by_conversation, meta


def _list_gmail_messages_to_alias(gmail_service, alias_email: str, after_iso: str) -> list[dict[str, Any]]:
    after_dt = _parse_iso(after_iso) or (datetime.now(timezone.utc) - timedelta(days=DEFAULT_BACKFILL_DAYS))
    after_epoch = int(after_dt.timestamp())
    query = f"to:{alias_email} after:{after_epoch} in:sent"

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
        payload = data if isinstance(data, dict) else {}
        result.extend(payload.get("messages", []) or [])
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return result


def _list_gmail_draft_messages_to_alias(gmail_service, alias_email: str) -> list[dict[str, Any]]:
    query = f"to:{alias_email} in:drafts"
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
        payload = data if isinstance(data, dict) else {}
        result.extend(payload.get("messages", []) or [])
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return result


def _list_gmail_draft_ids_by_message_id(
    gmail_service,
    candidate_message_ids: set[str],
    *,
    scan_limit: int = 500,
) -> dict[str, str]:
    if not candidate_message_ids:
        return {}

    draft_ids: dict[str, str] = {}
    page_token = None
    scanned = 0
    while True:
        params = {
            "userId": "me",
            "maxResults": 100,
        }
        if page_token:
            params["pageToken"] = page_token

        data = _gmail_execute(lambda params=params: gmail_service.users().drafts().list(**params).execute())
        payload = data if isinstance(data, dict) else {}
        for draft in payload.get("drafts") or []:
            scanned += 1
            message = draft.get("message") or {}
            message_id = (message.get("id") or "").strip()
            draft_id = (draft.get("id") or "").strip()
            if message_id in candidate_message_ids and draft_id:
                draft_ids[message_id] = draft_id
            if scanned >= scan_limit or len(draft_ids) >= len(candidate_message_ids):
                break

        if scanned >= scan_limit or len(draft_ids) >= len(candidate_message_ids):
            break
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return draft_ids


def _parse_sender_email(from_header: str) -> str:
    _, addr = email.utils.parseaddr(from_header or "")
    return _normalize_email(addr)


def _normalize_gmail_label_ids(label_ids: Optional[list[str]]) -> set[str]:
    normalized: set[str] = set()
    for label_id in label_ids or []:
        candidate = (label_id or "").strip().upper()
        if candidate:
            normalized.add(candidate)
    return normalized


def _gmail_to_source_ignore_reason(label_ids: Optional[list[str]]) -> Optional[str]:
    normalized = _normalize_gmail_label_ids(label_ids)
    if "DRAFT" in normalized:
        return "gmail_draft_message"
    if "SENT" not in normalized:
        return "gmail_message_not_sent"
    return None


def _gmail_message_targets_alias(headers: dict[str, str], alias_email: str) -> bool:
    expected = _normalize_email(alias_email)
    if not expected:
        return False
    return expected in _extract_alias_recipients(headers)


def _alias_draft_delete_minutes() -> int:
    return _env_int("PENGUIN_CONNECT_ALIAS_DRAFT_DELETE_MINUTES", 30, 5, 7 * 24 * 60)


def _delete_gmail_draft(gmail_service, draft_id: str) -> Optional[str]:
    try:
        _gmail_execute(
            lambda draft_id=draft_id: gmail_service.users().drafts().delete(
                userId="me",
                id=draft_id,
            ).execute()
        )
        return None
    except _GmailRetryableError:
        raise
    except Exception:
        return "gmail_draft_delete_failed"


def _cleanup_stale_alias_drafts(
    conn: sqlite3.Connection,
    gmail_service,
    conv: sqlite3.Row | dict[str, Any],
    canonical_thread_id: Optional[str],
) -> int:
    canonical = (canonical_thread_id or "").strip()
    alias_email = _normalize_email(conv["alias_email"])
    if not canonical or not alias_email:
        return 0
    if not _thread_is_bridge_owned(conn, conv["conversation_id"], canonical):
        return 0

    draft_messages = _list_gmail_draft_messages_to_alias(gmail_service, alias_email)
    if not draft_messages:
        return 0

    draft_id_by_message_id = _list_gmail_draft_ids_by_message_id(
        gmail_service,
        {(message.get("id") or "").strip() for message in draft_messages if (message.get("id") or "").strip()},
    )
    delete_before = datetime.now(timezone.utc) - timedelta(minutes=_alias_draft_delete_minutes())
    deleted = 0
    for message in draft_messages:
        message_id = (message.get("id") or "").strip()
        if not message_id:
            continue

        draft_id = draft_id_by_message_id.get(message_id)
        if not draft_id:
            continue

        full = _gmail_execute(
            lambda message_id=message_id: gmail_service.users().messages().get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["To", "Cc", "Delivered-To", "X-Original-To", "X-Forwarded-To"],
            ).execute()
        )
        headers = _gmail_header_map(full.get("payload") or {})
        label_ids = full.get("labelIds") or []
        if _gmail_to_source_ignore_reason(label_ids) != "gmail_draft_message":
            continue
        if not _gmail_message_targets_alias(headers, alias_email):
            continue

        thread_id = (full.get("threadId") or "").strip()
        if not thread_id or thread_id == canonical:
            continue

        draft_ts = _parse_iso(_iso_from_gmail_internal_date(full.get("internalDate")))
        if not draft_ts or draft_ts > delete_before:
            continue

        error = _delete_gmail_draft(gmail_service, draft_id)
        log_action(
            "gmail_alias_draft_cleanup",
            success=not bool(error),
            error=error,
            gmail_draft_id=draft_id,
            gmail_message_id=message_id,
            gmail_thread_id=thread_id,
            canonical_gmail_thread_id=canonical,
            draft_age_seconds=max(0, int((datetime.now(timezone.utc) - draft_ts).total_seconds())),
            **_conversation_log_fields(conv),
        )
        if not error:
            deleted += 1

    return deleted


def _maybe_send_gmail_delivery_error_notice(
    conn: sqlite3.Connection,
    gmail_service,
    conv: sqlite3.Row | dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if not gmail_service:
        return metadata
    if _delivery_status("email_to_imessage", None, metadata) != "failed_permanent":
        return metadata
    if metadata.get("error_notice_sent_at"):
        return metadata

    thread_id = (
        metadata.get("error_notice_gmail_thread_id")
        or metadata.get("gmail_thread_id")
        or row["gmail_thread_id"]
        or conv["gmail_thread_id"]
    )
    raw_email, notice_rfc_message_id = _build_gmail_delivery_error_email(conv, row, metadata)
    imported_data, import_error, recovered_thread_id = _import_message_to_gmail_with_thread_recovery(
        gmail_service,
        raw_email,
        thread_id,
        True,
        _normalize_rfc_message_id(metadata.get("rfc_message_id")),
    )
    if import_error or not imported_data:
        metadata["error_notice_last_error"] = import_error or "delivery_error_notice_failed"
        log_action(
            "gmail_to_imessage_error_notice",
            success=False,
            error=metadata["error_notice_last_error"],
            provider_message_id=row["provider_message_id"],
            gmail_message_id=metadata.get("gmail_message_id") or row["gmail_message_id"],
            gmail_thread_id=thread_id,
            **_conversation_log_fields(conv),
            **message_fingerprint(row["body_text"] or ""),
        )
        return metadata

    metadata["error_notice_sent_at"] = _now_iso()
    metadata["error_notice_last_error"] = None
    metadata["error_notice_gmail_message_id"] = imported_data.get("id")
    metadata["error_notice_gmail_thread_id"] = recovered_thread_id or imported_data.get("threadId") or thread_id
    metadata["error_notice_rfc_message_id"] = notice_rfc_message_id
    log_action(
        "gmail_to_imessage_error_notice",
        success=True,
        provider_message_id=row["provider_message_id"],
        gmail_message_id=metadata.get("gmail_message_id") or row["gmail_message_id"],
        gmail_thread_id=metadata["error_notice_gmail_thread_id"],
        error_notice_gmail_message_id=metadata["error_notice_gmail_message_id"],
        **_conversation_log_fields(conv),
        **message_fingerprint(row["body_text"] or ""),
    )
    return metadata


def _new_gmail_error_notice_sent(previous_metadata: dict[str, Any], metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("error_notice_sent_at")) and not bool(previous_metadata.get("error_notice_sent_at"))


def _maybe_send_gmail_delivery_rejection_notice(
    conn: sqlite3.Connection,
    gmail_service,
    conv: sqlite3.Row | dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if not gmail_service:
        return metadata
    if _delivery_status("email_to_imessage", None, metadata) != "ignored":
        return metadata
    if (metadata.get("reason") or "").strip() != "ambiguous_email_body":
        return metadata
    if metadata.get("rejection_notice_sent_at"):
        return metadata

    thread_id = (
        metadata.get("rejection_notice_gmail_thread_id")
        or metadata.get("gmail_thread_id")
        or row["gmail_thread_id"]
        or conv["gmail_thread_id"]
    )
    raw_email, notice_rfc_message_id = _build_gmail_delivery_rejection_email(conv, row, metadata)
    imported_data, import_error, recovered_thread_id = _import_message_to_gmail_with_thread_recovery(
        gmail_service,
        raw_email,
        thread_id,
        True,
        _normalize_rfc_message_id(metadata.get("rfc_message_id")),
    )
    if import_error or not imported_data:
        metadata["rejection_notice_last_error"] = import_error or "delivery_rejection_notice_failed"
        log_action(
            "gmail_to_imessage_rejection_notice",
            success=False,
            error=metadata["rejection_notice_last_error"],
            provider_message_id=row["provider_message_id"],
            gmail_message_id=metadata.get("gmail_message_id") or row["gmail_message_id"],
            gmail_thread_id=thread_id,
            **_conversation_log_fields(conv),
            **message_fingerprint(row["body_text"] or ""),
        )
        return metadata

    metadata["rejection_notice_sent_at"] = _now_iso()
    metadata["rejection_notice_last_error"] = None
    metadata["rejection_notice_gmail_message_id"] = imported_data.get("id")
    metadata["rejection_notice_gmail_thread_id"] = recovered_thread_id or imported_data.get("threadId") or thread_id
    metadata["rejection_notice_rfc_message_id"] = notice_rfc_message_id
    log_action(
        "gmail_to_imessage_rejection_notice",
        success=True,
        provider_message_id=row["provider_message_id"],
        gmail_message_id=metadata.get("gmail_message_id") or row["gmail_message_id"],
        gmail_thread_id=metadata["rejection_notice_gmail_thread_id"],
        rejection_notice_gmail_message_id=metadata["rejection_notice_gmail_message_id"],
        **_conversation_log_fields(conv),
        **message_fingerprint(row["body_text"] or ""),
    )
    return metadata


def _new_gmail_rejection_notice_sent(previous_metadata: dict[str, Any], metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("rejection_notice_sent_at")) and not bool(previous_metadata.get("rejection_notice_sent_at"))


def _latest_synced_source_message_ts(conn: sqlite3.Connection, conversation_id: str) -> Optional[str]:
    state = conn.execute(
        "SELECT last_imessage_ts FROM penguin_connect_sync_state WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    latest = state["last_imessage_ts"] if state else None
    row = conn.execute(
        """SELECT message_timestamp
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND direction = 'imessage_to_email'
             AND message_timestamp IS NOT NULL
           ORDER BY message_timestamp DESC
           LIMIT 1""",
        (conversation_id,),
    ).fetchone()
    if row:
        latest = _max_iso_value(latest, row["message_timestamp"])
    return latest


def _normalized_gmail_delivery_body(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def _find_recent_gmail_to_source_duplicate(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    gmail_thread_id: Optional[str],
    sender_email: Optional[str],
    body_text: Optional[str],
    message_ts: Optional[str],
    window_seconds: int = 180,
) -> Optional[sqlite3.Row]:
    normalized_body = _normalized_gmail_delivery_body(body_text)
    message_dt = _parse_iso(message_ts)
    if not gmail_thread_id or not normalized_body or not message_dt:
        return None

    sender = _normalize_email(sender_email)
    window_start = (message_dt - timedelta(seconds=max(1, window_seconds))).isoformat()
    rows = conn.execute(
        """SELECT provider_message_id, gmail_message_id, gmail_thread_id, sender_email, body_text, message_timestamp, metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ?
             AND provider = 'gmail'
             AND direction = 'email_to_imessage'
             AND gmail_thread_id = ?
             AND lower(COALESCE(sender_email, '')) = lower(?)
             AND message_timestamp >= ?
           ORDER BY message_timestamp DESC
           LIMIT 25""",
        (conversation_id, gmail_thread_id, sender, window_start),
    ).fetchall()
    for row in rows:
        if _normalized_gmail_delivery_body(row["body_text"]) != normalized_body:
            continue
        return row
    return None


def _fail_stale_gmail_to_source_delivery(
    conn: sqlite3.Connection,
    conv: sqlite3.Row | dict[str, Any],
    row: sqlite3.Row | dict[str, Any],
    metadata: dict[str, Any],
    *,
    message_ts: str,
    gmail_service=None,
    log_event: str,
) -> tuple[bool, dict[str, Any]]:
    latest_source_ts = _latest_synced_source_message_ts(conn, conv["conversation_id"])
    latest_dt = _parse_iso(latest_source_ts)
    message_dt = _parse_iso(message_ts)
    if not latest_dt or not message_dt or latest_dt <= message_dt:
        return False, metadata

    now_dt = datetime.now(timezone.utc)
    error = "newer_source_message_synced"
    metadata = _mark_delivery_failed_permanent(metadata, "send_result", error, now_dt)
    metadata["reason"] = error
    metadata["stale_gmail_message_timestamp"] = message_ts
    metadata["newer_source_message_timestamp"] = latest_source_ts
    metadata = _maybe_send_gmail_delivery_error_notice(conn, gmail_service, conv, row, metadata)
    log_action(
        log_event,
        success=False,
        error=error,
        provider_message_id=_record_value(row, "provider_message_id"),
        gmail_message_id=metadata.get("gmail_message_id") or _record_value(row, "gmail_message_id"),
        gmail_thread_id=metadata.get("gmail_thread_id") or _record_value(row, "gmail_thread_id"),
        stale_gmail_message_timestamp=message_ts,
        newer_source_message_timestamp=latest_source_ts,
        **_conversation_log_fields(conv),
        **message_fingerprint(_record_value(row, "body_text") or ""),
    )
    return True, metadata


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
            """SELECT id, provider_message_id, gmail_message_id, gmail_thread_id, subject, body_text,
                      message_timestamp, metadata
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
            if status == "failed_permanent":
                previous_metadata = dict(metadata)
                metadata = _maybe_send_gmail_delivery_error_notice(conn, gmail_service, conv, row, metadata)
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET metadata = ?
                    WHERE conversation_id = ? AND provider_message_id = ?""",
                    (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                )
                conn.commit()
                continue
            if status in {"delivered", "blocked", "ignored"}:
                continue

            if not _should_attempt_delivery_retry(metadata, now_dt):
                previous_metadata = dict(metadata)
                metadata = _maybe_send_gmail_delivery_error_notice(conn, gmail_service, conv, row, metadata)
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET metadata = ?
                    WHERE conversation_id = ? AND provider_message_id = ?""",
                    (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                )
                conn.commit()
                continue

            previous_metadata = dict(metadata)
            is_stale, metadata = _fail_stale_gmail_to_source_delivery(
                conn,
                conv,
                row,
                metadata,
                message_ts=row["message_timestamp"],
                gmail_service=gmail_service,
                log_event="gmail_to_imessage_retry_result",
            )
            if is_stale:
                conn.execute(
                    """UPDATE penguin_connect_messages
                       SET metadata = ?
                       WHERE conversation_id = ? AND provider_message_id = ?""",
                    (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                )
                conn.commit()
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
                    previous_metadata = dict(metadata)
                    metadata = _mark_delivery_failure(
                        metadata,
                        "send_result",
                        "gmail_attachment_download_failed",
                        now_dt,
                    )
                    metadata = _maybe_send_gmail_delivery_error_notice(conn, gmail_service, conv, row, metadata)
                    conn.execute(
                        """UPDATE penguin_connect_messages
                           SET metadata = ?
                           WHERE conversation_id = ? AND provider_message_id = ?""",
                        (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
                    )
                    conn.commit()
                    log_action(
                        "gmail_to_imessage_retry_result",
                        success=False,
                        error="gmail_attachment_download_failed",
                        provider_message_id=row["provider_message_id"],
                        gmail_message_id=gmail_message_id,
                        attachment_count=len(attachment_meta),
                        **_conversation_log_fields(conv),
                    )
                continue

            ok, error = _send_to_source_conversation(
                conv,
                body_text,
                attachment_paths=attachment_paths,
                action_context={
                    "action": "gmail_to_source_retry",
                    "provider_message_id": row["provider_message_id"],
                    "gmail_message_id": gmail_message_id,
                },
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
                metadata = _maybe_send_gmail_delivery_error_notice(conn, gmail_service, conv, row, metadata)
            conn.execute(
                """UPDATE penguin_connect_messages
                   SET metadata = ?
                   WHERE conversation_id = ? AND provider_message_id = ?""",
                (json.dumps(metadata), conv["conversation_id"], row["provider_message_id"]),
            )
            conn.commit()
            log_action(
                "gmail_to_imessage_retry_result",
                success=ok,
                error=None if ok else (error or f"{_conversation_source_provider(conv)}_failed"),
                provider_message_id=row["provider_message_id"],
                gmail_message_id=gmail_message_id,
                attachment_count=len(attachment_paths),
                **_conversation_log_fields(conv),
                **message_fingerprint(body_text),
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
    elif _conversation_needs_initial_bootstrap(state):
        since = FULL_GMAIL_SYNC_SINCE
    else:
        since = (state["last_gmail_ts"] if state and state["last_gmail_ts"] else cutoff.isoformat())
    converted = _retry_pending_gmail_to_imessage(conn, conv, gmail_service=gmail_service)
    blocked = 0
    last_gmail_ts = state["last_gmail_ts"] if state else since
    history_id = state["last_gmail_history_id"] if state else None
    canonical_thread_id = _resolve_canonical_gmail_thread_id(conn, conv["conversation_id"], conv["gmail_thread_id"])
    observed_thread_ids: list[str] = []
    deleted_alias_drafts = 0

    messages = _list_gmail_messages_to_alias(gmail_service, conv["alias_email"], since)
    if not messages:
        canonical_thread_id = _resolve_canonical_gmail_thread_id(
            conn, conv["conversation_id"], canonical_thread_id or conv["gmail_thread_id"]
        )
        if canonical_thread_id:
            _apply_canonical_thread_reconciliation(conn, conv["conversation_id"], canonical_thread_id)
            conn.commit()
            deleted_alias_drafts = _cleanup_stale_alias_drafts(conn, gmail_service, conv, canonical_thread_id)
        _upsert_sync_state(conn, conv["conversation_id"], None, None if verify_all else since, None)
        _clear_pending_gmail_activity_if_caught_up(conn, conv["conversation_id"], force=True)
        return {
            "email_to_imessage": converted,
            "blocked_sender_count": blocked,
            "alias_drafts_deleted": deleted_alias_drafts,
        }

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

        try:
            full = _gmail_execute(
                lambda message_id=message_id: gmail_service.users().messages().get(
                    userId="me",
                    id=message_id,
                    format="full",
                ).execute()
            )
        except Exception as exc:
            if _extract_gmail_error_status(exc) == 404:
                log_action(
                    "gmail_alias_message_missing",
                    gmail_email=gmail_email,
                    gmail_message_id=message_id,
                    **_conversation_log_fields(conv),
                )
                continue
            raise
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
        ignore_reason = _gmail_to_source_ignore_reason(label_ids)
        if not ignore_reason and not _gmail_message_targets_alias(headers, conv["alias_email"]):
            ignore_reason = "alias_recipient_mismatch"

        if ignore_reason:
            log_action(
                "gmail_to_imessage_message",
                success=False,
                ignored=True,
                reason=ignore_reason,
                gmail_message_id=message_id,
                gmail_thread_id=thread_id,
                attachment_count=len(attachment_meta),
                sender_email=sender,
                **_conversation_log_fields(conv),
            )
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
                    _friendly_email_sender_name(from_header, sender, own_sender=False),
                    headers.get("subject") or "",
                    (full.get("snippet") or "").strip()[:20000],
                    message_ts,
                    0 if "UNREAD" in _normalize_gmail_label_ids(label_ids) else 1,
                    json.dumps(
                        {
                            "ignored": True,
                            "delivery_status": "ignored",
                            "reason": ignore_reason,
                            "gmail_message_id": message_id,
                            "gmail_thread_id": thread_id,
                            "rfc_message_id": rfc_message_id,
                            "rfc_in_reply_to": rfc_in_reply_to,
                            "rfc_references": rfc_references,
                            "attachments": attachment_meta,
                            "labels": label_ids,
                            "retry_count": 0,
                            "max_retries": _gmail_to_source_max_retries(),
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            conn.commit()
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        sender_allowed = _sender_allowed(sender, gmail_email, allowed_senders)
        stored_sender_name = _friendly_email_sender_name(from_header, sender, own_sender=sender_allowed)

        if not sender_allowed:
            blocked += 1
            log_action(
                "gmail_to_imessage_message",
                success=False,
                blocked=True,
                error="sender_not_connected_gmail",
                gmail_message_id=message_id,
                gmail_thread_id=thread_id,
                sender_email=sender,
                **_conversation_log_fields(conv),
            )
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
                    stored_sender_name,
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
                            "max_retries": _gmail_to_source_max_retries(),
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            conn.commit()
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        # Ignore bridge-generated inbound imports.
        bridge_header = (headers.get(PENGUINCONNECT_HEADER.lower()) or "").strip()
        if bridge_header in {"imessage_to_email", THREAD_REPAIR_HEADER_VALUE, DELIVERY_ERROR_HEADER_VALUE, DELIVERY_REJECTION_HEADER_VALUE}:
            log_action(
                "gmail_to_imessage_message",
                success=False,
                ignored=True,
                reason=(
                    "bridge_generated_message"
                    if bridge_header == "imessage_to_email"
                    else (
                        "thread_repair_clone"
                        if bridge_header == THREAD_REPAIR_HEADER_VALUE
                        else ("delivery_error_notice" if bridge_header == DELIVERY_ERROR_HEADER_VALUE else "delivery_rejection_notice")
                    )
                ),
                gmail_message_id=message_id,
                gmail_thread_id=thread_id,
                **_conversation_log_fields(conv),
            )
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
                    stored_sender_name,
                    headers.get("subject") or "",
                    (full.get("snippet") or "").strip()[:20000],
                    message_ts,
                    0 if "UNREAD" in label_ids else 1,
                    json.dumps(
                        {
                            "ignored": True,
                            "delivery_status": "ignored",
                            "reason": (
                                "bridge_generated_message"
                                if bridge_header == "imessage_to_email"
                                else (
                                    "thread_repair_clone"
                                    if bridge_header == THREAD_REPAIR_HEADER_VALUE
                                    else ("delivery_error_notice" if bridge_header == DELIVERY_ERROR_HEADER_VALUE else "delivery_rejection_notice")
                                )
                            ),
                            "gmail_message_id": message_id,
                            "gmail_thread_id": thread_id,
                            "rfc_message_id": rfc_message_id,
                            "rfc_in_reply_to": rfc_in_reply_to,
                            "rfc_references": rfc_references,
                            "attachments": attachment_meta,
                            "labels": label_ids,
                            "retry_count": 0,
                            "max_retries": _gmail_to_source_max_retries(),
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            conn.commit()
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        attachment_paths: list[str] = []
        attachment_delivery: dict[str, Any] = {"forwarded": [], "skipped": []}
        staged_dir: Optional[Path] = None
        plain_body_text, html_body_text = _extract_gmail_body_variants(payload)
        parsed_body = extract_latest_email_text(
            plain_text=plain_body_text,
            html_text=html_body_text,
            snippet=full.get("snippet") or "",
        )
        raw_text_source = plain_body_text or ((full.get("snippet") or "") if parsed_body.source == "snippet" else "")
        raw_html_source = html_body_text or ""
        body_text = parsed_body.text
        body_text = _rewrite_markdown_links_for_source_message(body_text)
        if body_text and not parsed_body.safe_for_send:
            log_action(
                "gmail_to_imessage_message",
                success=False,
                ignored=True,
                reason="ambiguous_email_body",
                gmail_message_id=message_id,
                gmail_thread_id=thread_id,
                gmail_body_source=parsed_body.source,
                gmail_body_safety_flags=list(parsed_body.safety_flags),
                attachment_count=len(attachment_meta),
                sender_email=sender,
                **_conversation_log_fields(conv),
                **message_fingerprint(body_text),
            )
            metadata = {
                "ignored": True,
                "delivery_status": "ignored",
                "reason": "ambiguous_email_body",
                "gmail_message_id": message_id,
                "gmail_thread_id": thread_id,
                "rfc_message_id": rfc_message_id,
                "rfc_in_reply_to": rfc_in_reply_to,
                "rfc_references": rfc_references,
                "attachments": attachment_meta,
                "attachments_forwarded": attachment_delivery.get("forwarded", []),
                "attachments_skipped": attachment_delivery.get("skipped", []),
                "source_body_text": body_text,
                "source_body_text_raw": raw_text_source,
                "source_body_html_raw": raw_html_source,
                "gmail_body_source": parsed_body.source,
                "gmail_quoted_content_removed": parsed_body.quoted_content_removed,
                "gmail_signature_removed": parsed_body.signature_removed,
                "gmail_body_safe_for_send": parsed_body.safe_for_send,
                "gmail_body_safety_flags": list(parsed_body.safety_flags),
                "labels": label_ids,
                "retry_count": 0,
                "max_retries": _gmail_to_source_max_retries(),
            }
            rejection_row = {
                "provider_message_id": f"gmail:{message_id}",
                "gmail_message_id": message_id,
                "gmail_thread_id": thread_id,
                "subject": headers.get("subject") or "",
                "body_text": body_text[:20000],
            }
            previous_metadata = dict(metadata)
            metadata = _maybe_send_gmail_delivery_rejection_notice(conn, gmail_service, conv, rejection_row, metadata)
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
                    stored_sender_name,
                    headers.get("subject") or "",
                    body_text[:20000],
                    message_ts,
                    0 if "UNREAD" in label_ids else 1,
                    json.dumps(metadata),
                    message_id,
                    thread_id,
                ),
            )
            conn.commit()
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue
        if not body_text and attachment_meta:
            preview = [a.get("filename") or a.get("mime_type") or "attachment" for a in attachment_meta[:3]]
            suffix = f" (+{len(attachment_meta) - 3} more)" if len(attachment_meta) > 3 else ""
            body_text = f"[Email attachment] {', '.join(preview)}{suffix}"
        meta = {
            "gmail_message_id": message_id,
            "gmail_thread_id": thread_id,
            "rfc_message_id": rfc_message_id,
            "rfc_in_reply_to": rfc_in_reply_to,
            "rfc_references": rfc_references,
            "attachments": attachment_meta,
            "attachments_forwarded": [],
            "attachments_skipped": [],
            "source_body_text": body_text,
            "source_body_text_raw": raw_text_source,
            "source_body_html_raw": raw_html_source,
            "gmail_body_source": parsed_body.source,
            "gmail_quoted_content_removed": parsed_body.quoted_content_removed,
            "gmail_signature_removed": parsed_body.signature_removed,
            "gmail_body_safe_for_send": parsed_body.safe_for_send,
            "gmail_body_safety_flags": list(parsed_body.safety_flags),
            "labels": label_ids,
            "retry_count": 0,
            "max_retries": _gmail_to_source_max_retries(),
            "delivery_status": "pending",
        }
        if not body_text:
            log_action(
                "gmail_to_imessage_message",
                success=False,
                ignored=True,
                reason="empty_email_body",
                gmail_message_id=message_id,
                gmail_thread_id=thread_id,
                attachment_count=len(attachment_meta),
                **_conversation_log_fields(conv),
            )
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
                    stored_sender_name,
                    headers.get("subject") or "",
                    "",
                    message_ts,
                    0 if "UNREAD" in label_ids else 1,
                    json.dumps(
                        {
                            **meta,
                            "ignored": True,
                            "delivery_status": "ignored",
                            "reason": "empty_email_body",
                            "source_body_text": "",
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            conn.commit()
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        provider_message_id = f"gmail:{message_id}"
        duplicate_row = _find_recent_gmail_to_source_duplicate(
            conn,
            conv["conversation_id"],
            gmail_thread_id=thread_id,
            sender_email=sender,
            body_text=body_text,
            message_ts=message_ts,
        )
        if duplicate_row:
            log_action(
                "gmail_to_imessage_message",
                success=False,
                ignored=True,
                duplicate=True,
                reason="duplicate_recent_gmail_message",
                duplicate_of_provider_message_id=duplicate_row["provider_message_id"],
                duplicate_of_gmail_message_id=duplicate_row["gmail_message_id"],
                gmail_message_id=message_id,
                gmail_thread_id=thread_id,
                gmail_body_source=parsed_body.source,
                gmail_quoted_content_removed=parsed_body.quoted_content_removed,
                gmail_signature_removed=parsed_body.signature_removed,
                attachment_count=len(attachment_meta),
                sender_email=sender,
                **_conversation_log_fields(conv),
                **message_fingerprint(body_text),
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
                    stored_sender_name,
                    headers.get("subject") or "",
                    body_text[:20000],
                    message_ts,
                    0 if "UNREAD" in label_ids else 1,
                    json.dumps(
                        {
                            **meta,
                            "ignored": True,
                            "delivery_status": "ignored",
                            "reason": "duplicate_recent_gmail_message",
                            "duplicate_of_provider_message_id": duplicate_row["provider_message_id"],
                            "duplicate_of_gmail_message_id": duplicate_row["gmail_message_id"],
                        }
                    ),
                    message_id,
                    thread_id,
                ),
            )
            conn.commit()
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        stale_row = {
            "provider_message_id": provider_message_id,
            "gmail_message_id": message_id,
            "gmail_thread_id": thread_id,
            "subject": headers.get("subject") or "",
            "body_text": body_text,
            "message_timestamp": message_ts,
        }
        previous_meta = dict(meta)
        is_stale, meta = _fail_stale_gmail_to_source_delivery(
            conn,
            conv,
            stale_row,
            meta,
            message_ts=message_ts,
            gmail_service=gmail_service,
            log_event="gmail_to_imessage_message",
        )
        if is_stale:
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
                    stored_sender_name,
                    headers.get("subject") or "",
                    body_text[:20000],
                    message_ts,
                    0 if "UNREAD" in label_ids else 1,
                    json.dumps(meta),
                    message_id,
                    thread_id,
                ),
            )
            conn.commit()
            last_gmail_ts = max(last_gmail_ts or message_ts, message_ts)
            history_id = full.get("historyId") or history_id
            continue

        if attachment_meta:
            attachment_paths, attachment_delivery, staged_dir = _stage_gmail_attachments_for_imessage(
                gmail_service,
                message_id,
                payload,
            )
            meta["attachments_forwarded"] = attachment_delivery.get("forwarded", [])
            meta["attachments_skipped"] = attachment_delivery.get("skipped", [])

        ok, error = _send_to_source_conversation(
            conv,
            body_text,
            attachment_paths=attachment_paths,
            action_context={
                "action": "gmail_to_source_sync",
                "provider_message_id": provider_message_id,
                "gmail_message_id": message_id,
                "gmail_thread_id": thread_id,
                "gmail_body_source": parsed_body.source,
                "gmail_quoted_content_removed": parsed_body.quoted_content_removed,
                "gmail_signature_removed": parsed_body.signature_removed,
            },
        )
        if staged_dir:
            shutil.rmtree(staged_dir, ignore_errors=True)
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
            meta = _maybe_send_gmail_delivery_error_notice(
                conn,
                gmail_service,
                conv,
                {
                    "provider_message_id": provider_message_id,
                    "gmail_message_id": message_id,
                    "gmail_thread_id": thread_id,
                    "subject": headers.get("subject") or "",
                    "body_text": body_text,
                },
                meta,
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
                stored_sender_name,
                headers.get("subject") or "",
                body_text[:20000],
                message_ts,
                0 if "UNREAD" in label_ids else 1,
                json.dumps(meta),
                message_id,
                thread_id,
            ),
        )
        conn.commit()
        log_action(
            "gmail_to_imessage_message",
            success=ok,
            error=None if ok else (error or f"{source_provider}_failed"),
            gmail_message_id=message_id,
            gmail_thread_id=thread_id,
            provider_message_id=provider_message_id,
            gmail_body_source=parsed_body.source,
            gmail_quoted_content_removed=parsed_body.quoted_content_removed,
            gmail_signature_removed=parsed_body.signature_removed,
            gmail_body_safe_for_send=parsed_body.safe_for_send,
            gmail_body_safety_flags=list(parsed_body.safety_flags),
            attachment_count=len(attachment_paths),
            sender_email=sender,
            **_conversation_log_fields(conv),
            **message_fingerprint(body_text),
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
        conn.commit()
        deleted_alias_drafts = _cleanup_stale_alias_drafts(conn, gmail_service, conv, canonical_thread_id)

    _upsert_sync_state(conn, conv["conversation_id"], None, last_gmail_ts, history_id)
    _clear_pending_gmail_activity_if_caught_up(conn, conv["conversation_id"])
    return {
        "email_to_imessage": converted,
        "blocked_sender_count": blocked,
        "alias_drafts_deleted": deleted_alias_drafts,
    }


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
    if mode in {"backfill", "startup_catchup"}:
        sweep_result = self_heal_conversation_cache(conn, gmail_email)
        conn.commit()
    else:
        ensure_conversations_discovered(conn, gmail_email)
        conn.commit()
        sweep_result = None
    exclusion_refresh = refresh_conversation_exclusions(conn, gmail_email)
    if exclusion_refresh.get("updated"):
        conn.commit()
    full_verify_schedule_backfilled = _ensure_full_verify_schedule(conn, gmail_email)
    if full_verify_schedule_backfilled:
        conn.commit()

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
        conn.commit()
        conversations, selection = _select_conversations_for_sync(
            conn,
            gmail_email,
            mode,
            days,
            hours,
            verify_all=verify_all,
            gmail_service=gmail_service,
        )
        conn.commit()
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

    verify_all_conversation_ids = set(selection.get("verify_all_conversation_ids", []))
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
        "alias_drafts_deleted": 0,
        "blocked_sender_count": 0,
        "gmail_thread_repairs": 0,
        "full_verify_completed": 0,
        "global_imessage_retry_conversations": 0,
        "global_imessage_retry_imported": 0,
    }
    if selection.get("selection_cutoff"):
        stats["selection_cutoff"] = selection["selection_cutoff"]
    if selection.get("selection_reason"):
        stats["selection_reason"] = selection["selection_reason"]
    if sweep_result:
        stats["self_heal_sweep"] = sweep_result
    if full_verify_schedule_backfilled:
        stats["full_verify_schedule_backfilled"] = full_verify_schedule_backfilled
    for key in (
        "queued_conversations",
        "selection_limit",
        "pending_bootstrap_conversations",
        "bootstrapped_conversations",
        "hot_conversations",
        "hot_imessage_conversations",
        "hot_gmail_conversations",
        "pending_full_verify_conversations",
        "scheduled_full_verify_selected",
        "gmail_history_initialized",
        "gmail_history_reset",
    ):
        if key in selection:
            stats[key] = selection[key]
    stats["failed_conversations"] = 0
    stats["conversation_errors"] = []
    stats["skipped_locked_conversations"] = 0
    run_id = _sync_runtime_started(mode, selection)
    log_action(
        "sync_run_started",
        mode=mode,
        days=days,
        hours=hours,
        verify_all=bool(verify_all),
        gmail_email=gmail_email,
        selected_conversations=len(conversations),
        selection=selection,
    )
    _print_sync_terminal_summary(
        mode,
        "run_started",
        selected=selection.get("selected_conversations"),
        strategy=selection.get("selection_strategy"),
        queued=selection.get("queued_conversations"),
        hot_imessage=selection.get("hot_imessage_conversations"),
        hot_gmail=selection.get("hot_gmail_conversations"),
        pending_bootstrap=selection.get("pending_bootstrap_conversations"),
        pending_full_verify=selection.get("pending_full_verify_conversations"),
    )

    try:
        global_imessage_retry = _retry_pending_imessage_to_gmail_globally(
            conn,
            gmail_service,
            gmail_email,
            mode=mode,
            run_id=run_id,
            verify_all=bool(verify_all),
        )
        stats["global_imessage_retry_conversations"] = global_imessage_retry["retried_conversations"]
        stats["global_imessage_retry_imported"] = global_imessage_retry["imported_messages"]
        stats["gmail_imported"] += global_imessage_retry["imported_messages"]
        stats["skipped_locked_conversations"] += global_imessage_retry["skipped_locked_conversations"]
        total = len(conversations)
        for index, conv in enumerate(conversations, start=1):
            display = conv["display_name"] or conv["conversation_id"]
            conversation_verify_all = bool(verify_all or conv["conversation_id"] in verify_all_conversation_ids)
            print(f"[PenguinConnect] Sync {mode} {index}/{total}: {display}")
            log_action(
                "sync_conversation_started",
                mode=mode,
                index=index,
                total=total,
                verify_all=conversation_verify_all,
                **_conversation_log_fields(conv),
            )
            _sync_runtime_progress(run_id, index - 1, conv)
            acquired, existing_lock = _try_acquire_conversation_sync(
                conv["conversation_id"],
                run_id=run_id,
                mode=mode,
            )
            if not acquired:
                stats["skipped_locked_conversations"] += 1
                print(
                    "[PenguinConnect] Sync "
                    f"{mode} {index}/{total} skipped for {display}: conversation_sync_in_progress"
                )
                log_action(
                    "sync_conversation_result",
                    mode=mode,
                    index=index,
                    total=total,
                    success=True,
                    skipped=True,
                    reason="conversation_sync_in_progress",
                    locked_by_mode=existing_lock.get("mode") if existing_lock else None,
                    locked_by_lane=existing_lock.get("lane") if existing_lock else None,
                    verify_all=conversation_verify_all,
                    **_conversation_log_fields(conv),
                )
                _sync_runtime_progress(run_id, index, None)
                continue
            try:
                imsg = _sync_conversation_imessage_to_gmail(
                    conn,
                    gmail_service,
                    conv,
                    mode,
                    days,
                    hours=hours,
                    cutoff_iso=selection.get("selection_cutoff"),
                    verify_all=conversation_verify_all,
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
                    verify_all=conversation_verify_all,
                )
                canonical_thread_id = _resolve_canonical_gmail_thread_id(
                    conn, conv["conversation_id"], conv["gmail_thread_id"]
                )
                repaired = _repair_split_gmail_messages(conn, gmail_service, conv["conversation_id"], canonical_thread_id)

                stats["imessage_imported"] += imsg.get("imessage_imported", 0)
                stats["gmail_imported"] += imsg.get("gmail_imported", 0)
                stats["email_to_imessage"] += gsync.get("email_to_imessage", 0)
                stats["alias_drafts_deleted"] += gsync.get("alias_drafts_deleted", 0)
                stats["blocked_sender_count"] += gsync.get("blocked_sender_count", 0)
                stats["gmail_thread_repairs"] += repaired
                bootstrap_completed = False
                bootstrap_deferred = False
                if mode in {"backfill", "startup_catchup"}:
                    if conv["initial_sync_completed_at"]:
                        bootstrap_completed = False
                    elif imsg.get("bootstrap_ready"):
                        _mark_conversation_bootstrapped(
                            conn,
                            conv["conversation_id"],
                            empty_verified=bool(imsg.get("bootstrap_empty_verified")),
                        )
                        bootstrap_completed = True
                    else:
                        bootstrap_deferred = True
                if conversation_verify_all:
                    _mark_conversation_full_verify_completed(conn, conv["conversation_id"])
                    stats["full_verify_completed"] += 1

                conn.execute(
                    "UPDATE penguin_connect_conversations SET last_synced_at = datetime('now') WHERE conversation_id = ?",
                    (conv["conversation_id"],),
                )
                conn.commit()
                imessage_imported = int(imsg.get("imessage_imported", 0))
                gmail_imported = int(imsg.get("gmail_imported", 0))
                email_to_imessage = int(gsync.get("email_to_imessage", 0))
                alias_drafts_deleted = int(gsync.get("alias_drafts_deleted", 0))
                blocked_sender_count = int(gsync.get("blocked_sender_count", 0))
                terminal_fields: dict[str, Any] = {}
                if (
                    imessage_imported
                    or gmail_imported
                    or email_to_imessage
                    or repaired
                    or alias_drafts_deleted
                    or blocked_sender_count
                ):
                    terminal_fields.update(
                        {
                            "imessage_imported": imessage_imported,
                            "gmail_imported": gmail_imported,
                            "email_to_imessage": email_to_imessage,
                            "repairs": repaired,
                            "alias_drafts_deleted": alias_drafts_deleted,
                            "blocked_senders": blocked_sender_count,
                        }
                    )
                else:
                    terminal_fields["result"] = "no_changes"
                if bootstrap_completed:
                    terminal_fields["bootstrap_completed"] = True
                elif bootstrap_deferred:
                    terminal_fields["bootstrap_pending"] = True
                if conversation_verify_all:
                    terminal_fields["full_verify_completed"] = True
                _print_sync_terminal_summary(
                    mode,
                    f"{index}/{total}_complete",
                    display=display,
                    **terminal_fields,
                )
                log_action(
                    "sync_conversation_result",
                    mode=mode,
                    index=index,
                    total=total,
                    success=True,
                    verify_all=conversation_verify_all,
                    imessage_imported=imessage_imported,
                    gmail_imported=gmail_imported,
                    email_to_imessage=email_to_imessage,
                    blocked_sender_count=blocked_sender_count,
                    alias_drafts_deleted=alias_drafts_deleted,
                    bootstrap_completed=bootstrap_completed,
                    bootstrap_pending=bootstrap_deferred,
                    full_verify_completed=conversation_verify_all,
                    gmail_thread_repairs=repaired,
                    **_conversation_log_fields(conv),
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
                log_action(
                    "sync_run_result",
                    mode=mode,
                    success=True,
                    skipped=True,
                    reason="gmail_rate_limited",
                    retry_after_seconds=exc.retry_after_seconds,
                    rate_limited_until=paused_until,
                    stats=stats,
                )
                _sync_runtime_finished(run_id, result=stats)
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
                log_action(
                    "sync_conversation_result",
                    mode=mode,
                    index=index,
                    total=total,
                    success=False,
                    verify_all=conversation_verify_all,
                    error=error,
                    **_conversation_log_fields(conv),
                )
            finally:
                _release_conversation_sync(conv["conversation_id"], run_id=run_id)
                _sync_runtime_progress(run_id, index, None)
    except Exception as exc:
        log_action("sync_run_result", mode=mode, success=False, error=str(exc).strip() or exc.__class__.__name__)
        _sync_runtime_finished(run_id, error=str(exc).strip() or exc.__class__.__name__)
        raise

    _clear_gmail_rate_limit_pause(conn, gmail_email)
    log_action("sync_run_result", mode=mode, success=True, stats=stats)
    _print_sync_terminal_summary(
        mode,
        "run_complete",
        processed=stats.get("selected_conversations"),
        imessage_imported=stats.get("imessage_imported"),
        gmail_imported=stats.get("gmail_imported"),
        email_to_imessage=stats.get("email_to_imessage"),
        alias_drafts_deleted=stats.get("alias_drafts_deleted"),
        full_verify_completed=stats.get("full_verify_completed"),
        failed=stats.get("failed_conversations"),
        skipped_locked=stats.get("skipped_locked_conversations"),
    )
    _sync_runtime_finished(run_id, result=stats)
    return stats


def sync_conversations(
    conn: sqlite3.Connection,
    mode: str = "incremental",
    days: int = DEFAULT_BACKFILL_DAYS,
    hours: Optional[int] = None,
    verify_all: bool = False,
) -> dict[str, Any]:
    with _sync_lane_lock(mode):
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
        "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        log_action("conversation_disconnect_result", conversation_id=conversation_id, success=False, error="conversation_not_found")
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
    result = {
        "success": True,
        "conversation_id": conversation_id,
        "messages_deleted": deleted_messages,
        "status": "disconnected",
    }
    log_action("conversation_disconnect_result", success=True, messages_deleted=deleted_messages, **_conversation_log_fields(conv))
    return result


def reconnect_conversation(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
    conv = conn.execute(
        "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        log_action("conversation_reconnect_result", conversation_id=conversation_id, success=False, error="conversation_not_found")
        return {"success": False, "error": "conversation_not_found"}
    refresh_conversation_exclusions(conn, conv["gmail_email"])
    conv = conn.execute(
        "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if conv["exclude_from_sync"]:
        log_action("conversation_reconnect_result", success=False, error="conversation_excluded", **_conversation_log_fields(conv))
        return {"success": False, "error": "conversation_excluded"}

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

    result = {
        "success": True,
        "conversation_id": conversation_id,
        "alias_email": alias_row["alias_email"],
        "status": "active",
    }
    log_action(
        "conversation_reconnect_result",
        success=True,
        alias_email=alias_row["alias_email"],
        **_conversation_log_fields(conv),
    )
    return result


def send_manual_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    sender_email: str,
    body_text: str,
) -> dict[str, Any]:
    body_text = _rewrite_markdown_links_for_source_message(body_text)
    conv = conn.execute(
        "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        log_action("manual_send_result", conversation_id=conversation_id, success=False, error="conversation_not_found")
        return {"success": False, "error": "conversation_not_found"}
    refresh_conversation_exclusions(conn, conv["gmail_email"])
    conv = conn.execute(
        "SELECT * FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if conv["status"] != "active":
        log_action("manual_send_result", success=False, error="conversation_disconnected", **_conversation_log_fields(conv))
        return {"success": False, "error": "conversation_disconnected"}
    if conv["exclude_from_sync"]:
        log_action("manual_send_result", success=False, error="conversation_excluded", **_conversation_log_fields(conv))
        return {"success": False, "error": "conversation_excluded"}

    account = conn.execute(
        "SELECT * FROM penguin_connect_accounts WHERE gmail_email = ? LIMIT 1",
        (conv["gmail_email"],),
    ).fetchone()
    if not account:
        log_action("manual_send_result", success=False, error="gmail_not_connected", **_conversation_log_fields(conv))
        return {"success": False, "error": "gmail_not_connected"}

    send_as = []
    try:
        send_as = json.loads(account["send_as_aliases"] or "[]")
    except Exception:
        send_as = []

    if not _sender_allowed(sender_email, conv["gmail_email"], send_as):
        log_action(
            "manual_send_result",
            success=False,
            error="sender_not_connected_gmail",
            sender_email=_normalize_email(sender_email),
            **_conversation_log_fields(conv),
        )
        return {
            "success": False,
            "error": "sender_not_connected_gmail",
            "status_code": 403,
        }

    source_provider = _conversation_source_provider(conv)
    provider_id = f"manual:{hashlib.sha1(f'{sender_email}:{_now_iso()}:{body_text}'.encode('utf-8')).hexdigest()}"
    sender_display_name = _friendly_email_sender_name(sender_email, sender_email, own_sender=True)
    ok, err = _send_to_source_conversation(
        conv,
        body_text,
        action_context={
            "action": "manual_send",
            "provider_message_id": provider_id,
            "sender_email": _normalize_email(sender_email),
        },
    )
    if not ok:
        log_action(
            "manual_send_result",
            success=False,
            error=err or f"failed_to_send_{source_provider}",
            sender_email=_normalize_email(sender_email),
            provider_message_id=provider_id,
            **_conversation_log_fields(conv),
            **message_fingerprint(body_text),
        )
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
            sender_display_name,
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
    result = {"success": True, "conversation_id": conversation_id}
    log_action(
        "manual_send_result",
        success=True,
        sender_email=_normalize_email(sender_email),
        provider_message_id=provider_id,
        **_conversation_log_fields(conv),
        **message_fingerprint(body_text),
    )
    return result


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
            result = run_sync_job_worker_once(
                conn,
                owner="startup",
                dedupe_key=enqueue_result.get("dedupe_key"),
            )
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
            mapped = _map_sync_sqlite_error(exc)
            log_action("startup_catchup_result", result=mapped)
            return mapped
        conn.commit()
        log_action("startup_catchup_result", result=result)
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
            result = run_sync_job_worker_once(
                conn,
                owner="watcher",
                dedupe_key=enqueue_result.get("dedupe_key"),
            )
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
            mapped = _map_sync_sqlite_error(exc)
            log_action("incremental_sync_result", result=mapped)
            return mapped
        conn.commit()
        log_action("incremental_sync_result", result=result)
        return result
    finally:
        conn.close()
