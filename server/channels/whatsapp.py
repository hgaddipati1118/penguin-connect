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


def _whatsapp_metadata_db_path() -> Path:
    configured = os.environ.get("PENGUIN_CONNECT_WHATSAPP_METADATA_DB_PATH")
    return Path(configured) if configured else _whatsapp_db_path().with_name("whatsapp.db")


def whatsapp_source_paths() -> tuple[Path, Path]:
    """Return local WhatsApp databases whose changes can affect the workspace."""
    return _whatsapp_db_path(), _whatsapp_metadata_db_path()


def _whatsapp_api_url() -> str:
    return os.environ.get("PENGUIN_CONNECT_WHATSAPP_API_URL", "http://localhost:8080/api")


def _whatsapp_db_available() -> bool:
    return _whatsapp_db_path().exists()


def whatsapp_attachment_count() -> int:
    conn = _open_whatsapp_db()
    if conn is None:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE TRIM(COALESCE(media_type, '')) <> ''"
        ).fetchone()
        return int(row["count"] or 0) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


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


def _open_whatsapp_metadata_db() -> Optional[sqlite3.Connection]:
    db_path = _whatsapp_metadata_db_path()
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _message_columns(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute("PRAGMA table_info(messages)")}
    except sqlite3.Error:
        return set()


def _reply_preview(media_type: str, filename: str) -> str:
    clean_media = str(media_type or "").strip()
    clean_filename = str(filename or "").strip()
    if clean_filename:
        return clean_filename
    if clean_media:
        return f"[{clean_media.capitalize()}]"
    return "Message"


def _whatsapp_identity(conn: Optional[sqlite3.Connection], jid: str) -> dict[str, str]:
    raw_jid = (jid or "").strip()
    fallback = _jid_to_phone(raw_jid)
    if conn is None or not raw_jid or _is_group_jid(raw_jid):
        return {"phone": fallback, "name": ""}
    resolved_phone = fallback
    if raw_jid.endswith("@lid"):
        row = conn.execute(
            "SELECT pn FROM whatsmeow_lid_map WHERE lid = ? LIMIT 1",
            (fallback,),
        ).fetchone()
        if row and row["pn"]:
            resolved_phone = str(row["pn"])
    candidate_jids = [raw_jid]
    phone_jid = f"{resolved_phone}@s.whatsapp.net"
    if phone_jid not in candidate_jids:
        candidate_jids.append(phone_jid)
    placeholders = ",".join("?" for _ in candidate_jids)
    rows = conn.execute(
        f"""SELECT their_jid, first_name, full_name, push_name, business_name
            FROM whatsmeow_contacts
            WHERE their_jid IN ({placeholders})""",
        candidate_jids,
    ).fetchall()
    by_jid = {row["their_jid"]: row for row in rows}
    ordered = [by_jid.get(jid_value) for jid_value in candidate_jids]
    for row in ordered:
        if not row:
            continue
        for key in ("full_name", "first_name", "business_name", "push_name"):
            value = str(row[key] or "").strip()
            if value:
                return {"phone": resolved_phone, "name": value}
    return {"phone": resolved_phone, "name": ""}


def _whatsapp_identity_cache(
    conn: Optional[sqlite3.Connection],
    jids: list[str],
) -> dict[str, dict[str, str]]:
    """Resolve a discovery batch without issuing queries per conversation."""
    raw_jids = [str(jid or "").strip() for jid in jids if str(jid or "").strip()]
    identities = {
        jid: {"phone": _jid_to_phone(jid), "name": ""}
        for jid in raw_jids
    }
    if conn is None:
        return identities

    lid_to_phone: dict[str, str] = {}
    try:
        lid_to_phone = {
            str(row["lid"]): str(row["pn"])
            for row in conn.execute("SELECT lid, pn FROM whatsmeow_lid_map")
            if row["lid"] and row["pn"]
        }
    except sqlite3.Error:
        lid_to_phone = {}

    names_by_jid: dict[str, str] = {}
    try:
        rows = conn.execute(
            """SELECT their_jid, first_name, full_name, push_name, business_name
               FROM whatsmeow_contacts"""
        ).fetchall()
    except sqlite3.Error:
        rows = []
    for row in rows:
        their_jid = str(row["their_jid"] or "").strip()
        if not their_jid or names_by_jid.get(their_jid):
            continue
        for key in ("full_name", "first_name", "business_name", "push_name"):
            value = str(row[key] or "").strip()
            if value:
                names_by_jid[their_jid] = value
                break

    for jid in raw_jids:
        if _is_group_jid(jid):
            continue
        raw_phone = _jid_to_phone(jid)
        phone = lid_to_phone.get(raw_phone, raw_phone) if jid.endswith("@lid") else raw_phone
        phone_jid = f"{phone}@s.whatsapp.net"
        identities[jid] = {
            "phone": phone,
            "name": names_by_jid.get(jid) or names_by_jid.get(phone_jid) or "",
        }
    return identities


def _is_group_jid(jid: str) -> bool:
    return jid.endswith("@g.us")


def _jid_to_phone(jid: str) -> str:
    return jid.split("@")[0] if "@" in jid else jid


class WhatsAppChannelAdapter:
    provider = "whatsapp"
    provider_label = "WhatsApp"

    def list_conversations(self, search: Optional[str] = None, limit: Optional[int] = 100) -> dict[str, Any]:
        conn = _open_whatsapp_db()
        if conn is None:
            return {"available": False, "reason": "WhatsApp messages.db not found"}

        metadata_conn = _open_whatsapp_metadata_db()
        try:
            safe_limit = 100000 if limit is None else max(1, min(int(limit or 100), 100000))
            params: list[Any] = []
            where_clause = ""
            if search:
                where_clause = "WHERE (LOWER(c.name) LIKE LOWER(?) OR LOWER(c.jid) LIKE LOWER(?))"
                pattern = f"%{search}%"
                params.extend([pattern, pattern])

            rows = conn.execute(
                f"""
                WITH message_stats AS (
                    SELECT chat_jid, COUNT(*) AS msg_count
                    FROM messages
                    GROUP BY chat_jid
                ),
                ranked_messages AS (
                    SELECT
                        chat_jid,
                        id,
                        content,
                        timestamp,
                        is_from_me,
                        sender,
                        media_type,
                        filename,
                        ROW_NUMBER() OVER (
                            PARTITION BY chat_jid
                            ORDER BY timestamp DESC, id DESC
                        ) AS row_number
                    FROM messages
                )
                SELECT
                    c.jid,
                    c.name,
                    c.last_message_time,
                    stats.msg_count,
                    latest.id AS last_message_id,
                    latest.content AS last_message_preview,
                    latest.timestamp AS latest_message_timestamp,
                    latest.is_from_me AS latest_is_from_me,
                    latest.sender AS latest_sender,
                    latest.media_type AS latest_media_type,
                    latest.filename AS latest_filename
                FROM chats c
                JOIN message_stats stats ON stats.chat_jid = c.jid
                LEFT JOIN ranked_messages latest
                  ON latest.chat_jid = c.jid
                 AND latest.row_number = 1
                {where_clause}
                ORDER BY c.last_message_time DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()

            selected_jids = [str(row["jid"] or "").strip() for row in rows if row["jid"]]
            latest_senders = [
                str(row["latest_sender"] or "").strip()
                for row in rows
                if str(row["latest_sender"] or "").strip()
            ]
            identities = _whatsapp_identity_cache(metadata_conn, [*selected_jids, *latest_senders])
            participants_by_chat: dict[str, list[str]] = {}
            for chunk_start in range(0, len(selected_jids), 800):
                jid_chunk = selected_jids[chunk_start:chunk_start + 800]
                placeholders = ",".join("?" for _ in jid_chunk)
                participant_rows = conn.execute(
                    f"""SELECT chat_jid, sender
                        FROM messages
                        WHERE chat_jid IN ({placeholders})
                          AND is_from_me = 0
                          AND TRIM(COALESCE(sender, '')) <> ''
                        GROUP BY chat_jid, sender
                        ORDER BY chat_jid, sender""",
                    jid_chunk,
                ).fetchall()
                for participant_row in participant_rows:
                    participants_by_chat.setdefault(participant_row["chat_jid"], []).append(
                        participant_row["sender"]
                    )

            chats = []
            for row in rows:
                jid = row["jid"]
                is_group = _is_group_jid(jid)
                identity = identities.get(jid) or {"phone": _jid_to_phone(jid), "name": ""}
                source_name = (row["name"] or "").strip()
                name = source_name or identity["name"] or identity["phone"]

                participants = participants_by_chat.get(jid, []) if is_group else [identity["phone"]]
                latest_sender = str(row["latest_sender"] or "").strip()
                latest_sender_identity = identities.get(latest_sender) or {
                    "phone": _jid_to_phone(latest_sender),
                    "name": "",
                }
                latest_filename = str(row["latest_filename"] or "").strip()
                latest_media_type = str(row["latest_media_type"] or "").strip()
                latest_attachments = None
                if latest_media_type:
                    latest_attachments = [
                        {
                            "filename": latest_filename,
                            "mime_type": latest_media_type,
                            "size": 0,
                            "transfer_name": Path(latest_filename).name
                            if latest_filename
                            else f"whatsapp-{row['last_message_id']}",
                        }
                    ]

                chats.append(
                    {
                        "chat_id": jid,
                        "chat_guid": jid,
                        "chat_identifier": jid,
                        "name": name,
                        "source_display_name": source_name or identity["name"],
                        "room_name": "",
                        "chat_type": "group" if is_group else "dm",
                        "participants": participants,
                        "message_count": row["msg_count"],
                        "last_message_at": row["last_message_time"],
                        "last_message_preview": (row["last_message_preview"] or "")[:120],
                        "latest_message": {
                            "text": row["last_message_preview"] or "",
                            "timestamp": row["latest_message_timestamp"] or row["last_message_time"],
                            "is_from_me": bool(row["latest_is_from_me"]),
                            "service": "WhatsApp",
                            "handle": latest_sender,
                            "push_name": latest_sender_identity["name"]
                            or latest_sender_identity["phone"],
                            "attachments": latest_attachments,
                            "native_message_id": str(row["last_message_id"] or ""),
                        },
                        "service": "WhatsApp",
                        "source_provider": "whatsapp",
                    }
                )

            return {"available": True, "chats": chats}
        except Exception as exc:
            return {"available": False, "reason": str(exc)}
        finally:
            conn.close()
            if metadata_conn is not None:
                metadata_conn.close()

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

    def list_attachment_messages(self, limit: int = 1000, offset: int = 0) -> dict[str, Any]:
        conn = _open_whatsapp_db()
        if conn is None:
            return {"available": False, "reason": "WhatsApp messages.db not found", "messages": [], "total": 0}
        metadata_conn = _open_whatsapp_metadata_db()
        try:
            safe_limit = max(1, min(int(limit or 1000), 5000))
            safe_offset = max(0, int(offset or 0))
            total_row = conn.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE TRIM(COALESCE(media_type, '')) <> ''"
            ).fetchone()
            rows = conn.execute(
                """SELECT m.id, m.chat_jid, m.content, m.timestamp, m.is_from_me,
                          m.sender, m.media_type, m.filename, c.name AS chat_name
                   FROM messages m
                   LEFT JOIN chats c ON c.jid = m.chat_jid
                   WHERE TRIM(COALESCE(m.media_type, '')) <> ''
                   ORDER BY m.timestamp DESC, m.id DESC
                   LIMIT ? OFFSET ?""",
                (safe_limit, safe_offset),
            ).fetchall()
            messages = []
            for row in rows:
                sender_jid = row["sender"] or ""
                identity = _whatsapp_identity(metadata_conn, sender_jid)
                filename = str(row["filename"] or "").strip()
                messages.append({
                    "chat_id": row["chat_jid"],
                    "chat_identifier": row["chat_jid"],
                    "chat_name": row["chat_name"] or row["chat_jid"],
                    "source_provider": "whatsapp",
                    "text": row["content"] or "",
                    "timestamp": row["timestamp"],
                    "is_from_me": bool(row["is_from_me"]),
                    "service": "WhatsApp",
                    "handle": sender_jid,
                    "push_name": identity["name"] or _jid_to_phone(sender_jid),
                    "attachments": [{
                        "filename": filename,
                        "mime_type": row["media_type"] or "",
                        "size": 0,
                        "transfer_name": Path(filename).name if filename else f"whatsapp-{row['id']}",
                        "whatsapp_chat_jid": row["chat_jid"],
                        "whatsapp_message_id": str(row["id"]),
                    }],
                    "native_message_id": str(row["id"]),
                })
            return {
                "available": True,
                "messages": messages,
                "total": int(total_row["count"] or 0) if total_row else 0,
                "limit": safe_limit,
                "offset": safe_offset,
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc), "messages": [], "total": 0}
        finally:
            conn.close()
            if metadata_conn is not None:
                metadata_conn.close()

    def fetch_messages(
        self,
        chat_id: str,
        limit: int = 50,
        since: Optional[str] = None,
        since_native_message_id: Optional[str] = None,
        before: Optional[str] = None,
        before_native_message_id: Optional[str] = None,
        download_media: bool = False,
    ) -> list[dict[str, Any]]:
        conn = _open_whatsapp_db()
        if conn is None:
            return []

        metadata_conn = _open_whatsapp_metadata_db()
        try:
            safe_limit = max(1, min(int(limit or 50), 1000))
            columns = _message_columns(conn)
            has_reply_context = {
                "reply_to_message_id",
                "reply_to_sender",
                "reply_to_text",
            }.issubset(columns)
            reply_select = (
                """,
                    m.reply_to_message_id,
                    m.reply_to_sender,
                    m.reply_to_text"""
                if has_reply_context
                else ""
            )
            params: list[Any] = [chat_id]
            date_filter = ""
            if since and since_native_message_id:
                date_filter = "AND (m.timestamp > ? OR (m.timestamp = ? AND m.id > ?))"
                params.extend([since, since, since_native_message_id])
            elif since:
                date_filter = "AND m.timestamp > ?"
                params.append(since)
            elif before and before_native_message_id:
                date_filter = "AND (m.timestamp < ? OR (m.timestamp = ? AND m.id < ?))"
                params.extend([before, before, before_native_message_id])
            elif before:
                date_filter = "AND m.timestamp < ?"
                params.append(before)
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
                    {reply_select}
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
                    local_path = self._download_media(chat_id, row["id"]) if download_media else None
                    attachments = [
                        {
                            "filename": local_path or filename,
                            "mime_type": media_type,
                            "size": 0,
                            "transfer_name": filename,
                        }
                    ]

                if not text and not attachments:
                    continue

                sender_jid = row["sender"] or ""
                identity = _whatsapp_identity(metadata_conn, sender_jid)
                chat_name = identity["name"] or None
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
                        **(
                            {
                                "reply_to_message_id": row["reply_to_message_id"] or "",
                                "reply_to_sender": row["reply_to_sender"] or "",
                                "reply_to_text": row["reply_to_text"] or "",
                            }
                            if has_reply_context and row["reply_to_message_id"]
                            else {}
                        ),
                    }
                )
            return messages
        except Exception:
            return []
        finally:
            conn.close()
            if metadata_conn is not None:
                metadata_conn.close()

    def _download_media(self, chat_jid: str, message_id: str) -> Optional[str]:
        """Download media via the whatsapp-mcp bridge API, return local file path."""
        try:
            resp = httpx.post(
                f"{_whatsapp_api_url()}/download",
                json={"chat_jid": chat_jid, "message_id": str(message_id)},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("path"):
                    path = Path(data["path"])
                    if path.exists():
                        return str(path)
        except Exception:
            pass
        return None

    def download_attachment(self, chat_jid: str, message_id: str) -> Optional[str]:
        return self._download_media(chat_jid, message_id)

    def send_message(
        self,
        chat_identifier: str,
        message_text: str,
        attachment_paths: Optional[list[str]] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        normalized_text = (message_text or "").strip()
        normalized_reply_id = str(reply_to_message_id or "").strip()
        valid_attachments: list[str] = []
        for path in attachment_paths or []:
            candidate = Path(path).expanduser()
            if candidate.exists() and candidate.is_file():
                valid_attachments.append(str(candidate))

        if not normalized_text and not valid_attachments:
            return False, "empty_message"

        try:
            reply_payload: dict[str, str] = {}
            if normalized_reply_id:
                capability_response = httpx.get(
                    f"{_whatsapp_api_url()}/capabilities",
                    timeout=5,
                )
                if (
                    capability_response.status_code != 200
                    or not capability_response.json().get("native_replies")
                ):
                    return False, "whatsapp_native_replies_unavailable"
                conn = _open_whatsapp_db()
                if conn is None:
                    return False, "whatsapp_reply_target_unavailable"
                try:
                    columns = _message_columns(conn)
                    media_select = (
                        "media_type, filename"
                        if {"media_type", "filename"}.issubset(columns)
                        else "'' AS media_type, '' AS filename"
                    )
                    target = conn.execute(
                        f"""SELECT sender, content, {media_select}
                            FROM messages
                            WHERE chat_jid = ? AND id = ?
                            LIMIT 1""",
                        (chat_identifier, normalized_reply_id),
                    ).fetchone()
                finally:
                    conn.close()
                if target is None:
                    return False, "whatsapp_reply_target_not_found"
                reply_payload = {
                    "reply_to_message_id": normalized_reply_id,
                    "reply_to_sender": str(target["sender"] or ""),
                    "reply_to_text": str(target["content"] or "")
                    or _reply_preview(target["media_type"], target["filename"]),
                }

            reply_on_attachment = bool(normalized_reply_id and not normalized_text)
            for index, attachment_path in enumerate(valid_attachments):
                payload = {"recipient": chat_identifier, "media_path": attachment_path}
                if reply_on_attachment and index == 0:
                    payload.update(reply_payload)
                resp = httpx.post(
                    f"{_whatsapp_api_url()}/send",
                    json=payload,
                    timeout=30,
                )
                if resp.status_code != 200 or not resp.json().get("success"):
                    return False, f"whatsapp_attachment_send_failed: {resp.text}"

            if normalized_text:
                payload = {"recipient": chat_identifier, "message": normalized_text}
                payload.update(reply_payload)
                resp = httpx.post(
                    f"{_whatsapp_api_url()}/send",
                    json=payload,
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
