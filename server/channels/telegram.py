"""Telegram adapter for PenguinConnect's source-channel interface.

Uses Telethon (MTProto API) for full access to user's message history.
Requires PENGUIN_CONNECT_TELEGRAM_API_ID and PENGUIN_CONNECT_TELEGRAM_API_HASH env vars.
Session file persists auth at ~/penguin-connect-data/telegram.session.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .base import LookupContactName, LooksLikeUnresolvedHandle

_DEFAULT_SESSION_PATH = str(Path.home() / "penguin-connect-data" / "telegram")
_MEDIA_CACHE_DIR = Path.home() / "penguin-connect-data" / "telegram-media"


def _session_path() -> str:
    return os.environ.get("PENGUIN_CONNECT_TELEGRAM_SESSION_PATH", _DEFAULT_SESSION_PATH)


def _get_api_credentials() -> tuple[Optional[int], Optional[str]]:
    api_id = os.environ.get("PENGUIN_CONNECT_TELEGRAM_API_ID")
    api_hash = os.environ.get("PENGUIN_CONNECT_TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        return None, None
    try:
        return int(api_id), api_hash
    except (ValueError, TypeError):
        return None, None


# Lazy-loaded Telethon client and event loop.
_client = None
_loop = None


def _get_loop():
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


def _get_client():
    global _client
    if _client is not None and _client.is_connected():
        return _client

    api_id, api_hash = _get_api_credentials()
    if not api_id:
        return None

    from telethon import TelegramClient

    _client = TelegramClient(_session_path(), api_id, api_hash)
    loop = _get_loop()
    loop.run_until_complete(_client.connect())

    if not loop.run_until_complete(_client.is_user_authorized()):
        return None

    return _client


def _telegram_available() -> bool:
    api_id, _ = _get_api_credentials()
    return api_id is not None


class TelegramChannelAdapter:
    provider = "telegram"
    provider_label = "Telegram"

    # ------------------------------------------------------------------
    # list_conversations
    # ------------------------------------------------------------------
    def list_conversations(self, search: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
        api_id, _ = _get_api_credentials()
        if not api_id:
            return {"available": False, "reason": "PENGUIN_CONNECT_TELEGRAM_API_ID/API_HASH not set"}

        client = _get_client()
        if not client:
            return {"available": False, "reason": "Telegram not authenticated — run scripts/telegram_auth.py"}

        loop = _get_loop()

        async def _browse():
            from telethon.tl.types import Channel, Chat, User

            chats = []
            async for dialog in client.iter_dialogs(limit=limit):
                entity = dialog.entity
                peer_id = str(dialog.id)

                if isinstance(entity, User):
                    chat_type = "dm"
                    name = f"{entity.first_name or ''} {entity.last_name or ''}".strip()
                    if not name:
                        name = entity.username or peer_id
                    participants = [name]
                elif isinstance(entity, Chat):
                    chat_type = "group"
                    name = entity.title or peer_id
                    participants = []
                elif isinstance(entity, Channel):
                    chat_type = "channel" if entity.broadcast else "group"
                    name = entity.title or peer_id
                    participants = []
                else:
                    continue

                if search:
                    s = search.lower()
                    if s not in f"{name} {peer_id} {' '.join(participants)}".lower():
                        continue

                last_msg = ""
                last_msg_at = None
                if dialog.message:
                    last_msg = (dialog.message.text or "")[:120]
                    if dialog.message.date:
                        last_msg_at = dialog.message.date.astimezone(timezone.utc).isoformat()

                chats.append(
                    {
                        "chat_id": peer_id,
                        "chat_guid": peer_id,
                        "chat_identifier": peer_id,
                        "name": name,
                        "source_display_name": name,
                        "room_name": "",
                        "chat_type": chat_type,
                        "participants": participants,
                        "message_count": dialog.unread_count or 0,
                        "last_message_at": last_msg_at,
                        "last_message_preview": last_msg,
                        "service": "Telegram",
                        "source_provider": "telegram",
                    }
                )

            return chats

        try:
            chats = loop.run_until_complete(_browse())
            return {"available": True, "chats": chats}
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # list_recent_activity
    # ------------------------------------------------------------------
    def list_recent_activity(self, since: str, limit: int = 500) -> dict[str, Any]:
        client = _get_client()
        if not client:
            return {"available": False, "reason": "Telegram not authenticated"}

        loop = _get_loop()

        since_dt = None
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except Exception:
            pass

        async def _recent():
            from telethon.tl.types import Channel, Chat, User

            chats = []
            async for dialog in client.iter_dialogs(limit=limit):
                last_msg_at = None
                if dialog.message and dialog.message.date:
                    last_msg_at = dialog.message.date.astimezone(timezone.utc).isoformat()

                if since_dt and dialog.message and dialog.message.date:
                    if dialog.message.date.astimezone(timezone.utc) <= since_dt:
                        continue
                elif since_dt:
                    continue

                peer_id = str(dialog.id)
                chats.append(
                    {
                        "chat_id": peer_id,
                        "chat_guid": peer_id,
                        "chat_identifier": peer_id,
                        "service": "Telegram",
                        "source_provider": "telegram",
                        "first_message_at": last_msg_at,
                        "last_message_at": last_msg_at,
                        "message_count": 1,
                    }
                )
            return chats

        try:
            chats = loop.run_until_complete(_recent())
            return {"available": True, "chats": chats}
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # fetch_messages
    # ------------------------------------------------------------------
    def fetch_messages(
        self,
        chat_id: str,
        limit: int = 50,
        since: Optional[str] = None,
        since_native_message_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        client = _get_client()
        if not client:
            return []

        loop = _get_loop()

        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except Exception:
                pass

        async def _fetch():
            try:
                entity = await client.get_entity(int(chat_id))
            except (ValueError, TypeError):
                return []

            import mimetypes as _mimetypes

            from telethon.tl.types import User

            media_dir = _MEDIA_CACHE_DIR / chat_id
            messages = []
            fetch_limit = limit if not since_dt else None
            async for msg in client.iter_messages(entity, limit=fetch_limit):
                if since_dt and msg.date and msg.date.astimezone(timezone.utc) <= since_dt:
                    break

                # Build attachment info if media is present.
                attachments = None
                if msg.media and not msg.text and not getattr(msg.media, "webpage", None):
                    # Media-only message — download and include.
                    pass  # handled below
                elif not msg.text and not msg.media:
                    continue

                if msg.media and not getattr(msg.media, "webpage", None):
                    try:
                        media_dir.mkdir(parents=True, exist_ok=True)
                        local_path = await client.download_media(msg, file=str(media_dir))
                        if local_path and Path(local_path).exists():
                            fname = Path(local_path).name
                            guessed, _ = _mimetypes.guess_type(fname)
                            attachments = [
                                {
                                    "filename": local_path,
                                    "mime_type": guessed or "application/octet-stream",
                                    "size": Path(local_path).stat().st_size,
                                    "transfer_name": fname,
                                }
                            ]
                    except Exception:
                        pass

                if not msg.text and not attachments:
                    continue

                sender_name = ""
                is_from_me = msg.out
                if not is_from_me and msg.sender:
                    if isinstance(msg.sender, User):
                        sender_name = f"{msg.sender.first_name or ''} {msg.sender.last_name or ''}".strip()
                    else:
                        sender_name = getattr(msg.sender, "title", "") or ""

                ts = msg.date.astimezone(timezone.utc).isoformat() if msg.date else ""

                messages.append(
                    {
                        "text": msg.text or "",
                        "timestamp": ts,
                        "is_from_me": is_from_me,
                        "service": "Telegram",
                        "handle": str(msg.sender_id) if msg.sender_id else "",
                        "push_name": sender_name,
                        "attachments": attachments,
                        "native_message_id": str(msg.id),
                    }
                )

            return messages

        try:
            result = loop.run_until_complete(_fetch())
            # Telethon returns newest-first; reverse for chronological order when using since.
            if since_dt:
                result.reverse()
            return result
        except Exception:
            return []

    # ------------------------------------------------------------------
    # send_message
    # ------------------------------------------------------------------
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

        client = _get_client()
        if not client:
            return False, "telegram_not_authenticated"

        loop = _get_loop()

        async def _send():
            entity = await client.get_entity(int(chat_identifier))
            for attachment_path in valid_attachments:
                await client.send_file(entity, attachment_path)
            if normalized_text:
                await client.send_message(entity, normalized_text)

        try:
            loop.run_until_complete(_send())
            return True, None
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------
    # get_unread_count
    # ------------------------------------------------------------------
    def get_unread_count(self, chat_identifier: str) -> Optional[int]:
        client = _get_client()
        if not client:
            return None

        loop = _get_loop()

        async def _unread():
            async for dialog in client.iter_dialogs():
                if str(dialog.id) == chat_identifier:
                    return dialog.unread_count
            return None

        try:
            return loop.run_until_complete(_unread())
        except Exception:
            return None

    # ------------------------------------------------------------------
    # resolve_sender_and_subject
    # ------------------------------------------------------------------
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
            contact_name = lookup_contact_name(conn, handle)
            sender_name = contact_name or push_name or handle or "Telegram"

        display_name = (conv["display_name"] or "").strip()
        if display_name and not looks_like_unresolved_handle(display_name):
            subject_name = display_name
        elif (conv["chat_type"] or "").strip().lower() == "dm":
            contact_name = lookup_contact_name(conn, handle)
            subject_name = contact_name or push_name or display_name or handle or "Conversation"
        else:
            subject_name = display_name or "Conversation"

        return sender_name, subject_name
