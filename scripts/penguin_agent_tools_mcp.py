#!/usr/bin/env python3
"""Read-only MCP tools for the Penguin AI sidebar agent (Codex or Claude Code).

The sidebar agent runs non-interactively with approvals disabled, so it must never get
send, contact-write, or group-creation tools. This server only reads, through the local
Penguin HTTP API, and is launched over stdio by the agent CLI for each sidebar request.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "scripts"))

from penguin_connect_local_api import resolve_local_api_base  # noqa: E402

MAX_TEXT = 500


def _api_base() -> str:
    explicit = (os.environ.get("PENGUIN_AGENT_API_BASE") or "").strip().rstrip("/")
    return explicit or resolve_local_api_base()


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{_api_base()}{path}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"http_{exc.code}"}
    except Exception as exc:  # pragma: no cover - surfaced to the agent as data
        return {"error": str(exc)}


def _trim(value: Any, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def search_messages(query: str, limit: int = 20) -> dict[str, Any]:
    """Ranked full-text search over the whole message history (iMessage/SMS from chat.db,
    plus cached WhatsApp and Slack). Results include conversation_id when the chat is
    known to Penguin, which read_conversation accepts."""
    limit = max(1, min(int(limit or 20), 50))
    indexed = _get(
        "/penguin-connect/search/hybrid",
        {"query": query, "limit": limit, "include_spotlight": "false"},
    )
    results = []
    for item in indexed.get("results") or []:
        if item.get("kind") != "message":
            continue
        metadata = item.get("metadata") or {}
        results.append(
            {
                "chat": _trim(item.get("title"), 120),
                "timestamp": item.get("timestamp") or "",
                "provider": item.get("provider") or "",
                "text": _trim(item.get("snippet")),
                "conversation_id": metadata.get("conversation_id") or "",
            }
        )
    return {"query": query, "count": len(results), "results": results}


def read_conversation(conversation_id: str, limit: int = 40, offset: int = 0) -> dict[str, Any]:
    """Read recent messages from one Penguin conversation, newest first. Use offset to page back."""
    limit = max(1, min(int(limit or 40), 200))
    payload = _get(
        f"/penguin-connect/conversations/{urllib.parse.quote(conversation_id, safe='')}/messages",
        {"limit": limit, "offset": max(0, int(offset or 0)), "refresh": "false", "compact": "true"},
    )
    if payload.get("error"):
        return payload
    messages = []
    for message in payload.get("messages") or []:
        messages.append(
            {
                "timestamp": message.get("message_timestamp") or "",
                "from": "Me" if message.get("is_from_me") or message.get("direction") == "outbound"
                else (message.get("sender_name") or message.get("sender_email") or ""),
                "text": _trim(message.get("body_text") or ("[attachment]" if message.get("metadata") else "")),
            }
        )
    return {"conversation_id": conversation_id, "count": len(messages), "messages": messages}


def search_contacts(query: str, limit: int = 15) -> dict[str, Any]:
    """Look up saved contacts and known chat participants by name, phone, email, or company."""
    payload = _get(
        "/penguin-connect/contacts",
        {
            "search": query,
            "limit": max(1, min(int(limit or 15), 50)),
            "source": "all",
            "include_counts": "false",
            "include_thread_stats": "false",
        },
    )
    contacts = [
        {
            "name": _trim(contact.get("display_name"), 120),
            "organization": _trim(contact.get("organization"), 120),
            "phone": contact.get("phone") or "",
            "email": contact.get("email") or "",
        }
        for contact in payload.get("contacts") or []
    ]
    return {"query": query, "count": len(contacts), "contacts": contacts}


def list_conversations(query: str = "", limit: int = 25) -> dict[str, Any]:
    """List conversations (most recent first), optionally filtered by name, to find a conversation_id."""
    payload = _get("/penguin-connect/conversations", {"compact": "true", "fast": "true", "sparse": "true"})
    needle = (query or "").strip().lower()
    rows = []
    for conversation in payload.get("conversations") or []:
        name = conversation.get("display_name") or conversation.get("source_chat_identifier") or ""
        if needle and needle not in name.lower():
            continue
        rows.append(
            {
                "conversation_id": conversation.get("conversation_id"),
                "name": _trim(name, 120),
                "provider": conversation.get("source_provider") or "",
                "last_message_at": conversation.get("last_message_at") or conversation.get("updated_at") or "",
            }
        )
        if len(rows) >= max(1, min(int(limit or 25), 100)):
            break
    return {"count": len(rows), "conversations": rows}


TOOLS = (search_messages, read_conversation, search_contacts, list_conversations)


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("penguin-messages")
    for tool in TOOLS:
        mcp.tool()(tool)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
