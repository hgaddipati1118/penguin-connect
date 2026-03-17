"""WhatsApp adapter for PenguinConnect's source-channel interface."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from .base import LookupContactName, LooksLikeUnresolvedHandle

_DEFAULT_WHATSAPP_DB = str(Path.home() / "whatsapp-mcp" / "whatsapp-bridge" / "store" / "messages.db")


def _whatsapp_db_path() -> Path:
    return Path(os.environ.get("PENGUIN_CONNECT_WHATSAPP_DB_PATH", _DEFAULT_WHATSAPP_DB))


def _whatsapp_api_url() -> str:
    return os.environ.get("PENGUIN_CONNECT__whatsapp_api_url()", "http://localhost:8080/api")


def _whatsapp_db_available() -> bool:
    return _whatsapp_db_path().exists()


def _open_whatsapp_db() -> Optional[sqlite3.Connection]:
    db_path = _whatsapp_db_path()
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _is_group_jid(jid: str) -> bool:
    return jid.endswith("@g.us")


def _jid_to_phone(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


class WhatsAppChannelAdapter:
    provider = "whatsapp"
    provider_label = "WhatsApp"

    def list_conversations(self, search: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
        conn = _open_whatsapp_db()
        if conn is None:
            return {"available": False, "reason": "WhatsApp messages.db not found"}

        try:
            safe_limit = max(1, min(int(limit or 100), 100000))
            params: list[Any] = []
            where_clause = ""
            if search:
                where_clause = "WHERE (LOWER(c.name) LIKE LOWER(?) OR LOWER(c.jid) LIKE LOWER(?))"
                pattern = f"%{search}%"
                params.extend([pattern, pattern])

            rows = conn.execute(
                f"""
                SELECT
                    c.jid,
                    c.name,
                    c.last_message_time,
                    COUNT(DISTINCT m.id) AS msg_count,
                    (SELECT m2.content FROM messages m2
                     WHERE m2.chat_jid = c.jid
                     ORDER BY m2.timestamp DESC LIMIT 1) AS last_message_preview
                FROM chats c
                LEFT JOIN messages m ON m.chat_jid = c.jid
                {where_clause}
                GROUP BY c.jid
                HAVING msg_count > 0
                ORDER BY c.last_message_time DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()

            chats = []
            for row in rows:
                jid = row["jid"]
                is_group = _is_group_jid(jid)
                name = row["name"] or _jid_to_phone(jid)

                participants: list[str] = []
                if is_group:
                    senders = conn.execute(
                        "SELECT DISTINCT sender FROM messages WHERE chat_jid = ? AND is_from_me = 0",
                        (jid,),
                    ).fetchall()
                    participants = [s["sender"] for s in senders if s["sender"]]
                else:
                    participants = [_jid_to_phone(jid)]

                chats.append(
                    {
                        "chat_id": jid,
                        "chat_guid": jid,
                        "chat_identifier": jid,
                        "name": name,
                        "source_display_name": (row["name"] or "").strip(),
                        "room_name": "",
                        "chat_type": "group" if is_group else "dm",
                        "participants": participants,
                        "message_count": row["msg_count"],
                        "last_message_at": row["last_message_time"],
                        "last_message_preview": (row["last_message_preview"] or "")[:120],
                        "service": "WhatsApp",
                        "source_provider": "whatsapp",
                    }
                )

            return {"available": True, "chats": chats}
        except Exception as exc:
            return {"available": False, "reason": str(exc)}
        finally:
            conn.close()

    def list_recent_activity(self, since: str, limit: int = 500) -> dict[str, Any]:
        conn = _open_whatsapp_db()
        if conn is None:
            return {"available": False, "reason": "WhatsApp messages.db not found"}

        try:
            safe_limit = max(1, min(int(limit or 500), 5000))
            rows = conn.execute(
                """
                SELECT
                    m.chat_jid,
                    MIN(m.timestamp) AS first_message_at,
                    MAX(m.timestamp) AS last_message_at,
                    COUNT(DISTINCT m.id) AS msg_count
                FROM messages m
                WHERE m.timestamp > ?
                GROUP BY m.chat_jid
                ORDER BY first_message_at ASC
                LIMIT ?
                """,
                (since, safe_limit),
            ).fetchall()

            chats = []
            for row in rows:
                jid = row["chat_jid"]
                chats.append(
                    {
                        "chat_id": jid,
                        "chat_guid": jid,
                        "chat_identifier": jid,
                        "service": "WhatsApp",
                        "source_provider": "whatsapp",
                        "first_message_at": row["first_message_at"],
                        "last_message_at": row["last_message_at"],
                        "message_count": row["msg_count"] or 0,
                    }
                )

            return {"available": True, "chats": chats}
        except Exception as exc:
            return {"available": False, "reason": str(exc)}
        finally:
            conn.close()

    def fetch_messages(
        self,
        chat_id: str,
        limit: int = 50,
        since: Optional[str] = None,
        since_native_message_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        conn = _open_whatsapp_db()
        if conn is None:
            return []

        try:
            safe_limit = max(1, min(int(limit or 50), 1000))
            params: list[Any] = [chat_id]
            date_filter = ""
            if since:
                date_filter = "AND m.timestamp > ?"
                params.append(since)
            order_direction = "ASC" if since else "DESC"
            params.append(safe_limit)

            rows = conn.execute(
                f"""
                SELECT
                    m.id,
                    m.content,
                    m.timestamp,
                    m.is_from_me,
                    m.sender,
                    m.media_type,
                    m.filename
                FROM messages m
                WHERE m.chat_jid = ?
                  {date_filter}
                  AND (m.content IS NOT NULL AND m.content != '' OR m.media_type IS NOT NULL)
                ORDER BY m.timestamp {order_direction}, m.id {order_direction}
                LIMIT ?
                """,
                params,
            ).fetchall()

            messages = []
            for row in rows:
                text = row["content"] or ""
                media_type = row["media_type"] or ""
                filename = row["filename"] or ""

                attachments = None
                if media_type:
                    attachments = [
                        {
                            "filename": filename,
                            "mime_type": media_type,
                            "size": 0,
                            "transfer_name": filename,
                        }
                    ]

                if not text and not attachments:
                    continue

                sender_jid = row["sender"] or ""
                chat_name = None
                if sender_jid:
                    name_row = conn.execute(
                        "SELECT name FROM chats WHERE jid = ? LIMIT 1", (sender_jid,)
                    ).fetchone()
                    if name_row:
                        chat_name = name_row["name"]

                messages.append(
                    {
                        "text": text,
                        "timestamp": row["timestamp"],
                        "is_from_me": bool(row["is_from_me"]),
                        "service": "WhatsApp",
                        "handle": sender_jid,
                        "push_name": chat_name or _jid_to_phone(sender_jid),
                        "attachments": attachments,
                        "native_message_id": row["id"],
                    }
                )
            return messages
        except Exception:
            return []
        finally:
            conn.close()

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

        try:
            for attachment_path in valid_attachments:
                resp = httpx.post(
                    f"{_whatsapp_api_url()}/send",
                    json={"recipient": chat_identifier, "media_path": attachment_path},
                    timeout=30,
                )
                if resp.status_code != 200 or not resp.json().get("success"):
                    return False, f"whatsapp_attachment_send_failed: {resp.text}"

            if normalized_text:
                resp = httpx.post(
                    f"{_whatsapp_api_url()}/send",
                    json={"recipient": chat_identifier, "message": normalized_text},
                    timeout=30,
                )
                if resp.status_code != 200 or not resp.json().get("success"):
                    return False, f"whatsapp_send_failed: {resp.text}"

            return True, None
        except Exception as exc:
            return False, str(exc)

    def get_unread_count(self, chat_identifier: str) -> Optional[int]:
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
        push_name = (msg.get("push_name") or "").strip()

        if msg.get("is_from_me"):
            sender_name = "Me"
        else:
            contact_name = lookup_contact_name(conn, _jid_to_phone(handle))
            sender_name = contact_name or push_name or _jid_to_phone(handle) or "WhatsApp"

        display_name = (conv["display_name"] or "").strip()
        if display_name and not looks_like_unresolved_handle(display_name):
            subject_name = display_name
        elif (conv["chat_type"] or "").strip().lower() == "dm":
            contact_name = lookup_contact_name(conn, _jid_to_phone(handle))
            subject_name = contact_name or push_name or display_name or _jid_to_phone(handle) or "Conversation"
        else:
            subject_name = display_name or "Conversation"

        return sender_name, subject_name
