"""iMessage adapter for PenguinConnect's source-channel interface."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

import browse_sources
from browse_sources import (
    browse_imessage_chats,
    fetch_imessage_messages,
    list_recent_imessage_chat_activity,
    resolve_apple_messages_chat,
)

from .base import LookupContactName, LooksLikeUnresolvedHandle

def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class IMessageChannelAdapter:
    provider = "imessage"
    provider_label = "iMessage"

    def list_conversations(self, search: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
        return browse_imessage_chats(search=search, limit=limit)

    def list_recent_activity(self, since: str, limit: int = 500) -> dict[str, Any]:
        return list_recent_imessage_chat_activity(since, limit=limit)

    def fetch_messages(
        self,
        chat_id: str,
        limit: int = 50,
        since: Optional[str] = None,
        since_native_message_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return fetch_imessage_messages(
            chat_id,
            limit=limit,
            since=since,
            since_native_message_id=since_native_message_id,
        )

    def send_message(
        self,
        chat_identifier: str,
        message_text: str,
        attachment_paths: Optional[list[str]] = None,
    ) -> tuple[bool, Optional[str]]:
        normalized_text = (message_text or "").strip()
        valid_attachments: list[str] = []
        for path in attachment_paths or []:
            candidate = Path(path).expanduser()
            if candidate.exists() and candidate.is_file():
                valid_attachments.append(str(candidate))

        if not normalized_text and not valid_attachments:
            return False, "empty_message"

        route = resolve_apple_messages_chat(chat_identifier)
        if route and route.get("ambiguous"):
            return False, "unsafe_chat_route_ambiguous"
        guid = (route or {}).get("guid")
        if not guid:
            return False, "unsafe_chat_route_unresolved"

        safe_guid = _escape_applescript(guid)
        script_lines = [
            'tell application "Messages"',
            f'    set targetChat to chat id "{safe_guid}"',
        ]
        for path in valid_attachments:
            safe_path = _escape_applescript(path)
            script_lines.append(f'    send (POSIX file "{safe_path}") to targetChat')
        if normalized_text:
            safe_msg = _escape_applescript(normalized_text)
            script_lines.append(f'    send "{safe_msg}" to targetChat')
        script_lines.append("end tell")
        script = "\n".join(script_lines)

        try:
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=45)
            if result.returncode != 0:
                return False, (result.stderr or "failed_to_send_imessage").strip()
            return True, None
        except Exception as exc:
            return False, str(exc)

    def get_unread_count(self, chat_identifier: str) -> Optional[int]:
        route = resolve_apple_messages_chat(chat_identifier)
        if not route or route.get("ambiguous"):
            return None
        db_path = Path(browse_sources.IMESSAGE_DB)
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute("SELECT unread_count FROM chat WHERE guid = ? LIMIT 1", (route["guid"],)).fetchone()
            conn.close()
            if row is not None and row[0] is not None:
                return int(row[0])
        except Exception:
            return None
        return None

    def resolve_sender_and_subject(
        self,
        conn: sqlite3.Connection,
        conv: sqlite3.Row,
        msg: dict[str, Any],
        *,
        lookup_contact_name: LookupContactName,
        looks_like_unresolved_handle: LooksLikeUnresolvedHandle,
    ) -> tuple[str, str]:
        handle = (msg.get("handle") or "").strip()
        contact_name = lookup_contact_name(conn, handle)
        if msg.get("is_from_me"):
            sender_name = "Me"
        else:
            push_name = (msg.get("push_name") or "").strip()
            if push_name and looks_like_unresolved_handle(push_name):
                push_name = ""
            sender_name = contact_name or push_name or handle or conv["display_name"] or "iMessage"

        display_name = (conv["display_name"] or "").strip()
        if display_name and not looks_like_unresolved_handle(display_name):
            subject_name = display_name
        elif (conv["chat_type"] or "").strip().lower() == "dm":
            subject_name = contact_name or display_name or handle or "Conversation"
        else:
            subject_name = display_name or "Conversation"

        return sender_name, subject_name
