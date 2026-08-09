#!/usr/bin/env python3
"""MCP server for PenguinConnect search, contacts, files, and safe sends."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT_DIR / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(ROOT_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "scripts"))

import browse_sources
from channels import get_channel_adapter
from penguin_connect_mcp_auth import load_token
from penguin_connect_local_api import resolve_local_api_base
from search_index import hybrid_search, rebuild_search_index, spotlight_file_search


REMOTE_MCP_SCOPE = "penguin-connect"
DEFAULT_REMOTE_MCP_PORT = 8765
MIN_REMOTE_MCP_TOKEN_LENGTH = 32
REMOTE_CONFIRMATION_TTL_SECONDS = 5 * 60
MAX_REMOTE_PENDING_CONFIRMATIONS = 256
MAX_REMOTE_MESSAGE_CHARS = 16_000
MAX_REMOTE_SEARCH_QUERY_CHARS = 500
LOCAL_SEND_APPROVAL_TIMEOUT_SECONDS = 30
_LOCAL_SEND_APPROVAL_LOCK = threading.Lock()


class StaticBearerTokenVerifier:
    """Verify one locally managed bearer token without logging either value."""

    def __init__(self, expected_token: str):
        self._expected_token = expected_token

    async def verify_token(self, token: str):
        if not hmac.compare_digest(token, self._expected_token):
            return None
        from mcp.server.auth.provider import AccessToken

        return AccessToken(
            token=token,
            client_id="penguin-connect-remote-client",
            scopes=[REMOTE_MCP_SCOPE],
        )


class RemoteConfirmationStore:
    """Hold short-lived, one-use confirmations without persisting message content."""

    def __init__(
        self,
        *,
        ttl_seconds: int = REMOTE_CONFIRMATION_TTL_SECONDS,
        max_pending: int = MAX_REMOTE_PENDING_CONFIRMATIONS,
    ):
        self._ttl_seconds = max(1, ttl_seconds)
        self._max_pending = max(1, max_pending)
        self._pending: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(payload: dict[str, str]) -> bytes:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).digest()

    def _prune_expired(self, now: float) -> None:
        expired = [token for token, (deadline, _) in self._pending.items() if deadline <= now]
        for token in expired:
            self._pending.pop(token, None)

    def issue(self, payload: dict[str, str]) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune_expired(now)
            while len(self._pending) >= self._max_pending:
                self._pending.pop(next(iter(self._pending)))
            self._pending[token] = (now + self._ttl_seconds, self._digest(payload))
        return token

    def consume(self, token: str, payload: dict[str, str]) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            self._prune_expired(now)
            entry = self._pending.pop(token, None)
        if entry is None:
            return False
        deadline, expected_digest = entry
        return deadline > now and hmac.compare_digest(expected_digest, self._digest(payload))


def _is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _load_repo_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env", override=False)
    except ImportError:
        return


def _api_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 45,
) -> dict[str, Any]:
    api_base = resolve_local_api_base()
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{api_base}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("detail") or body
        except Exception:
            detail = body
        raise RuntimeError(f"PenguinConnect API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"PenguinConnect is not reachable at {api_base}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("PenguinConnect returned an unexpected response")
    return result


def _provider_key(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized == "whatsapp":
        return "whatsapp"
    return "imessage"


def _conversation_name(conversation: dict[str, Any]) -> str:
    return (
        str(conversation.get("title") or "").strip()
        or str(conversation.get("display_name") or "").strip()
        or str(conversation.get("source_chat_identifier") or "").strip()
        or "Conversation"
    )


def _conversation_matches(conversation: dict[str, Any], query: str) -> bool:
    needle = (query or "").strip().lower()
    if not needle:
        return True
    contacts = conversation.get("contact_context") if isinstance(conversation.get("contact_context"), list) else []
    values = [
        conversation.get("conversation_id"),
        _conversation_name(conversation),
        conversation.get("source_provider"),
        conversation.get("source_service_name"),
        conversation.get("source_chat_identifier"),
        conversation.get("last_message_preview"),
        *(conversation.get("participants") or []),
        *(contact.get("display_name") for contact in contacts if isinstance(contact, dict)),
        *(contact.get("primary_handle") for contact in contacts if isinstance(contact, dict)),
    ]
    return needle in " ".join(str(value or "") for value in values).lower()


def _conversation_summary(conversation: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": conversation.get("conversation_id"),
        "name": _conversation_name(conversation),
        "provider": _provider_key(str(conversation.get("source_provider") or "")),
        "service": conversation.get("source_service_name"),
        "chat_type": conversation.get("chat_type"),
        "participants": conversation.get("participants") or [],
        "last_message_at": conversation.get("last_message_ts") or conversation.get("updated_at"),
        "last_message_preview": conversation.get("last_message_preview") or "",
        "unread_count": int(conversation.get("unread_count") or 0),
    }


def _contact_summary(contact: dict[str, Any]) -> dict[str, Any]:
    return {
        "contact_key": contact.get("contact_key"),
        "name": contact.get("display_name"),
        "organization": contact.get("organization") or "",
        "phone": contact.get("phone") or contact.get("phone_normalized") or "",
        "email": contact.get("email") or "",
        "primary_handle": contact.get("primary_handle") or "",
        "saved": contact.get("is_saved") is not False,
        "thread_count": int(contact.get("thread_count") or 0),
        "last_interaction_at": contact.get("last_thread_at") or "",
    }


def search_contacts_data(query: str, limit: int = 25) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {"search": (query or "").strip(), "limit": max(1, min(limit, 100)), "source": "all"}
    )
    payload = _api_json("GET", f"/penguin-connect/contacts?{params}")
    contacts = [_contact_summary(contact) for contact in payload.get("contacts") or []]
    return {"query": query, "count": len(contacts), "contacts": contacts}


def search_unified_data(
    query: str,
    *,
    provider: str = "all",
    limit: int = 25,
    include_native_sources: bool = True,
) -> dict[str, Any]:
    clean_query = (query or "").strip()
    safe_limit = max(1, min(limit, 100))
    normalized_provider = (provider or "all").strip().lower()
    conversations_payload = _api_json("GET", "/penguin-connect/conversations?include_whatsapp=true")
    conversations = [
        conversation
        for conversation in conversations_payload.get("conversations") or []
        if _conversation_matches(conversation, clean_query)
        and (
            normalized_provider == "all"
            or _provider_key(str(conversation.get("source_provider") or "")) == normalized_provider
        )
    ][:safe_limit]

    message_params = urllib.parse.urlencode({"query": clean_query, "limit": safe_limit, "view": "all"})
    messages_payload = _api_json("GET", f"/penguin-connect/messages/search?{message_params}")
    messages = [
        {
            "conversation_id": message.get("conversation_id"),
            "provider_message_id": message.get("provider_message_id"),
            "provider": _provider_key(str(message.get("source_provider") or "")),
            "conversation_name": message.get("title") or message.get("display_name") or "Conversation",
            "sender": message.get("sender_name") or message.get("sender_email") or "",
            "text": message.get("body_text") or "",
            "timestamp": message.get("message_timestamp") or "",
        }
        for message in messages_payload.get("messages") or []
        if normalized_provider == "all"
        or _provider_key(str(message.get("source_provider") or "")) == normalized_provider
    ][:safe_limit]
    contacts = search_contacts_data(clean_query, safe_limit).get("contacts") or []

    native: dict[str, Any] = {}
    if include_native_sources and normalized_provider in {"all", "imessage"}:
        result = browse_sources.browse_imessage_chats(search=clean_query, limit=safe_limit)
        native["imessage"] = [
            {
                "chat_id": chat.get("chat_id"),
                "name": chat.get("name"),
                "service": chat.get("service"),
                "chat_type": chat.get("chat_type"),
                "participants": chat.get("participants") or [],
                "last_message_at": chat.get("last_message_at"),
            }
            for chat in result.get("chats") or []
        ] if result.get("available") else []
    if include_native_sources and normalized_provider in {"all", "whatsapp"}:
        result = get_channel_adapter("whatsapp").list_conversations(search=clean_query, limit=safe_limit)
        native["whatsapp"] = [
            {
                "chat_id": chat.get("chat_id"),
                "name": chat.get("name"),
                "chat_type": chat.get("chat_type"),
                "participants": chat.get("participants") or [],
                "last_message_at": chat.get("last_message_at"),
                "last_message_preview": chat.get("last_message_preview") or "",
            }
            for chat in result.get("chats") or []
        ] if result.get("available") else []

    return {
        "query": clean_query,
        "conversations": [_conversation_summary(conversation) for conversation in conversations],
        "messages": messages,
        "contacts": contacts,
        "native_sources": native,
    }


def search_whatsapp_data(query: str, limit: int = 25) -> dict[str, Any]:
    """Return only WhatsApp data for the least-privilege remote toolset."""
    clean_query = (query or "").strip()
    if len(clean_query) > MAX_REMOTE_SEARCH_QUERY_CHARS:
        return {"success": False, "error": "search_query_too_long"}
    safe_limit = max(1, min(limit, 50))
    conversations_payload = _api_json(
        "GET",
        "/penguin-connect/conversations?include_whatsapp=true",
    )
    conversations = [
        _conversation_summary(conversation)
        for conversation in conversations_payload.get("conversations") or []
        if _provider_key(str(conversation.get("source_provider") or "")) == "whatsapp"
        and _conversation_matches(conversation, clean_query)
    ][:safe_limit]

    message_params = urllib.parse.urlencode(
        {"query": clean_query, "limit": safe_limit, "view": "all"}
    )
    messages_payload = _api_json(
        "GET",
        f"/penguin-connect/messages/search?{message_params}",
    )
    messages = [
        {
            "conversation_id": message.get("conversation_id"),
            "provider_message_id": message.get("provider_message_id"),
            "provider": "whatsapp",
            "conversation_name": message.get("title") or message.get("display_name") or "Conversation",
            "sender": message.get("sender_name") or message.get("sender_email") or "",
            "text": message.get("body_text") or "",
            "timestamp": message.get("message_timestamp") or "",
        }
        for message in messages_payload.get("messages") or []
        if _provider_key(str(message.get("source_provider") or "")) == "whatsapp"
    ][:safe_limit]

    native_payload = get_channel_adapter("whatsapp").list_conversations(
        search=clean_query,
        limit=safe_limit,
    )
    native_chats = [
        {
            "chat_id": chat.get("chat_id"),
            "name": chat.get("name"),
            "chat_type": chat.get("chat_type"),
            "participants": chat.get("participants") or [],
            "last_message_at": chat.get("last_message_at"),
            "last_message_preview": chat.get("last_message_preview") or "",
        }
        for chat in native_payload.get("chats") or []
    ] if native_payload.get("available") else []
    return {
        "query": clean_query,
        "conversations": conversations,
        "messages": messages,
        "native_sources": {"whatsapp": native_chats},
    }


def _recipient_handle(recipient: str) -> tuple[str, list[dict[str, Any]]]:
    clean = (recipient or "").strip()
    if not clean:
        return "", []
    if "@" in clean or len(re.sub(r"\D+", "", clean)) >= 7:
        return clean, []
    contacts = search_contacts_data(clean, 10).get("contacts") or []
    exact = [contact for contact in contacts if str(contact.get("name") or "").strip().lower() == clean.lower()]
    candidates = exact or contacts
    if len(candidates) == 1:
        contact = candidates[0]
        return contact.get("primary_handle") or contact.get("phone") or contact.get("email") or "", candidates
    return "", candidates


def _matching_cached_conversations(recipient: str, provider: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"\D+", "", recipient)
    payload = _api_json("GET", "/penguin-connect/conversations?include_whatsapp=true")
    matches = []
    for conversation in payload.get("conversations") or []:
        if provider != "auto" and _provider_key(str(conversation.get("source_provider") or "")) != provider:
            continue
        participants = [str(value or "").strip() for value in conversation.get("participants") or []]
        if any(
            participant.lower() == recipient.lower()
            or (normalized and normalized in re.sub(r"\D+", "", participant))
            for participant in participants
        ):
            matches.append(conversation)
    return matches


def send_message_data(
    recipient: str,
    message: str,
    *,
    provider: Literal["auto", "imessage", "whatsapp"] = "auto",
    conversation_id: str = "",
    attachment_paths: list[str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    clean_message = (message or "").strip()
    clean_attachments = [str(Path(path).expanduser()) for path in (attachment_paths or []) if path]
    if not clean_message and not clean_attachments:
        return {"success": False, "error": "empty_message"}
    normalized_provider = provider if provider in {"auto", "imessage", "whatsapp"} else "auto"

    if conversation_id:
        preview = {
            "action": "send_existing_conversation",
            "conversation_id": conversation_id,
            "message_chars": len(clean_message),
            "attachment_count": len(clean_attachments),
        }
        if not confirm:
            return {"success": False, "confirmation_required": True, "preview": preview}
        result = _api_json(
            "POST",
            f"/penguin-connect/conversations/{urllib.parse.quote(conversation_id, safe='')}/send",
            payload={"sender_email": "", "message": clean_message, "attachment_paths": clean_attachments},
        )
        return {"success": True, "route": preview, "result": result}

    resolved_recipient, contact_candidates = _recipient_handle(recipient)
    if not resolved_recipient:
        return {
            "success": False,
            "error": "recipient_ambiguous_or_not_found",
            "contact_candidates": contact_candidates,
        }

    cached_matches = _matching_cached_conversations(resolved_recipient, normalized_provider)
    if len(cached_matches) == 1:
        return send_message_data(
            "",
            clean_message,
            conversation_id=str(cached_matches[0]["conversation_id"]),
            attachment_paths=clean_attachments,
            confirm=confirm,
        )
    if len(cached_matches) > 1 and normalized_provider == "auto":
        return {
            "success": False,
            "error": "multiple_provider_routes",
            "routes": [_conversation_summary(conversation) for conversation in cached_matches],
        }

    if normalized_provider in {"auto", "imessage"}:
        route = browse_sources.resolve_apple_messages_chat(resolved_recipient)
        if route and not route.get("ambiguous"):
            preview = {
                "action": "send_existing_imessage_route",
                "recipient": resolved_recipient,
                "service": route.get("service_name"),
                "display_name": route.get("display_name"),
                "message_chars": len(clean_message),
                "attachment_count": len(clean_attachments),
            }
            if not confirm:
                return {"success": False, "confirmation_required": True, "preview": preview}
            result = _api_json(
                "POST",
                "/penguin-connect/imessage/send",
                payload={"to": resolved_recipient, "text": clean_message, "attachment_paths": clean_attachments},
            )
            return {"success": bool(result.get("success")), "route": preview, "result": result}
        if route and route.get("ambiguous"):
            return {"success": False, "error": "ambiguous_imessage_route"}
        if normalized_provider == "imessage":
            preview = {
                "action": "stage_new_imessage_draft",
                "recipient": resolved_recipient,
                "message_chars": len(clean_message),
                "attachment_count": len(clean_attachments),
            }
            if not confirm:
                return {"success": False, "confirmation_required": True, "preview": preview}
            result = _api_json(
                "POST",
                "/penguin-connect/messages/draft",
                payload={
                    "participants": [resolved_recipient],
                    "message": clean_message,
                    "attachment_paths": clean_attachments,
                    "copy_to_clipboard": False,
                    "open_messages": True,
                    "open_addressed": True,
                    "open_attachments": bool(clean_attachments),
                },
            )
            return {"success": True, "staged_not_sent": True, "route": preview, "result": result}

    if normalized_provider == "whatsapp":
        digits = re.sub(r"\D+", "", resolved_recipient.split("@", 1)[0])
        whatsapp_recipient = (
            resolved_recipient
            if resolved_recipient.endswith(("@s.whatsapp.net", "@g.us"))
            else f"{digits}@s.whatsapp.net"
        )
        preview = {
            "action": "send_whatsapp",
            "recipient": whatsapp_recipient,
            "message_chars": len(clean_message),
            "attachment_count": len(clean_attachments),
        }
        if not confirm:
            return {"success": False, "confirmation_required": True, "preview": preview}
        ok, error = get_channel_adapter("whatsapp").send_message(
            whatsapp_recipient,
            clean_message,
            attachment_paths=clean_attachments,
        )
        return {"success": ok, "error": error, "route": preview}

    return {
        "success": False,
        "error": "provider_required_for_new_recipient",
        "recipient": resolved_recipient,
        "allowed_providers": ["imessage", "whatsapp"],
    }


def _remote_whatsapp_send_payload(
    recipient: str,
    message: str,
    conversation_id: str,
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    clean_message = (message or "").strip()
    if not clean_message:
        return None, {"success": False, "error": "empty_message"}
    if len(clean_message) > MAX_REMOTE_MESSAGE_CHARS:
        return None, {"success": False, "error": "message_too_long"}

    clean_conversation_id = (conversation_id or "").strip()
    clean_recipient = (recipient or "").strip()
    if clean_conversation_id:
        payload = _api_json("GET", "/penguin-connect/conversations?include_whatsapp=true")
        matches = [
            conversation
            for conversation in payload.get("conversations") or []
            if str(conversation.get("conversation_id") or "") == clean_conversation_id
            and _provider_key(str(conversation.get("source_provider") or "")) == "whatsapp"
        ]
        if len(matches) != 1:
            return None, {"success": False, "error": "whatsapp_conversation_not_found"}
        clean_recipient = ""
    else:
        digits = re.sub(r"\D+", "", clean_recipient.split("@", 1)[0])
        is_whatsapp_jid = bool(
            re.fullmatch(r"[0-9:-]+@(s\.whatsapp\.net|g\.us|lid)", clean_recipient)
        )
        if not is_whatsapp_jid and len(digits) < 7:
            return None, {
                "success": False,
                "error": "whatsapp_recipient_must_be_phone_or_jid",
            }

    return {
        "recipient": clean_recipient,
        "message": clean_message,
        "conversation_id": clean_conversation_id,
    }, None


def request_local_whatsapp_send_approval(payload: dict[str, str]) -> bool:
    """Require a click on this Mac before a remote MCP send can execute."""
    if not _LOCAL_SEND_APPROVAL_LOCK.acquire(blocking=False):
        return False
    destination = payload["conversation_id"] or payload["recipient"]
    message_preview = payload["message"][:500]
    script = f"""
on run argv
    set destinationText to item 1 of argv
    set messageText to item 2 of argv
    try
        set answer to display dialog "Remote MCP wants to send this WhatsApp message to " & destinationText & ":" & return & return & messageText buttons {{"Deny", "Send"}} default button "Deny" cancel button "Deny" with title "PenguinConnect approval" giving up after {LOCAL_SEND_APPROVAL_TIMEOUT_SECONDS}
        if gave up of answer then return "DENIED"
        if button returned of answer is "Send" then return "APPROVED"
    on error
        return "DENIED"
    end try
    return "DENIED"
end run
"""
    try:
        try:
            completed = subprocess.run(
                ["/usr/bin/osascript", "-e", script, destination, message_preview],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=LOCAL_SEND_APPROVAL_TIMEOUT_SECONDS + 5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0 and completed.stdout.strip() == "APPROVED"
    finally:
        _LOCAL_SEND_APPROVAL_LOCK.release()


def remote_send_whatsapp_data(
    confirmations: RemoteConfirmationStore,
    recipient: str,
    message: str,
    *,
    conversation_id: str = "",
    confirmation_token: str = "",
) -> dict[str, Any]:
    """Preview or execute a WhatsApp-only send with an exact one-use confirmation."""
    payload, error = _remote_whatsapp_send_payload(recipient, message, conversation_id)
    if error is not None or payload is None:
        return error or {"success": False, "error": "invalid_request"}

    if confirmation_token:
        if not confirmations.consume(confirmation_token, payload):
            return {"success": False, "error": "invalid_or_expired_confirmation"}
        if not request_local_whatsapp_send_approval(payload):
            return {
                "success": False,
                "error": "local_approval_denied_or_timed_out",
            }
        return send_message_data(
            payload["recipient"],
            payload["message"],
            provider="whatsapp",
            conversation_id=payload["conversation_id"],
            attachment_paths=None,
            confirm=True,
        )

    result = send_message_data(
        payload["recipient"],
        payload["message"],
        provider="whatsapp",
        conversation_id=payload["conversation_id"],
        attachment_paths=None,
        confirm=False,
    )
    if not result.get("confirmation_required"):
        return result
    return {
        **result,
        "confirmation_token": confirmations.issue(payload),
        "confirmation_expires_in_seconds": REMOTE_CONFIRMATION_TTL_SECONDS,
    }


def create_mcp_server(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_REMOTE_MCP_PORT,
    bearer_token: str = "",
):
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.auth.settings import AuthSettings
    except ImportError as exc:
        raise RuntimeError(
            "The MCP SDK is not installed. Run: server/venv/bin/pip install 'mcp>=1.27,<2'"
        ) from exc

    if bearer_token:
        if len(bearer_token) < MIN_REMOTE_MCP_TOKEN_LENGTH:
            raise ValueError("Remote MCP bearer token must be at least 32 characters")
        if not _is_loopback_host(host):
            raise ValueError(
                "Remote MCP must bind to a loopback host; expose it through an authenticated HTTPS tunnel."
            )

    auth_settings = None
    token_verifier = None
    if bearer_token:
        auth_settings = AuthSettings(
            issuer_url="https://penguin-connect.invalid",
            resource_server_url=None,
            required_scopes=[REMOTE_MCP_SCOPE],
        )
        token_verifier = StaticBearerTokenVerifier(bearer_token)

    is_remote = bool(bearer_token)
    mcp = FastMCP(
        "PenguinConnect",
        instructions=(
            (
                "Search WhatsApp and send text-only WhatsApp messages. The remote surface is "
                "intentionally restricted: it cannot access Mac Contacts, files, iMessage, "
                "attachments, or index administration. Sending requires a preview followed by "
                "a second call with the returned short-lived confirmation_token and an approval "
                "click in the local PenguinConnect dialog on the Mac."
            )
            if is_remote
            else (
                "Search local iMessage, WhatsApp, Contacts, and files. "
                "Message sends always require an explicit second call with confirm=true. "
                "A brand-new iMessage destination is staged in Messages for human review because "
                "PenguinConnect never guesses an Apple Messages delivery route."
            )
        ),
        host=host,
        port=port,
        json_response=True,
        stateless_http=bool(bearer_token),
        auth=auth_settings,
        token_verifier=token_verifier,
    )

    if bearer_token:
        from starlette.responses import JSONResponse

        @mcp.custom_route("/health", methods=["GET"])
        async def remote_health(_request):
            return JSONResponse({"ok": True, "service": "penguin-connect-mcp"})

        confirmations = RemoteConfirmationStore()

        @mcp.tool()
        def search_whatsapp(query: str, limit: int = 25) -> dict[str, Any]:
            """Search WhatsApp conversations and messages only."""
            return search_whatsapp_data(query, limit)

        @mcp.tool()
        def send_whatsapp(
            recipient: str = "",
            message: str = "",
            conversation_id: str = "",
            confirmation_token: str = "",
        ) -> dict[str, Any]:
            """Preview or send a text-only WhatsApp message.

            The first call omits confirmation_token and returns the exact route plus a
            short-lived token. Repeat the same arguments with that token to send. Tokens
            are one-use and become invalid if any recipient, conversation, or text changes.
            The Mac owner must also approve the local dialog within 30 seconds.
            New destinations must be a phone number or WhatsApp JID; contact-name lookup and
            local attachment paths are deliberately unavailable remotely.
            """
            return remote_send_whatsapp_data(
                confirmations,
                recipient,
                message,
                conversation_id=conversation_id,
                confirmation_token=confirmation_token,
            )

        return mcp

    @mcp.tool()
    def search_contacts(query: str, limit: int = 25) -> dict[str, Any]:
        """Search Mac Contacts plus unsaved participants from local conversations."""
        return search_contacts_data(query, limit)

    @mcp.tool()
    def search_messages(
        query: str,
        provider: Literal["all", "imessage", "whatsapp"] = "all",
        limit: int = 25,
        include_native_sources: bool = True,
    ) -> dict[str, Any]:
        """Search cached messages, contacts, and native chats beyond Penguin's loaded rail."""
        return search_unified_data(
            query,
            provider=provider,
            limit=limit,
            include_native_sources=include_native_sources,
        )

    @mcp.tool()
    def search_files(
        query: str,
        roots: list[str] | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Search filenames and Spotlight-indexed file contents on this Mac.

        Results contain metadata and local paths, not raw file contents. Roots default
        to Desktop, Documents, and Downloads and must stay inside the user's home folder.
        """
        results = spotlight_file_search(query, roots=roots, limit=limit)
        return {"query": query, "count": len(results), "files": results}

    @mcp.tool()
    def semantic_search(
        query: str,
        kinds: list[Literal["message", "file"]] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the optional local FTS5/sqlite-vec index with hybrid ranking."""
        return hybrid_search(query, kinds=list(kinds or []), limit=limit)

    @mcp.tool()
    def rebuild_local_search_index(
        include_messages: bool = True,
        include_files: bool = True,
        semantic: bool = False,
        roots: list[str] | None = None,
        message_limit: int = 25_000,
        file_limit: int = 1_000,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Rebuild Penguin's private local search index.

        Set semantic=true only after installing sqlite-vec and pulling the configured
        Ollama embedding model. This is local but can be expensive, so confirm=true is
        required.
        """
        if not confirm:
            return {
                "success": False,
                "confirmation_required": True,
                "preview": {
                    "include_messages": include_messages,
                    "include_files": include_files,
                    "semantic": semantic,
                    "roots": roots or [],
                    "message_limit": message_limit,
                    "file_limit": file_limit,
                },
            }
        return rebuild_search_index(
            include_messages=include_messages,
            include_files=include_files,
            semantic=semantic,
            roots=roots,
            message_limit=message_limit,
            file_limit=file_limit,
        )

    @mcp.tool()
    def send_message(
        recipient: str = "",
        message: str = "",
        provider: Literal["auto", "imessage", "whatsapp"] = "auto",
        conversation_id: str = "",
        attachment_paths: list[str] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Preview or send a message through a safe local route.

        The first call should omit confirm and returns the resolved route. Repeat with
        confirm=true to send. Existing native iMessage routes are found even when they
        are absent from Penguin's cache. WhatsApp can address a new phone/JID. A new
        iMessage destination is staged in Messages for review rather than auto-sent.
        """
        return send_message_data(
            recipient,
            message,
            provider=provider,
            conversation_id=conversation_id,
            attachment_paths=attachment_paths,
            confirm=confirm,
        )

    return mcp


def main() -> int:
    _load_repo_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("PENGUIN_CONNECT_MCP_HOST", "127.0.0.1"),
        help="Streamable HTTP bind host (must remain loopback; default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PENGUIN_CONNECT_MCP_PORT", str(DEFAULT_REMOTE_MCP_PORT))),
        help=f"Streamable HTTP port (default: {DEFAULT_REMOTE_MCP_PORT})",
    )
    args = parser.parse_args()
    bearer_token = ""
    if args.transport == "streamable-http":
        bearer_token = load_token()
        if not bearer_token:
            parser.error(
                "remote MCP token missing; run scripts/penguin_connect_mcp_auth.py --ensure"
            )
    mcp = create_mcp_server(
        host=args.host,
        port=args.port,
        bearer_token=bearer_token,
    )
    mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
