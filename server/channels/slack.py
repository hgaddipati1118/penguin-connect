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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .base import LookupContactName, LooksLikeUnresolvedHandle

_API_BASE = "https://slack.com/api"
_STATE_PATH = Path.home() / "penguin-connect-data" / "slack-state.json"
_KEYCHAIN_SERVICE = "com.penguinconnect.slack.oauth-token"
_KEYCHAIN_ACCOUNT = "penguin-connect-slack-user"
_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#([A-Z0-9]+)\|([^>]+)>")
_REACTION_NAME_RE = re.compile(r"^[a-z0-9_+\-]+(?::skin-tone-[2-6])?$")
_REACTION_NAMES_BY_EMOJI = {
    "👍": "+1",
    "👎": "-1",
    "❤️": "heart",
    "❤": "heart",
    "😂": "joy",
    "🎉": "tada",
    "👀": "eyes",
    "✅": "white_check_mark",
}
_REACTION_EMOJI_BY_NAME = {
    "+1": "👍",
    "thumbsup": "👍",
    "-1": "👎",
    "thumbsdown": "👎",
    "heart": "❤️",
    "joy": "😂",
    "tada": "🎉",
    "eyes": "👀",
    "white_check_mark": "✅",
}
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


def _profile_avatar_url(*profiles: Any) -> str:
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        for key in ("image_72", "image_48", "image_32", "image_24"):
            url = str(profile.get(key) or "").strip()
            if url.startswith("https://") and len(url) <= 2048:
                return url
    return ""


def _slack_fallback_text(message: dict[str, Any]) -> str:
    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            normalized = " ".join(value.split())
            if normalized and normalized not in parts:
                parts.append(normalized)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("fallback", "pretext", "title", "text", "value"):
            if key in value:
                collect(value.get(key))
        for key in ("fields", "blocks", "elements"):
            if key in value:
                collect(value.get(key))

    collect(message.get("attachments"))
    if not parts:
        collect(message.get("blocks"))
    return "\n".join(parts)


def _slack_reaction_name(value: Any) -> str:
    clean_value = str(value or "").strip()
    if clean_value in _REACTION_NAMES_BY_EMOJI:
        return _REACTION_NAMES_BY_EMOJI[clean_value]
    if clean_value.startswith(":") and clean_value.endswith(":"):
        clean_value = clean_value[1:-1]
    clean_value = clean_value.lower()
    if len(clean_value) > 100 or not _REACTION_NAME_RE.fullmatch(clean_value):
        return ""
    return clean_value


def _slack_reactions(message: dict[str, Any], self_user_id: str) -> list[dict[str, Any]]:
    normalized = []
    raw_reactions = message.get("reactions")
    if not isinstance(raw_reactions, list):
        return normalized
    for item in raw_reactions:
        if not isinstance(item, dict):
            continue
        name = _slack_reaction_name(item.get("name"))
        if not name:
            continue
        try:
            count = max(0, int(item.get("count") or 0))
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        users = item.get("users") if isinstance(item.get("users"), list) else []
        normalized.append(
            {
                "name": name,
                "emoji": _REACTION_EMOJI_BY_NAME.get(name, f":{name}:"),
                "count": count,
                "reacted_by_me": bool(self_user_id and self_user_id in users),
            }
        )
    return normalized


class SlackChannelAdapter:
    provider = "slack"
    provider_label = "Slack"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._user_resolution_lock = threading.Lock()
        self._users: dict[str, str] = {}
        self._user_avatars: dict[str, str] = {}
        self._channels: dict[str, str] = {}
        self._self_user_id = ""
        self._workspace_name = ""
        self._history_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
        self._thread_cache: dict[
            tuple[str, str, str, int],
            tuple[float, list[dict[str, Any]]],
        ] = {}
        self._conversation_cache: dict[
            tuple[str, int],
            tuple[float, dict[str, Any]],
        ] = {}
        self._participant_cache: dict[
            str,
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

    def _upload_external_file(
        self,
        upload_url: str,
        path: Path,
    ) -> tuple[bool, Optional[str]]:
        parsed_url = urlparse(str(upload_url or "").strip())
        hostname = str(parsed_url.hostname or "").lower()
        if (
            parsed_url.scheme != "https"
            or not (
                hostname == "files.slack.com"
                or hostname.endswith(".files.slack.com")
            )
        ):
            return False, "slack_invalid_upload_url"
        try:
            size = path.stat().st_size
            with path.open("rb") as source, httpx.Client(timeout=60) as client:
                response = client.post(
                    upload_url,
                    content=source,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size),
                    },
                )
                response.raise_for_status()
        except OSError:
            return False, "slack_attachment_unreadable"
        except httpx.HTTPError:
            return False, "slack_file_upload_failed"
        return True, None

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
                    avatar_url = _profile_avatar_url(profile)
                    if avatar_url:
                        self._user_avatars[user_id] = avatar_url
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor:
                break

    def _resolve_user_name(self, user_id: str) -> str:
        normalized = str(user_id or "").strip()
        if not normalized:
            return ""
        cached = self._users.get(normalized)
        if cached:
            return cached
        if not normalized.startswith(("U", "W")):
            return ""
        with self._user_resolution_lock:
            cached = self._users.get(normalized)
            if cached:
                return cached
            payload = self._api("users.info", params={"user": normalized})
            user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
            profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
            name = (
                str(profile.get("display_name") or "").strip()
                or str(profile.get("real_name") or "").strip()
                or str(user.get("real_name") or "").strip()
                or str(user.get("name") or "").strip()
                or normalized
            )
            self._users[normalized] = name
            avatar_url = _profile_avatar_url(profile)
            if avatar_url:
                self._user_avatars[normalized] = avatar_url
            return name

    def _render_text(self, value: Any) -> str:
        text = str(value or "")
        text = _CHANNEL_MENTION_RE.sub(lambda match: f"#{match.group(2)}", text)
        return _USER_MENTION_RE.sub(
            lambda match: f"@{self._resolve_user_name(match.group(1))}",
            text,
        ).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

    def _message(self, message: dict[str, Any], channel_id: str) -> dict[str, Any]:
        user_id = str(message.get("user") or message.get("bot_id") or "").strip()
        bot_profile = message.get("bot_profile") if isinstance(message.get("bot_profile"), dict) else {}
        sender_name = (
            self._resolve_user_name(user_id)
            or str(bot_profile.get("name") or "").strip()
            or str(message.get("username") or "").strip()
            or user_id
            or "Slack"
        )
        sender_avatar_url = (
            self._user_avatars.get(user_id)
            or _profile_avatar_url(
                bot_profile.get("icons"),
                message.get("icons"),
            )
        )
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
        thread_ts = str(message.get("thread_ts") or "").strip()
        parent_user_id = str(message.get("parent_user_id") or "").strip()
        message_text = str(message.get("text") or "").strip()
        if not message_text:
            message_text = _slack_fallback_text(message)
        return {
            "text": self._render_text(message_text),
            "timestamp": _timestamp_to_iso(timestamp),
            "native_message_id": timestamp,
            "native_guid": timestamp,
            "chat_id": channel_id,
            "handle": user_id,
            "push_name": sender_name,
            "sender_avatar_url": sender_avatar_url,
            "is_from_me": bool(user_id and user_id == self._self_user_id),
            "attachments": attachments,
            "source_provider": "slack",
            "thread_ts": thread_ts,
            "is_thread_reply": bool(thread_ts and thread_ts != timestamp),
            "thread_parent_name": self._resolve_user_name(parent_user_id),
            "reply_count": max(0, int(message.get("reply_count") or 0)),
            "reply_users_count": max(0, int(message.get("reply_users_count") or 0)),
            "latest_reply": str(message.get("latest_reply") or "").strip(),
            "provider_reactions": _slack_reactions(message, self._self_user_id),
        }

    def _fetch_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        replies: list[dict[str, Any]] = []
        cursor = ""
        max_pages = _env_int("PENGUIN_CONNECT_SLACK_THREAD_PAGES", 2, 1, 10)
        for _ in range(max_pages):
            remaining = max(1, limit - len(replies))
            payload = self._api(
                "conversations.replies",
                params={
                    "channel": channel_id,
                    "ts": thread_ts,
                    "limit": min(100, remaining),
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            if not payload.get("ok"):
                break
            batch = [item for item in payload.get("messages") or [] if isinstance(item, dict)]
            replies.extend(self._message(item, channel_id) for item in batch)
            if len(replies) >= limit:
                break
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor or not batch:
                break
        return replies[:limit]

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

    def list_participants(
        self,
        chat_identifier: str,
        limit: int = 1000,
    ) -> dict[str, Any]:
        channel_id = str(chat_identifier or "").strip()
        if not channel_id:
            return {
                "available": False,
                "reason": "slack_channel_required",
                "participants": [],
            }
        if not _slack_token():
            return {
                "available": False,
                "reason": "PENGUIN_CONNECT_SLACK_TOKEN not set",
                "participants": [],
            }
        with self._lock:
            cache_ttl = _env_int(
                "PENGUIN_CONNECT_SLACK_PARTICIPANT_CACHE_SECONDS",
                300,
                15,
                3600,
            )
            cached = self._participant_cache.get(channel_id)
            if cached and time.monotonic() - cached[0] < cache_ttl:
                return copy.deepcopy(cached[1])
            if not self._load_identity():
                return {
                    "available": False,
                    "reason": "Slack authentication failed",
                    "participants": [],
                }
            self._load_users()
            member_ids: list[str] = []
            seen: set[str] = set()
            cursor = ""
            safe_limit = _safe_limit(limit, default=1000)
            while len(member_ids) < safe_limit:
                payload = self._api(
                    "conversations.members",
                    params={
                        "channel": channel_id,
                        "limit": min(200, safe_limit - len(member_ids)),
                        **({"cursor": cursor} if cursor else {}),
                    },
                )
                if not payload.get("ok"):
                    return {
                        "available": False,
                        "reason": payload.get("error") or "Slack unavailable",
                        "participants": [],
                    }
                for raw_member_id in payload.get("members") or []:
                    member_id = str(raw_member_id or "").strip()
                    if not member_id or member_id in seen:
                        continue
                    seen.add(member_id)
                    member_ids.append(member_id)
                    if len(member_ids) >= safe_limit:
                        break
                cursor = str(
                    (payload.get("response_metadata") or {}).get("next_cursor") or ""
                ).strip()
                if not cursor:
                    break
            participants = [
                {
                    "id": member_id,
                    "display_name": self._resolve_user_name(member_id) or member_id,
                    "avatar_url": self._user_avatars.get(member_id, ""),
                    "is_self": bool(
                        self._self_user_id and member_id == self._self_user_id
                    ),
                }
                for member_id in member_ids
            ]
            participants.sort(
                key=lambda participant: (
                    str(participant["display_name"]).casefold(),
                    str(participant["id"]),
                )
            )
            result = {
                "available": True,
                "channel_id": channel_id,
                "participants": participants,
            }
            self._participant_cache[channel_id] = (
                time.monotonic(),
                copy.deepcopy(result),
            )
            return result

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
            cache_ttl = _env_int("PENGUIN_CONNECT_SLACK_HISTORY_MIN_SECONDS", 5, 5, 3600)
            cached = self._history_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < cache_ttl:
                return [dict(message) for message in cached[1]]
            self._load_identity()
            self._load_users()
            safe_limit = max(1, min(int(limit or 50), 300))
            params: dict[str, Any] = {
                "channel": chat_id,
                "limit": min(100, safe_limit),
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
            thread_roots = sorted(
                (
                    message for message in messages
                    if int(message.get("reply_count") or 0) > 0
                    and str(message.get("native_message_id") or "").strip()
                ),
                key=lambda message: (
                    message.get("latest_reply") or "",
                    message.get("timestamp") or "",
                ),
                reverse=True,
            )[:_env_int("PENGUIN_CONNECT_SLACK_THREAD_FETCHES", 10, 0, 50)]
            seen_message_ids = {
                str(message.get("native_message_id") or "").strip()
                for message in messages
            }

            def hydrate_thread(root: dict[str, Any]) -> list[dict[str, Any]]:
                return self._fetch_thread_replies(
                    chat_id,
                    str(root.get("native_message_id") or "").strip(),
                    limit=min(300, safe_limit),
                )

            thread_cache_ttl = _env_int(
                "PENGUIN_CONNECT_SLACK_THREAD_MIN_SECONDS",
                60,
                5,
                3600,
            )
            now = time.monotonic()
            hydrated_threads: list[list[dict[str, Any]]] = []
            thread_jobs: list[
                tuple[dict[str, Any], tuple[str, str, str, int]]
            ] = []
            for root in thread_roots:
                thread_id = str(root.get("native_message_id") or "").strip()
                thread_cache_key = (
                    chat_id,
                    thread_id,
                    str(root.get("latest_reply") or "").strip(),
                    max(0, int(root.get("reply_count") or 0)),
                )
                cached_thread = self._thread_cache.get(thread_cache_key)
                if cached_thread and now - cached_thread[0] < thread_cache_ttl:
                    hydrated_threads.append(
                        [dict(message) for message in cached_thread[1]]
                    )
                else:
                    thread_jobs.append((root, thread_cache_key))

            thread_workers = min(
                len(thread_jobs),
                _env_int("PENGUIN_CONNECT_SLACK_THREAD_WORKERS", 4, 1, 8),
            )
            if thread_workers:
                with ThreadPoolExecutor(
                    max_workers=thread_workers,
                    thread_name_prefix="penguin-slack-thread",
                ) as executor:
                    fetched_threads = list(executor.map(
                        hydrate_thread,
                        (root for root, _cache_key in thread_jobs),
                    ))
                for (_root, thread_cache_key), replies in zip(
                    thread_jobs,
                    fetched_threads,
                    strict=True,
                ):
                    durable_replies = [dict(message) for message in replies]
                    self._thread_cache[thread_cache_key] = (
                        time.monotonic(),
                        durable_replies,
                    )
                    hydrated_threads.append(durable_replies)
            if len(self._thread_cache) > 500:
                newest_threads = sorted(
                    self._thread_cache.items(),
                    key=lambda item: item[1][0],
                    reverse=True,
                )[:250]
                self._thread_cache = dict(newest_threads)
            for replies in hydrated_threads:
                for reply in replies:
                    message_id = str(reply.get("native_message_id") or "").strip()
                    if not message_id or message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message_id)
                    messages.append(reply)
            messages = [message for message in messages if message.get("timestamp")]
            messages.sort(key=lambda message: message.get("timestamp") or "")
            result = messages[-safe_limit:]
            result_ids = {
                str(message.get("native_message_id") or "").strip()
                for message in result
            }
            roots_by_id = {
                str(message.get("native_message_id") or "").strip(): message
                for message in messages
                if not message.get("is_thread_reply")
                and str(message.get("native_message_id") or "").strip()
            }
            missing_roots = []
            for message in result:
                if not message.get("is_thread_reply"):
                    continue
                thread_id = str(message.get("thread_ts") or "").strip()
                root = roots_by_id.get(thread_id)
                if root is None or thread_id in result_ids:
                    continue
                result_ids.add(thread_id)
                missing_roots.append(root)
            if missing_roots:
                result = sorted(
                    [*missing_roots, *result],
                    key=lambda message: message.get("timestamp") or "",
                )
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
        reply_to_message_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        text = str(message_text or "").strip()
        paths = [
            Path(str(raw_path or "").strip()).expanduser()
            for raw_path in attachment_paths or []
            if str(raw_path or "").strip()
        ]
        prepared_files: list[tuple[Path, str, int]] = []
        for path in paths:
            if not path.exists() or not path.is_file():
                return False, "slack_attachment_missing"
            try:
                size = path.stat().st_size
            except OSError:
                return False, "slack_attachment_unreadable"
            if size <= 0:
                return False, "slack_attachment_empty"
            prepared_files.append((path, path.name or "attachment", size))
        if not text and not prepared_files:
            return False, "empty_message"
        thread_ts = str(reply_to_message_id or "").strip()
        if thread_ts.startswith("slack:"):
            thread_ts = thread_ts.split(":", 1)[1]
        if prepared_files:
            completed_files = []
            for path, filename, size in prepared_files:
                upload_ticket = self._api(
                    "files.getUploadURLExternal",
                    json_body={"filename": filename, "length": size},
                )
                if not upload_ticket.get("ok"):
                    return False, str(
                        upload_ticket.get("error")
                        or "slack_file_upload_ticket_failed"
                    )
                upload_url = str(upload_ticket.get("upload_url") or "").strip()
                file_id = str(upload_ticket.get("file_id") or "").strip()
                if not upload_url or not file_id:
                    return False, "slack_invalid_upload_ticket"
                uploaded, upload_error = self._upload_external_file(
                    upload_url,
                    path,
                )
                if not uploaded:
                    return False, upload_error or "slack_file_upload_failed"
                completed_files.append({"id": file_id, "title": filename})
            body: dict[str, Any] = {
                "files": completed_files,
                "channel_id": chat_identifier,
            }
            if text:
                body["initial_comment"] = text
            if thread_ts:
                body["thread_ts"] = thread_ts
            payload = self._api(
                "files.completeUploadExternal",
                json_body=body,
            )
        else:
            body = {"channel": chat_identifier, "text": text}
            if thread_ts:
                body["thread_ts"] = thread_ts
            payload = self._api(
                "chat.postMessage",
                json_body=body,
            )
        if not payload.get("ok"):
            return False, str(payload.get("error") or "slack_send_failed")
        self._conversation_cache.clear()
        self._history_cache.clear()
        self._thread_cache.clear()
        self._touch_state(last_sent_channel=chat_identifier, last_sent_ts=str(payload.get("ts") or ""))
        return True, None

    def set_reaction(
        self,
        chat_identifier: str,
        message_id: str,
        emoji: str,
        *,
        remove: bool = False,
    ) -> tuple[bool, Optional[str]]:
        channel_id = str(chat_identifier or "").strip()
        timestamp = str(message_id or "").strip()
        if timestamp.startswith("slack:"):
            timestamp = timestamp.split(":", 1)[1]
        reaction_name = _slack_reaction_name(emoji)
        if not channel_id or not timestamp:
            return False, "slack_reaction_target_required"
        if not reaction_name:
            return False, "slack_invalid_reaction"
        method = "reactions.remove" if remove else "reactions.add"
        payload = self._api(
            method,
            json_body={
                "channel": channel_id,
                "timestamp": timestamp,
                "name": reaction_name,
            },
        )
        error = str(payload.get("error") or "").strip()
        idempotent_error = "no_reaction" if remove else "already_reacted"
        if not payload.get("ok") and error != idempotent_error:
            return False, error or "slack_reaction_failed"
        self._conversation_cache.clear()
        self._history_cache.clear()
        self._thread_cache.clear()
        return True, None

    def edit_message(
        self,
        chat_identifier: str,
        message_id: str,
        message_text: str,
    ) -> tuple[bool, Optional[str]]:
        channel_id = str(chat_identifier or "").strip()
        timestamp = str(message_id or "").strip()
        if timestamp.startswith("slack:"):
            timestamp = timestamp.split(":", 1)[1]
        text = str(message_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not channel_id or not timestamp:
            return False, "slack_message_target_required"
        if not text:
            return False, "slack_edit_text_required"
        if len(text) > 20_000:
            return False, "slack_edit_text_too_long"
        payload = self._api(
            "chat.update",
            json_body={
                "channel": channel_id,
                "ts": timestamp,
                "text": text,
            },
        )
        if not payload.get("ok"):
            return False, str(payload.get("error") or "slack_edit_failed")
        self._conversation_cache.clear()
        self._history_cache.clear()
        self._thread_cache.clear()
        return True, None

    def delete_message(
        self,
        chat_identifier: str,
        message_id: str,
    ) -> tuple[bool, Optional[str]]:
        channel_id = str(chat_identifier or "").strip()
        timestamp = str(message_id or "").strip()
        if timestamp.startswith("slack:"):
            timestamp = timestamp.split(":", 1)[1]
        if not channel_id or not timestamp:
            return False, "slack_message_target_required"
        payload = self._api(
            "chat.delete",
            json_body={
                "channel": channel_id,
                "ts": timestamp,
            },
        )
        if not payload.get("ok"):
            return False, str(payload.get("error") or "slack_delete_failed")
        self._conversation_cache.clear()
        self._history_cache.clear()
        self._thread_cache.clear()
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
