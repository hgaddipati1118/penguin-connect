"""Rate-aware Slack Web API adapter for PenguinConnect."""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from .base import LookupContactName, LooksLikeUnresolvedHandle

_API_BASE = "https://slack.com/api"
_STATE_PATH = Path.home() / "penguin-connect-data" / "slack-state.json"
_KEYCHAIN_SERVICE = "com.penguinconnect.slack.oauth-token"
_KEYCHAIN_ACCOUNT = "penguin-connect-slack-user"
_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#([A-Z0-9]+)\|([^>]+)>")
_keychain_token_cache = ""


def _read_slack_keychain_token() -> str:
    global _keychain_token_cache
    if _keychain_token_cache:
        return _keychain_token_cache
    if os.environ.get("PENGUIN_CONNECT_SLACK_DISABLE_KEYCHAIN", "").strip() == "1":
        return ""
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-s",
                _KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    token = (result.stdout or "").strip()
    if token.startswith("xoxp-"):
        _keychain_token_cache = token
        return token
    return ""


def _slack_token() -> str:
    configured = os.environ.get("PENGUIN_CONNECT_SLACK_TOKEN", "").strip()
    return configured or _read_slack_keychain_token()


def slack_source_paths() -> tuple[Path, ...]:
    """Return local files whose changes mean the cached Slack workspace changed."""
    configured = os.environ.get("PENGUIN_CONNECT_SLACK_STATE_PATH", "").strip()
    return (Path(configured).expanduser() if configured else _STATE_PATH,)


def _state_path() -> Path:
    return slack_source_paths()[0]


def _safe_limit(value: Optional[int], default: int = 100) -> int:
    if value is None:
        return 10_000
    try:
        return max(1, min(int(value or default), 10_000))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _timestamp_to_iso(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


class SlackChannelAdapter:
    provider = "slack"
    provider_label = "Slack"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[str, str] = {}
        self._channels: dict[str, str] = {}
        self._self_user_id = ""
        self._workspace_name = ""
        self._history_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
        self._conversation_cache: dict[
            tuple[str, int],
            tuple[float, dict[str, Any]],
        ] = {}

    def _api(
        self,
        method: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        token = _slack_token()
        if not token:
            return {"ok": False, "error": "slack_token_not_configured"}
        try:
            with httpx.Client(
                base_url=_API_BASE,
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            ) as client:
                if json_body is not None:
                    response = client.post(f"/{method}", json=json_body)
                else:
                    response = client.get(f"/{method}", params=params or {})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "error": f"slack_request_failed:{exc}"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "slack_invalid_response"}
        return payload

    def _touch_state(self, **updates: Any) -> None:
        path = _state_path()
        try:
            existing = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            existing = {}
        if all(existing.get(key) == value for key, value in updates.items()):
            return
        existing.update(updates)
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing, indent=2, sort_keys=True))
        except OSError:
            pass

    def _load_identity(self) -> bool:
        if self._self_user_id:
            return True
        payload = self._api("auth.test")
        if not payload.get("ok"):
            return False
        self._self_user_id = str(payload.get("user_id") or "")
        self._workspace_name = str(payload.get("team") or "Slack")
        return True

    def _load_users(self) -> None:
        if self._users:
            return
        cursor = ""
        for _ in range(20):
            payload = self._api(
                "users.list",
                params={"limit": 200, **({"cursor": cursor} if cursor else {})},
            )
            if not payload.get("ok"):
                return
            for member in payload.get("members") or []:
                if not isinstance(member, dict):
                    continue
                profile = member.get("profile") if isinstance(member.get("profile"), dict) else {}
                name = (
                    str(profile.get("display_name") or "").strip()
                    or str(profile.get("real_name") or "").strip()
                    or str(member.get("real_name") or "").strip()
                    or str(member.get("name") or "").strip()
                )
                user_id = str(member.get("id") or "").strip()
                if user_id:
                    self._users[user_id] = name or user_id
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor:
                break

    def _render_text(self, value: Any) -> str:
        text = str(value or "")
        text = _CHANNEL_MENTION_RE.sub(lambda match: f"#{match.group(2)}", text)
        return _USER_MENTION_RE.sub(
            lambda match: f"@{self._users.get(match.group(1), match.group(1))}",
            text,
        ).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

    def _message(self, message: dict[str, Any], channel_id: str) -> dict[str, Any]:
        user_id = str(message.get("user") or message.get("bot_id") or "").strip()
        files = message.get("files") if isinstance(message.get("files"), list) else []
        attachments = []
        for item in files:
            if not isinstance(item, dict):
                continue
            attachments.append(
                {
                    "filename": str(item.get("name") or item.get("title") or "Slack file"),
                    "transfer_name": str(item.get("title") or item.get("name") or "Slack file"),
                    "mime_type": str(item.get("mimetype") or "application/octet-stream"),
                    "size": int(item.get("size") or 0),
                    "remote_url": str(item.get("url_private_download") or item.get("url_private") or ""),
                    "slack_file_id": str(item.get("id") or ""),
                }
            )
        timestamp = str(message.get("ts") or "").strip()
        return {
            "text": self._render_text(message.get("text")),
            "timestamp": _timestamp_to_iso(timestamp),
            "native_message_id": timestamp,
            "native_guid": timestamp,
            "chat_id": channel_id,
            "handle": user_id,
            "push_name": self._users.get(user_id, user_id or "Slack"),
            "is_from_me": bool(user_id and user_id == self._self_user_id),
            "attachments": attachments,
            "source_provider": "slack",
        }

    def _recent_message_seed(self, days: int = 30, max_messages: int = 500) -> dict[str, list[dict[str, Any]]]:
        if not _slack_token().startswith("xoxp-"):
            return {}
        after = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).date().isoformat()
        by_channel: dict[str, list[dict[str, Any]]] = {}
        cursor = "*"
        remaining = max(1, min(max_messages, 1000))
        while remaining > 0:
            payload = self._api(
                "search.messages",
                params={
                    "query": f"after:{after}",
                    "count": min(100, remaining),
                    "sort": "timestamp",
                    "sort_dir": "desc",
                    "highlight": "false",
                    "cursor": cursor,
                },
            )
            if not payload.get("ok"):
                break
            matches = ((payload.get("messages") or {}).get("matches") or [])
            if not matches:
                break
            for match in matches:
                if not isinstance(match, dict):
                    continue
                channel = match.get("channel") if isinstance(match.get("channel"), dict) else {}
                channel_id = str(channel.get("id") or match.get("channel_id") or "").strip()
                if not channel_id:
                    continue
                by_channel.setdefault(channel_id, []).append(self._message(match, channel_id))
            remaining -= len(matches)
            cursor = str(
                (((payload.get("messages") or {}).get("pagination") or {}).get("next_cursor"))
                or (((payload.get("messages") or {}).get("response_metadata") or {}).get("next_cursor"))
                or ""
            ).strip()
            if not cursor:
                break
        return by_channel

    def list_conversations(
        self,
        search: Optional[str] = None,
        limit: Optional[int] = 100,
    ) -> dict[str, Any]:
        if not _slack_token():
            return {"available": False, "reason": "PENGUIN_CONNECT_SLACK_TOKEN not set"}
        with self._lock:
            safe_limit = _safe_limit(limit)
            query = str(search or "").strip().lower()
            cache_key = (query, safe_limit)
            cache_ttl = _env_int(
                "PENGUIN_CONNECT_SLACK_DISCOVERY_MIN_SECONDS",
                45,
                5,
                3600,
            )
            cached = self._conversation_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < cache_ttl:
                return copy.deepcopy(cached[1])
            if not self._load_identity():
                return {"available": False, "reason": "Slack authentication failed"}
            self._load_users()
            channels: list[dict[str, Any]] = []
            cursor = ""
            while len(channels) < safe_limit:
                payload = self._api(
                    "conversations.list",
                    params={
                        "types": "public_channel,private_channel,mpim,im",
                        "exclude_archived": "true",
                        "limit": min(200, safe_limit - len(channels)),
                        **({"cursor": cursor} if cursor else {}),
                    },
                )
                if not payload.get("ok"):
                    return {"available": False, "reason": payload.get("error") or "Slack unavailable"}
                batch = [item for item in payload.get("channels") or [] if isinstance(item, dict)]
                channels.extend(batch)
                cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
                if not cursor or not batch:
                    break

            recent = self._recent_message_seed(
                days=_env_int("PENGUIN_CONNECT_SLACK_SEED_DAYS", 30, 1, 365),
                max_messages=_env_int("PENGUIN_CONNECT_SLACK_SEED_MESSAGES", 200, 1, 1000),
            )
            rows = []
            for channel in channels[:safe_limit]:
                channel_id = str(channel.get("id") or "").strip()
                if not channel_id:
                    continue
                is_im = bool(channel.get("is_im"))
                is_mpim = bool(channel.get("is_mpim"))
                user_id = str(channel.get("user") or "").strip()
                name = str(channel.get("name_normalized") or channel.get("name") or "").strip()
                if is_im:
                    name = self._users.get(user_id, user_id or "Direct message")
                elif is_mpim:
                    name = str(channel.get("name") or "Group message").replace("mpdm-", "").replace("--", ", ")
                else:
                    name = f"#{name}" if name else channel_id
                if query and query not in f"{name} {channel_id}".lower():
                    continue
                self._channels[channel_id] = name
                messages = recent.get(channel_id) or []
                latest = max(messages, key=lambda item: item.get("timestamp") or "", default={})
                members = channel.get("members") if isinstance(channel.get("members"), list) else []
                rows.append(
                    {
                        "chat_id": channel_id,
                        "chat_guid": channel_id,
                        "chat_identifier": channel_id,
                        "name": name,
                        "source_display_name": name,
                        "room_name": name,
                        "chat_type": "dm" if is_im else ("group" if is_mpim else "channel"),
                        "participants": members or ([user_id] if user_id else []),
                        "message_count": len(messages),
                        "last_message_at": latest.get("timestamp"),
                        "last_message_preview": latest.get("text") or "",
                        "latest_message": latest,
                        "service": "Slack",
                        "source_provider": "slack",
                    }
                )
            rows.sort(key=lambda row: row.get("last_message_at") or "", reverse=True)
            self._touch_state(
                workspace=self._workspace_name,
                conversation_count=len(rows),
                latest_message_at=max((row.get("last_message_at") or "" for row in rows), default=""),
            )
            result = {"available": True, "chats": rows, "workspace": self._workspace_name}
            self._conversation_cache[cache_key] = (time.monotonic(), copy.deepcopy(result))
            return result

    def list_recent_activity(self, since: str, limit: int = 500) -> dict[str, Any]:
        discovered = self.list_conversations(limit=limit)
        if not discovered.get("available"):
            return discovered
        cutoff = str(since or "")
        return {
            **discovered,
            "chats": [
                chat for chat in discovered.get("chats") or []
                if str(chat.get("last_message_at") or "") > cutoff
            ],
        }

    def fetch_messages(
        self,
        chat_id: str,
        limit: int = 50,
        since: Optional[str] = None,
        since_native_message_id: Optional[str] = None,
        before: Optional[str] = None,
        before_native_message_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        del since_native_message_id, before_native_message_id
        if not _slack_token() or not chat_id:
            return []
        with self._lock:
            cache_key = (chat_id, str(since or ""), str(before or ""))
            cache_ttl = _env_int("PENGUIN_CONNECT_SLACK_HISTORY_MIN_SECONDS", 60, 5, 3600)
            cached = self._history_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < cache_ttl:
                return [dict(message) for message in cached[1]]
            self._load_identity()
            self._load_users()
            safe_limit = max(1, min(int(limit or 50), 300))
            params: dict[str, Any] = {
                "channel": chat_id,
                "limit": min(15, safe_limit),
                "inclusive": "false",
            }
            if since:
                try:
                    params["oldest"] = f"{datetime.fromisoformat(since).timestamp():.6f}"
                except ValueError:
                    pass
            if before:
                try:
                    params["latest"] = f"{datetime.fromisoformat(before).timestamp():.6f}"
                except ValueError:
                    pass
            messages: list[dict[str, Any]] = []
            cursor = ""
            max_pages = _env_int("PENGUIN_CONNECT_SLACK_HISTORY_PAGES", 1, 1, 20)
            for _ in range(max_pages):
                payload = self._api(
                    "conversations.history",
                    params={**params, **({"cursor": cursor} if cursor else {})},
                )
                if not payload.get("ok"):
                    break
                batch = [item for item in payload.get("messages") or [] if isinstance(item, dict)]
                messages.extend(self._message(item, chat_id) for item in batch)
                if len(messages) >= safe_limit:
                    break
                cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
                if not cursor or not batch:
                    break
            messages = [message for message in messages if message.get("timestamp")]
            messages.sort(key=lambda message: message.get("timestamp") or "")
            result = messages[-safe_limit:]
            self._history_cache[cache_key] = (time.monotonic(), result)
            self._touch_state(
                workspace=self._workspace_name,
                last_history_channel=chat_id,
                last_history_message_at=max((item.get("timestamp") or "" for item in messages), default=""),
            )
            return [dict(message) for message in result]

    def send_message(
        self,
        chat_identifier: str,
        message_text: str,
        attachment_paths: Optional[list[str]] = None,
    ) -> tuple[bool, Optional[str]]:
        if attachment_paths:
            return False, "slack_file_upload_not_supported_yet"
        text = str(message_text or "").strip()
        if not text:
            return False, "empty_message"
        payload = self._api(
            "chat.postMessage",
            json_body={"channel": chat_identifier, "text": text},
        )
        if not payload.get("ok"):
            return False, str(payload.get("error") or "slack_send_failed")
        self._conversation_cache.clear()
        self._history_cache.clear()
        self._touch_state(last_sent_channel=chat_identifier, last_sent_ts=str(payload.get("ts") or ""))
        return True, None

    def get_unread_count(self, chat_identifier: str) -> Optional[int]:
        del chat_identifier
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
        del lookup_contact_name, looks_like_unresolved_handle
        if msg.get("is_from_me"):
            sender_name = "Me"
        else:
            handle = str(msg.get("handle") or "").strip()
            sender_name = str(msg.get("push_name") or "").strip() or self._users.get(handle) or handle or "Slack"
        subject_name = str(conv["display_name"] or "").strip() or self._channels.get(
            str(conv["source_chat_id"] or "").strip(),
            "Slack conversation",
        )
        return sender_name, subject_name
