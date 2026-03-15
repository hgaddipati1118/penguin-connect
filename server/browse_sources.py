"""Minimal iMessage browse/fetch helpers for PenguinConnect bridge."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

IMESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
APPLE_MESSAGES_SERVICES = ("iMessage", "SMS", "RCS")


def _apple_ts_to_iso(ts):
    if not ts:
        return None
    try:
        return (APPLE_EPOCH + timedelta(seconds=ts / 1_000_000_000)).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _extract_text_from_attributed_body(blob):
    if not blob:
        return None
    try:
        marker = b"NSString\x01\x94\x84\x01+"
        idx = blob.find(marker)
        if idx == -1:
            return None
        pos = idx + len(marker)
        first = blob[pos]
        if first < 0x80:
            length = first
            pos += 1
        elif first == 0x81:
            length = blob[pos + 1]
            pos += 3
        elif first == 0x82:
            length = (blob[pos + 1] << 8) | blob[pos + 2]
            pos += 4
        else:
            return None
        text = blob[pos : pos + length].decode("utf-8", errors="replace")
        return text if text else None
    except Exception:
        return None


def _service_to_provider(service_name):
    normalized = (service_name or "").strip().lower()
    if normalized in {"imessage", "sms", "rcs"}:
        return normalized
    return "imessage"


def _service_rank(service_name):
    normalized = (service_name or "").strip().lower()
    if normalized == "imessage":
        return 0
    if normalized == "rcs":
        return 1
    if normalized == "sms":
        return 2
    return 3


def _looks_like_chat_guid(chat_key):
    value = (chat_key or "").strip()
    parts = value.split(";")
    return len(parts) == 3 and all(parts)


def _preferred_source_chat_title(display_name, room_name, chat_identifier="", chat_guid=""):
    banned = {
        (chat_identifier or "").strip(),
        (chat_guid or "").strip(),
    }
    for candidate in (display_name, room_name):
        title = (candidate or "").strip()
        if title and title not in banned:
            return title
    return ""


def _chat_room_name_expr(conn):
    try:
        columns = conn.execute("PRAGMA table_info(chat)").fetchall()
    except Exception:
        columns = []
    has_room_name = any((col[1] or "").strip().lower() == "room_name" for col in columns if len(col) > 1)
    return "c.room_name" if has_room_name else "''"


def _allowed_service_rows(cur, match_column, match_value, allowed_services=None):
    services = tuple(allowed_services or APPLE_MESSAGES_SERVICES)
    placeholders = ",".join("?" for _ in services)
    room_name_expr = _chat_room_name_expr(cur.connection)
    rows = cur.execute(
        f"""
        SELECT
            c.ROWID,
            c.guid,
            c.chat_identifier,
            c.display_name,
            {room_name_expr},
            c.service_name,
            c.is_archived,
            MAX(m.date) AS last_msg_date
        FROM chat c
        LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        LEFT JOIN message m ON m.ROWID = cmj.message_id
        WHERE c.{match_column} = ?
          AND c.service_name IN ({placeholders})
        GROUP BY c.ROWID
        """,
        (match_value, *services),
    ).fetchall()
    rows = sorted(
        rows,
        key=lambda row: (
            _service_rank(row[5]),
            int(row[6] or 0),
            -(int(row[7] or 0)),
            -(int(row[0] or 0)),
        ),
    )
    return rows


def resolve_apple_messages_chat(chat_key, allowed_services=None):
    if not chat_key or not os.path.exists(IMESSAGE_DB):
        return None

    conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        rows = []
        matched_by_guid = False
        if _looks_like_chat_guid(chat_key):
            rows = _allowed_service_rows(cur, "guid", chat_key, allowed_services=allowed_services)
            matched_by_guid = bool(rows)
        if not rows:
            rows = _allowed_service_rows(cur, "chat_identifier", chat_key, allowed_services=allowed_services)
        if not rows:
            return None
        rowid, guid, chat_identifier, display_name, room_name, service_name, is_archived, last_msg_date = rows[0]
        return {
            "rowid": rowid,
            "guid": guid,
            "chat_identifier": chat_identifier,
            "display_name": display_name,
            "room_name": room_name,
            "service_name": service_name,
            "is_archived": bool(is_archived),
            "last_message_at": _apple_ts_to_iso(last_msg_date),
            "source_provider": _service_to_provider(service_name),
            "ambiguous": not matched_by_guid and len(rows) > 1,
        }
    finally:
        conn.close()


def list_apple_messages_chat_routes(chat_key, allowed_services=None):
    if not chat_key or not os.path.exists(IMESSAGE_DB):
        return []

    conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        routes = []
        lookup_key = chat_key
        if _looks_like_chat_guid(chat_key):
            exact_rows = _allowed_service_rows(cur, "guid", chat_key, allowed_services=allowed_services)
            if exact_rows:
                lookup_key = exact_rows[0][2] or chat_key
                routes.extend(exact_rows)

        identifier_rows = _allowed_service_rows(cur, "chat_identifier", lookup_key, allowed_services=allowed_services)
        if identifier_rows:
            routes = identifier_rows
        if not routes:
            return []

        seen_guids = set()
        out = []
        for rowid, guid, chat_identifier, display_name, room_name, service_name, is_archived, last_msg_date in routes:
            if not guid or guid in seen_guids:
                continue
            seen_guids.add(guid)
            out.append(
                {
                    "rowid": rowid,
                    "guid": guid,
                    "chat_id": guid,
                    "chat_identifier": chat_identifier,
                    "display_name": display_name,
                    "room_name": room_name,
                    "service_name": service_name,
                    "is_archived": bool(is_archived),
                    "last_message_at": _apple_ts_to_iso(last_msg_date),
                    "source_provider": _service_to_provider(service_name),
                }
            )
        return out
    finally:
        conn.close()


def browse_imessage_chats(search=None, limit=100):
    if not os.path.exists(IMESSAGE_DB):
        return {"available": False, "reason": "chat.db not found"}

    conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        params = []
        limit_clause = ""
        room_name_expr = _chat_room_name_expr(conn)
        if limit is not None:
            safe_limit = max(1, min(int(limit or 100), 100000))
            limit_clause = "LIMIT ?"
            params.append(safe_limit)
        cur.execute(
            f"""
            SELECT
                c.ROWID,
                c.guid,
                c.chat_identifier,
                c.display_name,
                {room_name_expr},
                c.service_name,
                COUNT(DISTINCT cmj.message_id) as msg_count,
                MAX(m.date) as last_msg_date
            FROM chat c
            LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            LEFT JOIN message m ON m.ROWID = cmj.message_id
            WHERE c.guid IS NOT NULL
              AND c.service_name IN ('iMessage', 'SMS', 'RCS')
            GROUP BY c.ROWID
            HAVING msg_count > 0
            ORDER BY last_msg_date DESC
            {limit_clause}
            """,
            params,
        )
        raw_chats = cur.fetchall()

        chats = []
        for row in raw_chats:
            chat_rowid, chat_guid, chat_identifier, display_name, room_name, service, msg_count, last_date = row

            cur.execute(
                """
                SELECT h.id FROM handle h
                JOIN chat_handle_join chj ON chj.handle_id = h.ROWID
                WHERE chj.chat_id = ?
                """,
                (chat_rowid,),
            )
            participants = [p[0] for p in cur.fetchall() if p and p[0]]

            is_group = len(participants) > 1
            chat_type = "group" if is_group else "dm"
            name = _preferred_source_chat_title(display_name, room_name, chat_identifier, chat_guid)
            if not name and participants:
                name = ", ".join(participants[:3])
                if len(participants) > 3:
                    name += f" +{len(participants) - 3}"
            if not name:
                name = chat_identifier or chat_guid or "Apple Messages Conversation"

            if search:
                s = search.lower()
                searchable = f"{name} {chat_identifier} {chat_guid} {' '.join(participants)}".lower()
                if s not in searchable:
                    continue

            cur.execute(
                """
                SELECT m.text, m.attributedBody
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE cmj.chat_id = ?
                  AND ((m.text IS NOT NULL AND m.text != '') OR m.attributedBody IS NOT NULL)
                ORDER BY m.date DESC
                LIMIT 1
                """,
                (chat_rowid,),
            )
            last_msg_row = cur.fetchone()
            last_msg = ""
            if last_msg_row:
                last_msg = last_msg_row[0] or ""
                if not last_msg and last_msg_row[1]:
                    last_msg = _extract_text_from_attributed_body(last_msg_row[1]) or ""
                last_msg = last_msg[:120]

            chats.append(
                {
                    "chat_id": chat_guid,
                    "chat_guid": chat_guid,
                    "chat_identifier": chat_identifier,
                    "name": name,
                    "source_display_name": (display_name or "").strip(),
                    "room_name": (room_name or "").strip(),
                    "chat_type": chat_type,
                    "participants": participants,
                    "message_count": msg_count,
                    "last_message_at": _apple_ts_to_iso(last_date),
                    "last_message_preview": last_msg,
                    "service": service or "iMessage",
                    "source_provider": _service_to_provider(service),
                }
            )

        return {"available": True, "chats": chats}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    finally:
        conn.close()


def list_recent_imessage_chat_activity(since, limit=500):
    if not os.path.exists(IMESSAGE_DB):
        return {"available": False, "reason": "chat.db not found"}

    since_ns = _iso_to_apple_ns(since)
    if since_ns is None:
        return {"available": False, "reason": "invalid_since"}

    safe_limit = max(1, min(int(limit or 500), 5000))
    conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.guid,
                c.chat_identifier,
                c.service_name,
                MIN(m.date) AS first_msg_date,
                MAX(m.date) AS last_msg_date,
                COUNT(DISTINCT m.ROWID) AS msg_count
            FROM chat c
            JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            JOIN message m ON m.ROWID = cmj.message_id
            LEFT JOIN message_attachment_join maj ON maj.message_id = m.ROWID
            WHERE c.guid IS NOT NULL
              AND c.chat_identifier IS NOT NULL
              AND c.service_name IN ('iMessage', 'SMS', 'RCS')
              AND m.date > ?
              AND ((m.text IS NOT NULL AND m.text != '')
                   OR m.attributedBody IS NOT NULL
                   OR maj.attachment_id IS NOT NULL)
            GROUP BY c.ROWID, c.guid, c.chat_identifier, c.service_name
            ORDER BY first_msg_date ASC, c.ROWID ASC
            LIMIT ?
            """,
            (since_ns, safe_limit),
        )

        chats = []
        for chat_guid, chat_identifier, service_name, first_date, last_date, msg_count in cur.fetchall():
            if not chat_guid:
                continue
            chats.append(
                {
                    "chat_id": chat_guid,
                    "chat_guid": chat_guid,
                    "chat_identifier": chat_identifier,
                    "service": service_name or "iMessage",
                    "source_provider": _service_to_provider(service_name),
                    "first_message_at": _apple_ts_to_iso(first_date),
                    "last_message_at": _apple_ts_to_iso(last_date),
                    "message_count": msg_count or 0,
                }
            )

        return {"available": True, "chats": chats}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    finally:
        conn.close()


def _resolve_imessage_rowid(cur, chat_identifier):
    if _looks_like_chat_guid(chat_identifier):
        row = _allowed_service_rows(cur, "guid", chat_identifier, allowed_services=APPLE_MESSAGES_SERVICES)
        if row:
            return row[0][0]
    row = _allowed_service_rows(cur, "chat_identifier", chat_identifier, allowed_services=APPLE_MESSAGES_SERVICES)
    if len(row) == 1:
        return row[0][0]
    if len(row) > 1:
        return None
    try:
        rowid = int(chat_identifier)
        row = cur.execute("SELECT ROWID FROM chat WHERE ROWID = ?", (rowid,)).fetchone()
        return row[0] if row else None
    except (ValueError, TypeError):
        return None


def _iso_to_apple_ns(iso_ts):
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return int((dt - APPLE_EPOCH).total_seconds() * 1_000_000_000)
    except Exception:
        return None


def _native_message_rowid(value):
    raw = (value or "").strip()
    if not raw:
        return None
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1].strip()
    try:
        rowid = int(raw)
    except Exception:
        return None
    return rowid if rowid > 0 else None


def fetch_imessage_messages(chat_id, limit=50, since=None, since_native_message_id=None):
    if not os.path.exists(IMESSAGE_DB):
        return []

    conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        chat_rowid = _resolve_imessage_rowid(cur, chat_id)
        if not chat_rowid:
            return []

        since_ns = _iso_to_apple_ns(since)
        since_rowid = _native_message_rowid(since_native_message_id)
        if since_ns and since_rowid:
            date_filter = "AND (m.date > ? OR (m.date = ? AND m.ROWID > ?))"
        elif since_ns:
            date_filter = "AND m.date > ?"
        else:
            date_filter = ""
        limit_clause = "LIMIT ?"
        order_direction = "ASC" if since_ns else "DESC"

        safe_limit = max(1, min(int(limit or 50), 1000))
        params = [chat_rowid]
        if since_ns:
            if since_rowid:
                params.extend([since_ns, since_ns, since_rowid])
            else:
                params.append(since_ns)
        params.append(safe_limit)

        cur.execute(
            f"""
            SELECT DISTINCT
                m.ROWID,
                m.text,
                m.date,
                m.is_from_me,
                m.service,
                h.id as handle_id,
                m.attributedBody
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            LEFT JOIN message_attachment_join maj ON maj.message_id = m.ROWID
            WHERE cmj.chat_id = ?
              {date_filter}
              AND ((m.text IS NOT NULL AND m.text != '')
                   OR m.attributedBody IS NOT NULL
                   OR maj.attachment_id IS NOT NULL)
            ORDER BY m.date {order_direction}, m.ROWID {order_direction}
            {limit_clause}
            """,
            params,
        )

        messages = []
        for row in cur.fetchall():
            msg_rowid, text, date, is_from_me, service, handle_id, attributed_body = row
            if not text and attributed_body:
                text = _extract_text_from_attributed_body(attributed_body)

            cur.execute(
                """
                SELECT a.filename, a.mime_type, a.total_bytes, a.transfer_name
                FROM attachment a
                JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
                WHERE maj.message_id = ?
                  AND a.mime_type IS NOT NULL
                  AND (a.mime_type LIKE 'image/%' OR a.mime_type LIKE 'video/%'
                       OR a.mime_type LIKE 'audio/%' OR a.mime_type LIKE 'application/pdf')
                """,
                (msg_rowid,),
            )
            attachments = []
            for a_row in cur.fetchall():
                fname, mime, size, transfer_name = a_row
                if fname:
                    attachments.append(
                        {
                            "filename": fname,
                            "mime_type": mime or "",
                            "size": size or 0,
                            "transfer_name": transfer_name or "",
                        }
                    )

            if not text and not attachments:
                continue

            messages.append(
                {
                    "text": text or "",
                    "timestamp": _apple_ts_to_iso(date),
                    "is_from_me": bool(is_from_me),
                    "service": service or "iMessage",
                    "handle": handle_id or "",
                    "attachments": attachments if attachments else None,
                    "native_message_id": str(msg_rowid),
                }
            )
        return messages
    except Exception:
        return []
    finally:
        conn.close()
