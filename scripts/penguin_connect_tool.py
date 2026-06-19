#!/usr/bin/env python3
"""Local operator CLI for PenguinConnect conversations, contacts, and drafts."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT_DIR / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(ROOT_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "scripts"))

import browse_sources
import import_contacts
from penguin_connect_local_api import resolve_local_api_base

DATA_DIR = Path(os.environ.get("PENGUIN_CONNECT_DATA_DIR", str(Path.home() / "penguinconnect-local-bridge-data"))).expanduser()
CACHE_DB = Path(os.environ.get("PENGUIN_CONNECT_DB_PATH", str(DATA_DIR / "cache.db"))).expanduser()


class ToolError(Exception):
    """User-facing command failure."""


def _trim(value: Any, limit: int = 96) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _api_json(
    method: str,
    path: str,
    *,
    api_base: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{api_base}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body
        try:
            parsed = json.loads(body)
            detail = parsed.get("detail") or parsed
        except Exception:
            pass
        raise ToolError(f"API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Local bridge is not reachable at {api_base}: {exc.reason}") from exc

    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ToolError(f"API returned invalid JSON from {path}") from exc
    if not isinstance(parsed, dict):
        raise ToolError(f"API returned unexpected payload from {path}")
    return parsed


def _normalize_search(value: Any) -> str:
    return str(value or "").strip().lower()


def _conversation_matches(conversation: dict[str, Any], query: str) -> bool:
    needle = _normalize_search(query)
    if not needle:
        return True
    participants = conversation.get("participants") or []
    haystack = " ".join(
        [
            str(conversation.get("conversation_id") or ""),
            str(conversation.get("source_provider") or ""),
            str(conversation.get("source_chat_id") or ""),
            str(conversation.get("source_chat_identifier") or ""),
            str(conversation.get("source_service_name") or ""),
            str(conversation.get("display_name") or ""),
            str(conversation.get("alias_email") or ""),
            " ".join(str(participant) for participant in participants),
        ]
    ).lower()
    return needle in haystack


def _format_conversation(conversation: dict[str, Any]) -> str:
    name = conversation.get("display_name") or "Conversation"
    provider = conversation.get("source_provider") or "imessage"
    service = conversation.get("source_service_name") or provider
    chat_type = conversation.get("chat_type") or "chat"
    status = conversation.get("status") or "unknown"
    ts = conversation.get("last_message_ts") or conversation.get("updated_at") or "n/a"
    return (
        f"{conversation.get('conversation_id')} | {_trim(name, 42)} | "
        f"{chat_type} | {service} | {status} | last={ts}"
    )


def _format_source_chat(chat: dict[str, Any]) -> str:
    participants = chat.get("participants") or []
    participant_summary = ", ".join(str(p) for p in participants[:3])
    if len(participants) > 3:
        participant_summary += f" +{len(participants) - 3}"
    if not participant_summary:
        participant_summary = "n/a"
    return (
        f"{chat.get('chat_id')} | {_trim(chat.get('name') or 'Apple Messages Conversation', 42)} | "
        f"{chat.get('chat_type') or 'chat'} | {chat.get('service') or 'iMessage'} | "
        f"participants={_trim(participant_summary, 48)} | last={chat.get('last_message_at') or 'n/a'}"
    )


def _format_search_message_row(row: dict[str, Any]) -> str:
    conversation_id = row.get("conversation_id") or row.get("chat_id") or "unknown"
    name = row.get("display_name") or row.get("chat_name") or "Conversation"
    sender = row.get("sender_name") or row.get("handle") or row.get("direction") or "unknown"
    ts = row.get("message_timestamp") or row.get("timestamp") or "n/a"
    body = _trim(row.get("body_text") or row.get("text") or "", 180)
    attachment_summary = _format_message_attachment_summary(row)
    if attachment_summary:
        body = f"{body} {attachment_summary}".strip()
    return f"{ts} | {conversation_id} | {_trim(name, 32)} | {_trim(sender, 24)} | {body}"


def command_status(args: argparse.Namespace) -> int:
    payload = _api_json("GET", "/penguin-connect/health", api_base=args.api_base, timeout=args.timeout)
    if args.json:
        _print_json(payload)
    else:
        print(f"ok: {bool(payload.get('ok'))}")
        gmail = payload.get("gmail") or {}
        print(f"gmail: {gmail.get('gmail_email') or 'not connected'}")
        conversations = payload.get("conversations") or {}
        print(
            "conversations: "
            f"active={conversations.get('active', 0)} "
            f"disconnected={conversations.get('disconnected', 0)} "
            f"total={conversations.get('total', 0)}"
        )
    return 0


def command_search(args: argparse.Namespace) -> int:
    limit = max(1, min(args.limit, 1000))
    output: dict[str, Any] = {}
    if args.source in {"bridge", "both"}:
        payload = _api_json("GET", "/penguin-connect/conversations", api_base=args.api_base, timeout=args.timeout)
        rows = [
            conversation
            for conversation in payload.get("conversations", [])
            if isinstance(conversation, dict) and _conversation_matches(conversation, args.query)
        ][:limit]
        output["bridge_conversations"] = rows
        if not args.json:
            print(f"Bridge conversations ({len(rows)}):")
            for row in rows:
                print(f"  {_format_conversation(row)}")

    if args.source in {"imessage", "both"}:
        result = browse_sources.browse_imessage_chats(search=args.query, limit=limit)
        if not result.get("available"):
            raise ToolError(result.get("reason") or "Unable to browse Apple Messages chats")
        chats = result.get("chats", [])
        output["imessage_chats"] = chats
        if not args.json:
            if args.source == "both":
                print("")
            print(f"Apple Messages chats ({len(chats)}):")
            for chat in chats:
                print(f"  {_format_source_chat(chat)}")
    if args.json:
        _print_json(output)
    return 0


def _search_bridge_messages(query: str, *, limit: int) -> list[dict[str, Any]]:
    needle = _normalize_search(query)
    if not needle or not CACHE_DB.exists():
        return []
    like = f"%{needle}%"
    conn = sqlite3.connect(str(CACHE_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                c.conversation_id,
                c.display_name,
                c.source_provider,
                c.source_service_name,
                m.provider,
                m.provider_message_id,
                m.direction,
                m.sender_email,
                m.sender_name,
                m.body_text,
                m.message_timestamp,
                m.metadata,
                m.gmail_message_id,
                m.gmail_thread_id
            FROM penguin_connect_messages m
            JOIN penguin_connect_conversations c
              ON c.conversation_id = m.conversation_id
            WHERE lower(
                COALESCE(c.conversation_id, '') || ' ' ||
                COALESCE(c.display_name, '') || ' ' ||
                COALESCE(c.source_provider, '') || ' ' ||
                COALESCE(c.source_chat_identifier, '') || ' ' ||
                COALESCE(c.participants, '') || ' ' ||
                COALESCE(m.sender_email, '') || ' ' ||
                COALESCE(m.sender_name, '') || ' ' ||
                COALESCE(m.subject, '') || ' ' ||
                COALESCE(m.body_text, '') || ' ' ||
                COALESCE(m.metadata, '')
            ) LIKE ?
            ORDER BY m.message_timestamp DESC, m.id DESC
            LIMIT ?
            """,
            (like, max(1, min(limit, 1000))),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except Exception:
            item["metadata"] = {}
        if isinstance(item["metadata"].get("attachments"), list):
            item["attachments"] = item["metadata"]["attachments"]
        out.append(item)
    return out


def command_message_search(args: argparse.Namespace) -> int:
    query = (args.query or "").strip()
    if not query:
        raise ToolError("Message search requires a non-empty query.")
    limit = max(1, min(args.limit, 1000))
    output: dict[str, Any] = {}

    if args.source in {"bridge", "both"}:
        rows = _search_bridge_messages(query, limit=limit)
        output["bridge_messages"] = rows
        if not args.json:
            print(f"Bridge messages ({len(rows)}):")
            for row in rows:
                print(f"  {_format_search_message_row(row)}")

    if args.source in {"imessage", "both"}:
        result = browse_sources.search_imessage_messages(query, limit=limit)
        if not result.get("available"):
            raise ToolError(result.get("reason") or "Unable to search Apple Messages")
        rows = result.get("messages", [])
        output["imessage_messages"] = rows
        if not args.json:
            if args.source == "both":
                print("")
            print(f"Apple Messages ({len(rows)}):")
            for row in rows:
                print(f"  {_format_search_message_row(row)}")

    if args.json:
        _print_json(output)
    return 0


def command_messages(args: argparse.Namespace) -> int:
    payload = _api_json(
        "GET",
        f"/penguin-connect/conversations/{args.conversation_id}/messages?limit={args.limit}",
        api_base=args.api_base,
        timeout=args.timeout,
    )
    if args.json:
        _print_json(payload)
        return 0

    messages = list(reversed(payload.get("messages") or []))
    print(f"{args.conversation_id} ({len(messages)} messages):")
    for message in messages:
        sender = message.get("sender_name") or message.get("sender_email") or message.get("direction") or "unknown"
        ts = message.get("message_timestamp") or "n/a"
        body = _trim(message.get("body_text"), args.body_chars)
        attachment_summary = _format_message_attachment_summary(message)
        if attachment_summary:
            body = f"{body} {attachment_summary}".strip()
        print(f"  {ts} | {_trim(sender, 28)} | {body}")
    return 0


def command_send(args: argparse.Namespace) -> int:
    message = args.message
    if args.message_file:
        message = Path(args.message_file).expanduser().read_text(encoding="utf-8")
    attachment_paths = _resolve_attachment_paths(args.attachment_paths)
    if not (message or "").strip() and not attachment_paths:
        raise ToolError("Message text or at least one attachment is required.")
    sender_email = (args.sender_email or "").strip().lower()

    payload = _api_json(
        "POST",
        f"/penguin-connect/conversations/{args.conversation_id}/send",
        api_base=args.api_base,
        payload={"sender_email": sender_email, "message": message, "attachment_paths": attachment_paths or None},
        timeout=args.timeout,
    )
    if args.json:
        _print_json(payload)
    else:
        print(f"sent: {bool(payload.get('success'))}")
        print(f"conversation_id: {payload.get('conversation_id') or args.conversation_id}")
        if attachment_paths:
            print(f"attachments: {len(attachment_paths)}")
    return 0


def _resolve_attachment_paths(paths: Iterable[str]) -> list[str]:
    resolved: list[str] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_file():
            raise ToolError(f"Attachment file not found: {raw_path}")
        resolved.append(str(path))
    return resolved


def _format_message_attachment_summary(message: dict[str, Any]) -> str:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        attachments = metadata.get("attachments") if isinstance(metadata.get("attachments"), list) else []
    if attachments:
        labels = []
        for attachment in attachments[:3]:
            if not isinstance(attachment, dict):
                continue
            label = (
                Path(str(attachment.get("transfer_name") or attachment.get("filename") or "attachment")).name
                or attachment.get("mime_type")
                or "attachment"
            )
            mime_type = attachment.get("mime_type") or ""
            if str(mime_type).startswith("audio/"):
                label = f"audio:{label}"
            labels.append(_trim(label, 40))
        suffix = f" +{len(attachments) - 3} more" if len(attachments) > 3 else ""
        return f"[attachments: {', '.join(labels)}{suffix}]"

    manual_count = int(metadata.get("manual_attachment_count") or 0)
    if manual_count:
        return f"[attachments sent: {manual_count}]"
    return ""


def _contact_display(row: sqlite3.Row) -> str:
    name = " ".join(part for part in [row["first_name"], row["last_name"]] if part).strip()
    if not name:
        name = row["organization"] or "Contact"
    handles = [value for value in [row["phone"], row["email"]] if value]
    return f"{row['id']} | {_trim(name, 36)} | {_trim(row['organization'], 28)} | {', '.join(handles) or 'n/a'}"


def _search_contacts(query: str, *, limit: int) -> list[sqlite3.Row]:
    if not CACHE_DB.exists():
        return []
    needle = f"%{_normalize_search(query)}%"
    conn = sqlite3.connect(str(CACHE_DB))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT id, first_name, last_name, organization, phone, phone_normalized, email, imported_at
            FROM contacts
            WHERE lower(
                COALESCE(first_name, '') || ' ' ||
                COALESCE(last_name, '') || ' ' ||
                COALESCE(organization, '') || ' ' ||
                COALESCE(phone, '') || ' ' ||
                COALESCE(phone_normalized, '') || ' ' ||
                COALESCE(email, '')
            ) LIKE ?
            ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE, organization COLLATE NOCASE
            LIMIT ?
            """,
            (needle, max(1, min(limit, 1000))),
        ).fetchall()
    finally:
        conn.close()


def command_contacts_search(args: argparse.Namespace) -> int:
    rows = _search_contacts(args.query, limit=args.limit)
    if args.json:
        _print_json({"contacts": [dict(row) for row in rows]})
    else:
        print(f"Contacts ({len(rows)}):")
        for row in rows:
            print(f"  {_contact_display(row)}")
    return 0


def command_contacts_refresh(_args: argparse.Namespace) -> int:
    import_contacts.CACHE_DB = CACHE_DB
    return import_contacts.main()


def _escape_applescript_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _as_applescript_text(value: str) -> str:
    return f'"{_escape_applescript_text(value)}"'


def _build_contact_create_script(
    *,
    first_name: str = "",
    last_name: str = "",
    organization: str = "",
    phones: Iterable[str] = (),
    emails: Iterable[str] = (),
    phone_label: str = "mobile",
    email_label: str = "home",
) -> str:
    properties: list[str] = []
    if first_name.strip():
        properties.append(f"first name:{_as_applescript_text(first_name.strip())}")
    if last_name.strip():
        properties.append(f"last name:{_as_applescript_text(last_name.strip())}")
    if organization.strip():
        properties.append(f"organization:{_as_applescript_text(organization.strip())}")

    make_person = "make new person"
    if properties:
        make_person += f" with properties {{{', '.join(properties)}}}"

    lines = ['tell application "Contacts"', f"    set newPerson to {make_person}"]
    for phone in phones:
        cleaned = phone.strip()
        if cleaned:
            lines.append(
                "    make new phone at end of phones of newPerson with properties "
                f"{{label:{_as_applescript_text(phone_label)}, value:{_as_applescript_text(cleaned)}}}"
            )
    for email in emails:
        cleaned = email.strip()
        if cleaned:
            lines.append(
                "    make new email at end of emails of newPerson with properties "
                f"{{label:{_as_applescript_text(email_label)}, value:{_as_applescript_text(cleaned)}}}"
            )
    lines.extend(["    save", "    return id of newPerson", "end tell"])
    return "\n".join(lines)


def _run_osascript(script: str, *, timeout: float = 30.0) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ToolError((result.stderr or "osascript failed").strip())
    return (result.stdout or "").strip()


def command_contacts_create(args: argparse.Namespace) -> int:
    phones = [phone for phone in args.phone if phone.strip()]
    emails = [email for email in args.email if email.strip()]
    has_identity = any([args.first_name.strip(), args.last_name.strip(), args.organization.strip(), phones, emails])
    if not has_identity:
        raise ToolError("Provide at least one name, organization, phone, or email field.")

    script = _build_contact_create_script(
        first_name=args.first_name,
        last_name=args.last_name,
        organization=args.organization,
        phones=phones,
        emails=emails,
        phone_label=args.phone_label,
        email_label=args.email_label,
    )
    if args.dry_run:
        print(script)
        return 0

    person_id = _run_osascript(script, timeout=args.timeout)
    print(f"created_contact_id: {person_id or 'unknown'}")
    print("Run `./scripts/penguin_connect_tool.py contacts refresh` to update PenguinConnect's local cache.")
    return 0


def _build_group_draft(participants: Iterable[str], message: str = "") -> str:
    cleaned = [participant.strip() for participant in participants if participant.strip()]
    body = (message or "").strip()
    if body:
        return f"To: {', '.join(cleaned)}\n\n{body}\n"
    return f"To: {', '.join(cleaned)}\n"


def _messages_address_url(participants: Iterable[str]) -> str:
    recipient_line = ", ".join(participant.strip() for participant in participants if participant.strip())
    return "sms://open?" + urllib.parse.urlencode(
        {"addresses": recipient_line},
        quote_via=urllib.parse.quote,
    )


def _copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise ToolError("Failed to copy draft to clipboard.") from exc


def command_group_compose(args: argparse.Namespace) -> int:
    participants = [participant.strip() for participant in args.participant if participant.strip()]
    if len(participants) < 2:
        raise ToolError("A group draft needs at least two participants.")
    message = args.message
    if args.message_file:
        message = Path(args.message_file).expanduser().read_text(encoding="utf-8")

    draft = _build_group_draft(participants, message)
    messages_url = _messages_address_url(participants)
    if args.copy:
        _copy_to_clipboard(draft)
        print("copied_group_draft: true")
    if args.open_addressed:
        try:
            subprocess.run(["open", messages_url], check=True)
        except subprocess.CalledProcessError as exc:
            raise ToolError("Failed to open addressed Messages compose.") from exc
        print("opened_addressed_messages: true")
    if args.open_messages:
        try:
            subprocess.run(["open", "-a", "Messages"], check=True)
        except subprocess.CalledProcessError as exc:
            raise ToolError("Failed to open Messages.app.") from exc
        print("opened_messages: true")

    if args.json:
        _print_json({"participants": participants, "message": message, "draft": draft, "messages_url": messages_url})
    else:
        print(draft.rstrip())
        print("")
        print(f"Addressed URL: {messages_url}")
        print("")
        print(
            "Note: Messages scripting can send to existing chats, but does not expose a reliable "
            "make-new-group-chat API. This command stages a draft; send it from Messages to create the group."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interact with PenguinConnect and local macOS messaging data.")
    parser.add_argument("--api-base", default=resolve_local_api_base(), help="Local bridge URL")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP or AppleScript timeout in seconds")

    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show local bridge health")
    status.add_argument("--json", action="store_true", help="Print raw JSON")
    status.set_defaults(func=command_status)

    search = sub.add_parser("search", help="Search bridge conversations and/or Apple Messages chats")
    search.add_argument("query", nargs="?", default="", help="Search text")
    search.add_argument("--source", choices=["bridge", "imessage", "both"], default="bridge")
    search.add_argument("--limit", type=int, default=25)
    search.add_argument("--json", action="store_true", help="Print raw JSON")
    search.set_defaults(func=command_search)

    message_search = sub.add_parser(
        "message-search",
        aliases=["search-messages"],
        help="Search message text in the local bridge cache and/or Apple Messages",
    )
    message_search.add_argument("query", help="Message text, sender, participant, or attachment metadata to search")
    message_search.add_argument("--source", choices=["bridge", "imessage", "both"], default="bridge")
    message_search.add_argument("--limit", type=int, default=25)
    message_search.add_argument("--json", action="store_true", help="Print raw JSON")
    message_search.set_defaults(func=command_message_search)

    messages = sub.add_parser("messages", help="Show cached messages for a bridge conversation")
    messages.add_argument("conversation_id")
    messages.add_argument("--limit", type=int, default=30)
    messages.add_argument("--body-chars", type=int, default=160)
    messages.add_argument("--json", action="store_true", help="Print raw JSON")
    messages.set_defaults(func=command_messages)

    send = sub.add_parser("send", help="Send through an existing PenguinConnect conversation")
    send.add_argument("conversation_id")
    send.add_argument("-f", "--from", dest="sender_email", default="", help="Optional local sender metadata")
    send.add_argument("-m", "--message", default="", help="Message text")
    send.add_argument("--message-file", help="Read message text from a UTF-8 file")
    send.add_argument(
        "-a",
        "--attachment",
        "--voice-memo",
        action="append",
        default=[],
        dest="attachment_paths",
        help="Attach a local file path; repeat for multiple files. Audio voice memos are supported.",
    )
    send.add_argument("--json", action="store_true", help="Print raw JSON")
    send.set_defaults(func=command_send)

    contacts = sub.add_parser("contacts", help="Search, refresh, or create macOS Contacts")
    contacts_sub = contacts.add_subparsers(dest="contacts_command", required=True)

    contacts_search = contacts_sub.add_parser("search", help="Search PenguinConnect's cached contacts")
    contacts_search.add_argument("query")
    contacts_search.add_argument("--limit", type=int, default=25)
    contacts_search.add_argument("--json", action="store_true", help="Print raw JSON")
    contacts_search.set_defaults(func=command_contacts_search)

    contacts_refresh = contacts_sub.add_parser("refresh", help="Refresh PenguinConnect's Contacts cache")
    contacts_refresh.set_defaults(func=command_contacts_refresh)

    contacts_create = contacts_sub.add_parser("create", help="Create a local macOS Contact")
    contacts_create.add_argument("--first", dest="first_name", default="")
    contacts_create.add_argument("--last", dest="last_name", default="")
    contacts_create.add_argument("--organization", default="")
    contacts_create.add_argument("--phone", action="append", default=[])
    contacts_create.add_argument("--email", action="append", default=[])
    contacts_create.add_argument("--phone-label", default="mobile")
    contacts_create.add_argument("--email-label", default="home")
    contacts_create.add_argument("--dry-run", action="store_true", help="Print AppleScript without changing Contacts")
    contacts_create.set_defaults(func=command_contacts_create)

    group = sub.add_parser("group", help="Stage new group-message drafts")
    group_sub = group.add_subparsers(dest="group_command", required=True)
    group_compose = group_sub.add_parser("compose", aliases=["create", "start"], help="Stage a new group draft")
    group_compose.add_argument("-p", "--participant", action="append", default=[], help="Phone number or iMessage email")
    group_compose.add_argument("-m", "--message", default="", help="Draft message text")
    group_compose.add_argument("--message-file", help="Read draft text from a UTF-8 file")
    group_compose.add_argument("--copy", action="store_true", help="Copy the draft to the clipboard")
    group_compose.add_argument("--open-addressed", action="store_true", help="Open a Messages compose URL with recipients")
    group_compose.add_argument("--open-messages", action="store_true", help="Open Messages.app")
    group_compose.add_argument("--json", action="store_true", help="Print raw JSON")
    group_compose.set_defaults(func=command_group_compose)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ToolError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[error] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
