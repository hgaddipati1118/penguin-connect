"""Helpers for user-managed PenguinConnect conversation exclusions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

EXCLUDED_CHATS_FILE_ENV = "PENGUIN_CONNECT_EXCLUDED_CHATS_FILE"
DEFAULT_EXCLUDED_CHATS_FILE = Path(__file__).resolve().parent.parent / ".penguin_connect_excluded_chats.json"


def resolve_excluded_chats_path(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    configured = (source.get(EXCLUDED_CHATS_FILE_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_EXCLUDED_CHATS_FILE


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _value(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    try:
        return source[key]
    except Exception:
        return default


def _normalize_source_provider(value: str | None) -> str:
    normalized = _clean_str(value).lower()
    if normalized in {"apple_messages", "imessage", "sms", "rcs"}:
        return normalized
    return "imessage"


def _normalize_email(value: str | None) -> str:
    return _clean_str(value).lower()


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def _normalize_thread_participant(value: str) -> str:
    participant = _clean_str(value)
    if not participant:
        return ""
    if "@" in participant and not participant.startswith("+"):
        return participant.lower()
    phone_key = _normalize_phone(participant)
    return phone_key or participant.lower()


def _parse_participants(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = []
        raw_items = parsed if isinstance(parsed, list) else []
    else:
        raw_items = []

    participants: list[str] = []
    for item in raw_items:
        cleaned = _clean_str(item)
        if cleaned:
            participants.append(cleaned)
    return list(dict.fromkeys(participants))


def thread_key_for_chat(chat: Mapping[str, Any]) -> str:
    chat_type = _clean_str(_value(chat, "chat_type")).lower()
    if chat_type == "dm":
        for participant in _parse_participants(_value(chat, "participants")):
            normalized = _normalize_thread_participant(participant)
            if normalized:
                return f"dm:{normalized}"
        normalized = _normalize_thread_participant(
            _clean_str(_value(chat, "chat_identifier") or _value(chat, "imessage_chat_identifier"))
        )
        if normalized:
            return f"dm:{normalized}"
    chat_id = _clean_str(_value(chat, "chat_id") or _value(chat, "imessage_chat_id") or _value(chat, "chat_identifier"))
    return f"chat:{chat_id}" if chat_id else ""


def _conversation_source_provider(chat: Mapping[str, Any]) -> str:
    chat_type = _clean_str(_value(chat, "chat_type")).lower()
    if chat_type == "dm":
        return "apple_messages"
    return _normalize_source_provider(_value(chat, "source_provider") or _value(chat, "imessage_service_name"))


def deterministic_conversation_id(gmail_email: str, source_chat_id: str, source_provider: str = "imessage") -> str:
    payload = f"{_normalize_email(gmail_email)}::{_normalize_source_provider(source_provider)}::{source_chat_id}".encode(
        "utf-8"
    )
    return f"amc_{hashlib.sha256(payload).hexdigest()}"


def conversation_id_for_chat(chat: Mapping[str, Any], gmail_email: str | None) -> str:
    gmail = _normalize_email(gmail_email)
    if not gmail:
        return ""

    provider = _conversation_source_provider(chat)
    thread_key = thread_key_for_chat(chat)
    chat_id = _clean_str(_value(chat, "chat_id") or _value(chat, "imessage_chat_id"))
    source_key = thread_key if provider == "apple_messages" else chat_id
    if not source_key:
        return ""
    return deterministic_conversation_id(gmail, source_key, provider)


def exclusion_identity_key(entry: Mapping[str, Any]) -> str:
    conversation_id = _clean_str(entry.get("conversation_id"))
    if conversation_id:
        return f"conversation_id:{conversation_id}"
    thread_key = _clean_str(entry.get("thread_key"))
    if thread_key:
        return f"thread_key:{thread_key}"
    chat_id = _clean_str(entry.get("chat_id"))
    if chat_id:
        return f"chat_id:{chat_id}"
    chat_identifier = _clean_str(entry.get("chat_identifier"))
    if chat_identifier:
        return f"chat_identifier:{chat_identifier}"
    return ""


def _normalize_entry(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    conversation_id = _clean_str(entry.get("conversation_id"))
    thread_key = _clean_str(entry.get("thread_key"))
    chat_id = _clean_str(entry.get("chat_id"))
    chat_identifier = _clean_str(entry.get("chat_identifier"))
    if not any((conversation_id, thread_key, chat_id, chat_identifier)):
        return None

    participants = _parse_participants(entry.get("participants"))
    normalized: dict[str, Any] = {
        "conversation_id": conversation_id,
        "thread_key": thread_key,
        "chat_id": chat_id,
        "chat_identifier": chat_identifier,
        "display_name": _clean_str(entry.get("display_name")),
        "chat_type": _clean_str(entry.get("chat_type")).lower(),
        "source_provider": _normalize_source_provider(entry.get("source_provider")),
        "participants": participants,
        "reason": _clean_str(entry.get("reason")),
    }
    return {key: value for key, value in normalized.items() if value not in ("", None) and value != []}


def load_excluded_chats(path: Path | None = None) -> list[dict[str, Any]]:
    resolved = path or resolve_excluded_chats_path()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []

    if isinstance(payload, dict):
        raw_entries = payload.get("excluded_chats")
        if raw_entries is None:
            raw_entries = payload.get("excluded_conversations")
    else:
        raw_entries = payload
    if not isinstance(raw_entries, list):
        return []

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_entry(item)
        if not normalized:
            continue
        identity = exclusion_identity_key(normalized)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        entries.append(normalized)
    return _sorted_entries(entries)


def _sorted_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda entry: (
            _clean_str(entry.get("display_name")).lower(),
            _clean_str(entry.get("thread_key")),
            _clean_str(entry.get("conversation_id")),
            _clean_str(entry.get("chat_id")),
        ),
    )


def write_excluded_chats(entries: list[Mapping[str, Any]], path: Path | None = None) -> Path:
    resolved = path or resolve_excluded_chats_path()
    normalized_entries = []
    for entry in entries:
        normalized = _normalize_entry(entry)
        if normalized:
            normalized_entries.append(normalized)
    payload = {"excluded_chats": _sorted_entries(normalized_entries)}
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return resolved


def build_excluded_chat_entry(
    chat: Mapping[str, Any],
    *,
    gmail_email: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "thread_key": thread_key_for_chat(chat),
        "chat_id": _clean_str(_value(chat, "chat_id") or _value(chat, "imessage_chat_id")),
        "chat_identifier": _clean_str(_value(chat, "chat_identifier") or _value(chat, "imessage_chat_identifier")),
        "display_name": _clean_str(_value(chat, "display_name") or _value(chat, "name")),
        "chat_type": _clean_str(_value(chat, "chat_type")).lower(),
        "source_provider": _conversation_source_provider(chat),
        "participants": _parse_participants(_value(chat, "participants")),
        "reason": _clean_str(reason),
    }
    conversation_id = conversation_id_for_chat(chat, gmail_email)
    if conversation_id:
        entry["conversation_id"] = conversation_id
    return {key: value for key, value in entry.items() if value not in {"", [], None}}


def candidate_keys_for_chat(chat: Mapping[str, Any], gmail_email: str | None = None) -> set[str]:
    keys = {
        _clean_str(_value(chat, "conversation_id")),
        _clean_str(_value(chat, "chat_id") or _value(chat, "imessage_chat_id")),
        _clean_str(_value(chat, "chat_identifier") or _value(chat, "imessage_chat_identifier")),
        thread_key_for_chat(chat),
    }
    derived_conversation_id = conversation_id_for_chat(chat, gmail_email)
    if derived_conversation_id:
        keys.add(derived_conversation_id)
    return {key for key in keys if key}


def find_matching_excluded_chat(
    candidate_keys: set[str],
    exclusions: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    for entry in exclusions or load_excluded_chats():
        entry_keys = {
            _clean_str(entry.get("conversation_id")),
            _clean_str(entry.get("thread_key")),
            _clean_str(entry.get("chat_id")),
            _clean_str(entry.get("chat_identifier")),
        }
        if candidate_keys & {key for key in entry_keys if key}:
            normalized = _normalize_entry(entry)
            if normalized:
                return normalized
    return None


def is_chat_excluded(
    chat: Mapping[str, Any],
    *,
    exclusions: list[Mapping[str, Any]] | None = None,
    gmail_email: str | None = None,
) -> bool:
    return find_matching_excluded_chat(candidate_keys_for_chat(chat, gmail_email), exclusions=exclusions) is not None


def apply_excluded_chats_to_account(
    conn: sqlite3.Connection,
    gmail_email: str,
    *,
    exclusions: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT conversation_id, source_provider, imessage_chat_id, imessage_chat_identifier,
                  display_name, chat_type, participants, exclude_from_sync
           FROM penguin_connect_conversations
           WHERE gmail_email = ?""",
        (gmail_email,),
    ).fetchall()
    if not rows:
        return {"updated": 0, "excluded": 0, "included": 0}

    exclusion_list = list(exclusions) if exclusions is not None else load_excluded_chats()
    updated = 0
    excluded_count = 0
    included_count = 0
    for row in rows:
        target = is_chat_excluded(row, exclusions=exclusion_list, gmail_email=gmail_email)
        current = bool(row["exclude_from_sync"])
        if target:
            excluded_count += 1
        else:
            included_count += 1
        if target == current:
            continue
        conn.execute(
            """UPDATE penguin_connect_conversations
               SET exclude_from_sync = ?, updated_at = datetime('now')
               WHERE conversation_id = ?""",
            (1 if target else 0, row["conversation_id"]),
        )
        updated += 1
    return {"updated": updated, "excluded": excluded_count, "included": included_count}
