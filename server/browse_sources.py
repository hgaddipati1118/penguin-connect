"""Minimal iMessage browse/fetch helpers for PenguinConnect bridge."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

IMESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


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


def browse_imessage_chats(search=None, limit=100):
    if not os.path.exists(IMESSAGE_DB):
        return {"available": False, "reason": "chat.db not found"}

    conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.ROWID,
                c.chat_identifier,
                c.display_name,
                c.service_name,
                COUNT(DISTINCT cmj.message_id) as msg_count,
                MAX(m.date) as last_msg_date
            FROM chat c
            LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            LEFT JOIN message m ON m.ROWID = cmj.message_id
            GROUP BY c.ROWID
            HAVING msg_count > 0
            ORDER BY last_msg_date DESC
            LIMIT ?
            """,
            (limit,),
        )
        raw_chats = cur.fetchall()

        chats = []
        for row in raw_chats:
            chat_rowid, chat_id, display_name, service, msg_count, last_date = row

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
            name = display_name or ""
            if not name and participants:
                name = ", ".join(participants[:3])
                if len(participants) > 3:
                    name += f" +{len(participants) - 3}"
            if not name:
                name = chat_id or "iMessage Conversation"

            if search:
                s = search.lower()
                searchable = f"{name} {chat_id} {' '.join(participants)}".lower()
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
                    "chat_id": chat_id,
                    "chat_identifier": chat_id,
                    "name": name,
                    "chat_type": chat_type,
                    "participants": participants,
                    "message_count": msg_count,
                    "last_message_at": _apple_ts_to_iso(last_date),
                    "last_message_preview": last_msg,
                    "service": service or "iMessage",
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
                c.chat_identifier,
                MIN(m.date) AS first_msg_date,
                MAX(m.date) AS last_msg_date,
                COUNT(DISTINCT m.ROWID) AS msg_count
            FROM chat c
            JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            JOIN message m ON m.ROWID = cmj.message_id
            LEFT JOIN message_attachment_join maj ON maj.message_id = m.ROWID
            WHERE c.chat_identifier IS NOT NULL
              AND m.date > ?
              AND ((m.text IS NOT NULL AND m.text != '')
                   OR m.attributedBody IS NOT NULL
                   OR maj.attachment_id IS NOT NULL)
            GROUP BY c.ROWID, c.chat_identifier
            ORDER BY first_msg_date ASC, c.ROWID ASC
            LIMIT ?
            """,
            (since_ns, safe_limit),
        )

        chats = []
        for chat_id, first_date, last_date, msg_count in cur.fetchall():
            if not chat_id:
                continue
            chats.append(
                {
                    "chat_id": chat_id,
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
    row = cur.execute("SELECT ROWID FROM chat WHERE chat_identifier = ?", (chat_identifier,)).fetchone()
    if row:
        return row[0]
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


def fetch_imessage_messages(chat_id, limit=50, since=None):
    if not os.path.exists(IMESSAGE_DB):
        return []

    conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        chat_rowid = _resolve_imessage_rowid(cur, chat_id)
        if not chat_rowid:
            return []

        since_ns = _iso_to_apple_ns(since)
        date_filter = "AND m.date > ?" if since_ns else ""
        limit_clause = "LIMIT ?"
        order_direction = "ASC" if since_ns else "DESC"

        safe_limit = max(1, min(int(limit or 50), 1000))
        params = [chat_rowid]
        if since_ns:
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
            ORDER BY m.date {order_direction}
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
