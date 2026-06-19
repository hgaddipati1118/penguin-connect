"""FastAPI app for the macOS-local PenguinConnect bridge."""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from action_log import action_log_path, log_action
from penguin_connect import (
    connect_gmail_account as penguinconnect_connect_gmail_account,
    get_cached_sync_metrics as penguinconnect_get_cached_sync_metrics,
    disconnect_conversation as penguinconnect_disconnect_conversation,
    get_conversation_alias as penguinconnect_get_conversation_alias,
    get_conversation_messages as penguinconnect_get_conversation_messages,
    get_gmail_connection_status as penguinconnect_get_gmail_connection_status,
    get_runtime_sync_status as penguinconnect_get_runtime_sync_status,
    list_conversations as penguinconnect_list_conversations,
    reconnect_conversation as penguinconnect_reconnect_conversation,
    run_startup_catchup as penguinconnect_run_startup_catchup,
    send_manual_message as penguinconnect_send_manual_message,
    sync_conversations as penguinconnect_sync_conversations,
)
from db import DB_PATH, get_connection, init_db
from startup_checks import StartupReadinessError, assert_startup_ready
from watcher import get_sync_status, refresh_contacts_now, start_watchers, stop_watchers


def _startup_catchup_batch_pause_seconds() -> float:
    raw = (os.environ.get("PENGUIN_CONNECT_STARTUP_CATCHUP_BATCH_PAUSE_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else 5.0
    except Exception:
        value = 5.0
    return max(1.0, min(value, 60.0))

UI_DIR = Path(__file__).resolve().parent / "ui"
DEFAULT_UI_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_UI_ATTACHMENT_TOTAL_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_CODEX_PROMPT_MAX_CHARS = 24_000
DEFAULT_CODEX_TIMEOUT_SECONDS = 90

class PenguinConnectGmailConnectRequest(BaseModel):
    gmail_email: str
    token_json: dict

class PenguinConnectSyncRequest(BaseModel):
    mode: str = "incremental"
    days: int = 7
    hours: int | None = None
    verify_all: bool = False

class PenguinConnectBrowserAttachment(BaseModel):
    filename: str
    mime_type: str = ""
    data_base64: str
    size: int | None = None

class PenguinConnectSendRequest(BaseModel):
    sender_email: str = ""
    message: str = ""
    attachment_paths: list[str] | None = None
    attachments: list[PenguinConnectBrowserAttachment] | None = None

class PenguinConnectContactCreateRequest(BaseModel):
    first_name: str = ""
    last_name: str = ""
    organization: str = ""
    phones: list[str] | None = None
    emails: list[str] | None = None
    phone_label: str = "mobile"
    email_label: str = "home"
    refresh_after: bool = True

class PenguinConnectDraftCreateRequest(BaseModel):
    participants: list[str] | None = None
    message: str = ""
    copy_to_clipboard: bool = True
    open_messages: bool = True

class PenguinConnectCodexAskRequest(BaseModel):
    prompt: str = ""

class PenguinConnectReadStateRequest(BaseModel):
    unread: bool = False

class PenguinConnectConversationManagementRequest(BaseModel):
    pinned: bool | None = None
    archived: bool | None = None
    title: str | None = None
    note: str | None = None
    labels: list[str] | None = None
    draft_text: str | None = None
    follow_up_at: str | None = None

class PenguinConnectMessageManagementRequest(BaseModel):
    provider_message_id: str = ""
    starred: bool | None = None
    note: str | None = None

def _map_sqlite_error(exc: sqlite3.OperationalError) -> HTTPException:
    msg = str(exc).lower()
    if "unable to open database file" in msg:
        return HTTPException(status_code=503, detail="imessage_db_unreadable")
    if "locked" in msg or "busy" in msg:
        return HTTPException(status_code=503, detail="database_busy_retry")
    return HTTPException(status_code=500, detail="database_error")

def _poll_seconds() -> int:
    raw = os.environ.get("PENGUIN_CONNECT_POLL_SECONDS", "30")
    return int(raw)

def _apply_runtime_sync_status(sync_status: dict) -> dict:
    runtime = penguinconnect_get_runtime_sync_status()
    sync_status.setdefault("penguin_connect", {}).update(runtime)
    return sync_status


def _ui_attachment_max_bytes() -> int:
    raw = (os.environ.get("PENGUIN_CONNECT_UI_ATTACHMENT_MAX_BYTES") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_UI_ATTACHMENT_MAX_BYTES
    except Exception:
        value = DEFAULT_UI_ATTACHMENT_MAX_BYTES
    return max(1024, min(value, 100 * 1024 * 1024))


def _ui_attachment_total_max_bytes() -> int:
    raw = (os.environ.get("PENGUIN_CONNECT_UI_ATTACHMENT_TOTAL_MAX_BYTES") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_UI_ATTACHMENT_TOTAL_MAX_BYTES
    except Exception:
        value = DEFAULT_UI_ATTACHMENT_TOTAL_MAX_BYTES
    return max(1024, min(value, 200 * 1024 * 1024))


def _safe_ui_attachment_filename(filename: str, fallback_index: int) -> str:
    candidate = Path((filename or "").strip()).name
    if not candidate:
        candidate = f"attachment-{fallback_index}"
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", candidate).strip(" .")
    return (safe or f"attachment-{fallback_index}")[:200]


def _decode_ui_attachment_data(raw_value: str) -> bytes:
    raw = (raw_value or "").strip()
    if "," in raw and raw[:80].lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    raw = "".join(raw.split())
    if not raw:
        return b""
    raw += "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(raw.encode("utf-8"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_attachment_data") from exc


def _stage_ui_attachments(attachments: list[PenguinConnectBrowserAttachment] | None) -> tuple[list[str], Path | None]:
    if not attachments:
        return [], None
    max_bytes = _ui_attachment_max_bytes()
    total_max_bytes = _ui_attachment_total_max_bytes()
    total_bytes = 0
    staged_dir = Path(tempfile.mkdtemp(prefix="penguinconnect-ui-attachments-"))
    staged_paths: list[str] = []
    try:
        for idx, attachment in enumerate(attachments, 1):
            data = _decode_ui_attachment_data(attachment.data_base64)
            if not data:
                raise HTTPException(status_code=400, detail="empty_attachment")
            declared_size = int(attachment.size or len(data))
            if len(data) > max_bytes or declared_size > max_bytes:
                raise HTTPException(status_code=413, detail="attachment_too_large")
            total_bytes += len(data)
            if total_bytes > total_max_bytes:
                raise HTTPException(status_code=413, detail="attachments_too_large")

            filename = _safe_ui_attachment_filename(attachment.filename, idx)
            out_path = staged_dir / filename
            if out_path.exists():
                out_path = staged_dir / f"{out_path.stem or 'attachment'}-{idx}{out_path.suffix}"
            out_path.write_bytes(data)
            staged_paths.append(str(out_path))
    except Exception:
        shutil.rmtree(staged_dir, ignore_errors=True)
        raise
    if not staged_paths:
        shutil.rmtree(staged_dir, ignore_errors=True)
        return [], None
    return staged_paths, staged_dir


def _ui_file_response(filename: str, media_type: str) -> Response:
    path = UI_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="ui_asset_not_found")
    return Response(path.read_text(encoding="utf-8"), media_type=media_type)


def _clean_text(value: str | None, *, max_chars: int = 500) -> str:
    return (value or "").strip()[:max_chars]


def _clean_text_values(values: list[str] | None, *, max_count: int = 10, max_chars: int = 500) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        item = _clean_text(value, max_chars=max_chars)
        if item:
            cleaned.append(item)
        if len(cleaned) >= max_count:
            break
    return cleaned


def _escape_applescript_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _as_applescript_text(value: str) -> str:
    return f'"{_escape_applescript_text(value)}"'


def _run_osascript(script: str, *, timeout: float = 30.0) -> str:
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=501, detail="osascript_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="osascript_timeout") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail="osascript_failed")
    return (result.stdout or "").strip()


def _build_contact_create_script(
    *,
    first_name: str = "",
    last_name: str = "",
    organization: str = "",
    phones: list[str] | None = None,
    emails: list[str] | None = None,
    phone_label: str = "mobile",
    email_label: str = "home",
) -> str:
    properties: list[str] = []
    if first_name:
        properties.append(f"first name:{_as_applescript_text(first_name)}")
    if last_name:
        properties.append(f"last name:{_as_applescript_text(last_name)}")
    if organization:
        properties.append(f"organization:{_as_applescript_text(organization)}")

    make_person = "make new person"
    if properties:
        make_person += f" with properties {{{', '.join(properties)}}}"

    lines = ['tell application "Contacts"', f"    set newPerson to {make_person}"]
    for phone in phones or []:
        lines.append(
            "    make new phone at end of phones of newPerson with properties "
            f"{{label:{_as_applescript_text(phone_label)}, value:{_as_applescript_text(phone)}}}"
        )
    for email in emails or []:
        lines.append(
            "    make new email at end of emails of newPerson with properties "
            f"{{label:{_as_applescript_text(email_label)}, value:{_as_applescript_text(email)}}}"
        )
    lines.extend(["    save", "    return id of newPerson", "end tell"])
    return "\n".join(lines)


def _create_contact(req: PenguinConnectContactCreateRequest) -> str:
    first_name = _clean_text(req.first_name, max_chars=160)
    last_name = _clean_text(req.last_name, max_chars=160)
    organization = _clean_text(req.organization, max_chars=240)
    phones = _clean_text_values(req.phones, max_count=10, max_chars=160)
    emails = _clean_text_values(req.emails, max_count=10, max_chars=240)
    phone_label = _clean_text(req.phone_label, max_chars=40) or "mobile"
    email_label = _clean_text(req.email_label, max_chars=40) or "home"

    if not any([first_name, last_name, organization, phones, emails]):
        raise HTTPException(status_code=400, detail="contact_requires_identity")

    script = _build_contact_create_script(
        first_name=first_name,
        last_name=last_name,
        organization=organization,
        phones=phones,
        emails=emails,
        phone_label=phone_label,
        email_label=email_label,
    )
    return _run_osascript(script, timeout=30.0) or "unknown"


def _build_messages_draft(participants: list[str], message: str = "") -> str:
    body = _clean_text(message, max_chars=50000)
    if body:
        return f"To: {', '.join(participants)}\n\n{body}\n"
    return f"To: {', '.join(participants)}\n"


def _copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=10.0)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=501, detail="pbcopy_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="clipboard_timeout") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail="clipboard_failed") from exc


def _open_messages_app() -> None:
    try:
        subprocess.run(["open", "-a", "Messages"], check=True, timeout=10.0)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=501, detail="open_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="open_messages_timeout") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail="open_messages_failed") from exc


def _codex_prompt_max_chars() -> int:
    raw = (os.environ.get("PENGUIN_CONNECT_CODEX_MAX_PROMPT_CHARS") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_CODEX_PROMPT_MAX_CHARS
    except Exception:
        value = DEFAULT_CODEX_PROMPT_MAX_CHARS
    return max(1_000, min(value, 100_000))


def _codex_timeout_seconds() -> float:
    raw = (os.environ.get("PENGUIN_CONNECT_CODEX_TIMEOUT_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_CODEX_TIMEOUT_SECONDS
    except Exception:
        value = DEFAULT_CODEX_TIMEOUT_SECONDS
    return max(5.0, min(value, 300.0))


def _run_codex_prompt(prompt: str) -> dict:
    prompt_text = (prompt or "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="codex_prompt_required")

    max_chars = _codex_prompt_max_chars()
    if len(prompt_text) > max_chars:
        raise HTTPException(status_code=413, detail="codex_prompt_too_large")

    codex_bin = shutil.which(os.environ.get("PENGUIN_CONNECT_CODEX_BIN", "codex"))
    if not codex_bin:
        raise HTTPException(status_code=501, detail="codex_cli_unavailable")

    with tempfile.TemporaryDirectory(prefix="penguinconnect-codex-") as tmp:
        tmp_path = Path(tmp)
        output_path = tmp_path / "answer.txt"
        command = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--cd",
            str(tmp_path),
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                input=prompt_text,
                capture_output=True,
                text=True,
                timeout=_codex_timeout_seconds(),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=501, detail="codex_cli_unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="codex_timeout") from exc

        answer = ""
        if output_path.exists():
            answer = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if not answer:
            answer = (result.stdout or "").strip()
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail="codex_failed")
        if not answer:
            raise HTTPException(status_code=400, detail="codex_empty_response")

        return {"success": True, "answer": answer, "prompt_chars": len(prompt_text)}


def _contact_display_name(row: sqlite3.Row) -> str:
    name = " ".join(part for part in [row["first_name"], row["last_name"]] if part).strip()
    return name or row["organization"] or row["email"] or row["phone"] or "Contact"


def _contact_primary_handle(row: sqlite3.Row) -> str:
    return row["email"] or row["phone"] or row["phone_normalized"] or ""


def _contact_phone_search_key(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _contact_handle_type(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        return "email"
    if len(_contact_phone_search_key(text)) >= 7:
        return "phone"
    return "handle"


def _contact_compare_key(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "@" in text:
        return f"email:{text}"
    digits = _contact_phone_search_key(text)
    if len(digits) >= 7:
        return f"phone:{digits}"
    return f"handle:{text}"


def _contact_row_keys(row: sqlite3.Row) -> set[str]:
    return {
        key
        for key in (
            _contact_compare_key(row["email"] or ""),
            _contact_compare_key(row["phone"] or ""),
            _contact_compare_key(row["phone_normalized"] or ""),
        )
        if key
    }


def _all_contact_keys(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT phone, phone_normalized, email FROM contacts"
    ).fetchall()
    keys: set[str] = set()
    for row in rows:
        keys.update(_contact_row_keys(row))
    return keys


def _contact_to_dict(row: sqlite3.Row) -> dict:
    display_name = _contact_display_name(row)
    primary_handle = _contact_primary_handle(row)
    return {
        "id": row["id"],
        "display_name": display_name,
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
        "organization": row["organization"] or "",
        "phone": row["phone"] or "",
        "phone_normalized": row["phone_normalized"] or "",
        "email": row["email"] or "",
        "primary_handle": primary_handle,
        "handle_type": "email" if row["email"] else ("phone" if row["phone"] or row["phone_normalized"] else ""),
        "imported_at": row["imported_at"] or "",
        "source": "contacts",
        "is_saved": True,
    }


def _conversation_participant_handles(row: sqlite3.Row) -> list[str]:
    values: list[str] = []
    try:
        parsed = json.loads(row["participants"] or "[]")
    except Exception:
        parsed = []
    if isinstance(parsed, list):
        values.extend(str(value or "").strip() for value in parsed)
    source_identifier = str(row["source_chat_identifier"] or "").strip()
    if source_identifier and _contact_handle_type(source_identifier) != "handle":
        values.append(source_identifier)

    handles: list[str] = []
    seen: set[str] = set()
    for value in values:
        handle = str(value or "").strip()
        key = _contact_compare_key(handle)
        if not handle or not key or key in seen:
            continue
        seen.add(key)
        handles.append(handle)
    return handles


def _participant_handle_matches_query(handle: str, query: str) -> bool:
    clean_query = str(query or "").strip().lower()
    if not clean_query:
        return False
    clean_handle = str(handle or "").strip().lower()
    if clean_query in clean_handle:
        return True
    query_digits = _contact_phone_search_key(clean_query)
    handle_digits = _contact_phone_search_key(clean_handle)
    return len(query_digits) >= 3 and len(handle_digits) >= 7 and query_digits in handle_digits


def _conversation_participant_contact_results(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    existing_keys: set[str],
    include_all: bool = False,
) -> list[dict]:
    if (not query and not include_all) or limit <= 0:
        return []
    rows = conn.execute(
        """
        SELECT conversation_id, display_name, source_chat_identifier, participants
        FROM penguin_connect_conversations
        ORDER BY updated_at DESC, display_name COLLATE NOCASE, conversation_id
        """
    ).fetchall()
    results: list[dict] = []
    seen = set(existing_keys)
    for row in rows:
        conversation_name = (row["display_name"] or row["source_chat_identifier"] or "Conversation").strip()
        for handle in _conversation_participant_handles(row):
            key = _contact_compare_key(handle)
            if not key or key in seen:
                continue
            if query and not _participant_handle_matches_query(handle, query):
                continue
            seen.add(key)
            handle_type = _contact_handle_type(handle)
            results.append(
                {
                    "id": f"conversation:{key}",
                    "display_name": handle,
                    "first_name": "",
                    "last_name": "",
                    "organization": f"Seen in {conversation_name}" if conversation_name else "Conversation participant",
                    "phone": handle if handle_type == "phone" else "",
                    "phone_normalized": _contact_phone_search_key(handle) if handle_type == "phone" else "",
                    "email": handle if handle_type == "email" else "",
                    "primary_handle": handle,
                    "handle_type": handle_type,
                    "imported_at": "",
                    "source": "conversation",
                    "is_saved": False,
                    "conversation_id": row["conversation_id"],
                    "conversation_name": conversation_name,
                }
            )
            if len(results) >= limit:
                return results
    return results


def _search_contacts(conn: sqlite3.Connection, search: str, *, limit: int, source: str = "all") -> dict:
    query = (search or "").strip()
    normalized_source = (source or "all").strip().lower()
    if normalized_source not in {"all", "contacts", "participants"}:
        normalized_source = "all"
    pattern = f"%{query.lower()}%"
    where = ""
    params: list[object] = []
    if query:
        phone_query = _contact_phone_search_key(query)
        conditions = [
            """lower(
                COALESCE(first_name, '') || ' ' ||
                COALESCE(last_name, '') || ' ' ||
                COALESCE(organization, '') || ' ' ||
                COALESCE(phone, '') || ' ' ||
                COALESCE(phone_normalized, '') || ' ' ||
                COALESCE(email, '')
            ) LIKE ?""",
        ]
        params.append(pattern)
        if len(phone_query) >= 3:
            conditions.append("COALESCE(phone_normalized, '') LIKE ?")
            params.append(f"%{phone_query}%")
        where = """
            WHERE ({conditions})
        """.format(conditions=" OR ".join(conditions))
    limit_value = max(1, min(limit, 100))
    rows = []
    if normalized_source in {"all", "contacts"}:
        params.append(limit_value)
        rows = conn.execute(
            f"""
            SELECT id, first_name, last_name, organization, phone, phone_normalized, email, imported_at
            FROM contacts
            {where}
            ORDER BY
                last_name COLLATE NOCASE,
                first_name COLLATE NOCASE,
                organization COLLATE NOCASE,
                email COLLATE NOCASE,
                phone COLLATE NOCASE
            LIMIT ?
            """,
            params,
        ).fetchall()
    contact_items = [_contact_to_dict(row) for row in rows]
    participant_items: list[dict] = []
    if normalized_source in {"all", "participants"}:
        existing_keys = _all_contact_keys(conn)
        participant_items = _conversation_participant_contact_results(
            conn,
            query,
            limit=max(0, limit_value - len(contact_items)),
            existing_keys=existing_keys,
            include_all=normalized_source == "participants",
        )
    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    return {
        "query": query,
        "source": normalized_source,
        "count": len(contact_items) + len(participant_items),
        "total_contacts": total_contacts,
        "participant_count": len(participant_items),
        "contacts": [*contact_items, *participant_items],
    }


def _message_metadata(raw_value: str | None) -> dict:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _message_attachment_path(raw_path: str) -> Path:
    value = (raw_path or "").strip()
    if not value:
        raise HTTPException(status_code=404, detail="attachment_file_not_found")
    expanded = Path(value).expanduser()
    if expanded.is_absolute():
        candidate = expanded
    elif value.startswith("Library/Messages/Attachments/"):
        candidate = Path.home() / value
    else:
        candidate = Path.home() / "Library" / "Messages" / "Attachments" / value
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="attachment_file_not_found")
    return candidate


def _stored_message_attachment(
    conn: sqlite3.Connection,
    conversation_id: str,
    provider_message_id: str,
    attachment_index: int,
) -> tuple[Path, str, str]:
    if attachment_index < 0:
        raise HTTPException(status_code=404, detail="attachment_not_found")
    row = conn.execute(
        """SELECT metadata
           FROM penguin_connect_messages
           WHERE conversation_id = ? AND provider_message_id = ?
           LIMIT 1""",
        (conversation_id, provider_message_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="message_not_found")
    metadata = _message_metadata(row["metadata"])
    attachments = metadata.get("attachments") if isinstance(metadata.get("attachments"), list) else []
    if attachment_index >= len(attachments) or not isinstance(attachments[attachment_index], dict):
        raise HTTPException(status_code=404, detail="attachment_not_found")

    attachment = attachments[attachment_index]
    raw_path = ""
    for key in ("filename", "path", "file_path", "local_path"):
        raw_path = str(attachment.get(key) or "").strip()
        if raw_path:
            break
    path = _message_attachment_path(raw_path)
    display_name = _safe_ui_attachment_filename(
        str(attachment.get("transfer_name") or path.name or "attachment"),
        attachment_index + 1,
    )
    media_type = str(attachment.get("mime_type") or "").strip()
    if not media_type:
        media_type = mimetypes.guess_type(display_name)[0] or "application/octet-stream"
    return path, display_name, media_type


def _search_messages(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    view: str = "all",
    conversation_id: str = "",
) -> dict:
    search = (query or "").strip()
    normalized_view = (view or "all").strip().lower()
    if normalized_view not in {"all", "current", "unread", "files", "audio", "mine"}:
        normalized_view = "all"
    target_conversation_id = (conversation_id or "").strip()
    if not search and normalized_view == "all":
        return {"query": "", "view": normalized_view, "conversation_id": "", "count": 0, "messages": []}
    if normalized_view == "current" and not target_conversation_id:
        return {"query": search, "view": normalized_view, "conversation_id": "", "count": 0, "messages": []}

    conditions: list[str] = []
    params: list[object] = []
    if search:
        conditions.append(
            """lower(
                COALESCE(c.conversation_id, '') || ' ' ||
                COALESCE(c.display_name, '') || ' ' ||
                COALESCE(cm.title, '') || ' ' ||
                COALESCE(cm.note, '') || ' ' ||
                COALESCE(cm.labels, '') || ' ' ||
                COALESCE(c.source_provider, '') || ' ' ||
                COALESCE(c.source_chat_identifier, '') || ' ' ||
                COALESCE(c.participants, '') || ' ' ||
                COALESCE(m.sender_email, '') || ' ' ||
                COALESCE(m.sender_name, '') || ' ' ||
                COALESCE(m.subject, '') || ' ' ||
                COALESCE(m.body_text, '') || ' ' ||
                COALESCE(m.metadata, '')
            ) LIKE ?"""
        )
        params.append(f"%{search.lower()}%")

    if normalized_view == "current":
        conditions.append("c.conversation_id = ?")
        params.append(target_conversation_id)
    elif normalized_view == "unread":
        conditions.append("COALESCE(m.is_read, 0) = 0")
    elif normalized_view == "files":
        conditions.append(
            """(
                COALESCE(m.metadata, '') LIKE '%"attachments"%'
                OR COALESCE(m.metadata, '') LIKE '%manual_attachment_count%'
            )"""
        )
    elif normalized_view == "audio":
        conditions.append(
            """(
                lower(COALESCE(m.metadata, '')) LIKE '%audio/%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%.aac%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%.aif%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%.aiff%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%.caf%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%.m4a%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%.mp3%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%.wav%'
                OR lower(COALESCE(m.metadata, '')) LIKE '%voice memo%'
            )"""
        )
    elif normalized_view == "mine":
        conditions.append("m.direction IN ('manual_to_imessage', 'email_to_imessage')")

    where_clause = " AND ".join(f"({condition})" for condition in conditions) or "1 = 1"
    params.append(max(1, min(limit, 100)))

    rows = conn.execute(
        f"""
        SELECT
            c.conversation_id,
            COALESCE(cm.title, '') AS title,
            COALESCE(cm.labels, '[]') AS labels,
            c.display_name,
            c.source_provider,
            c.source_service_name,
            c.chat_type,
            c.source_chat_identifier,
            c.participants,
            m.provider,
            m.provider_message_id,
            m.direction,
            m.sender_email,
            m.sender_name,
            m.subject,
            m.body_text,
            m.message_timestamp,
            m.is_read,
            m.metadata,
            m.gmail_message_id,
            m.gmail_thread_id
        FROM penguin_connect_messages m
        JOIN penguin_connect_conversations c
          ON c.conversation_id = m.conversation_id
        LEFT JOIN penguin_connect_conversation_management cm
          ON cm.conversation_id = c.conversation_id
        WHERE {where_clause}
        ORDER BY m.message_timestamp DESC, m.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    messages = []
    for row in rows:
        metadata = _message_metadata(row["metadata"])
        item = dict(row)
        item["metadata"] = metadata
        item["labels"] = _parse_management_labels(row["labels"])
        item["is_read"] = bool(row["is_read"])
        if isinstance(metadata.get("attachments"), list):
            item["attachments"] = metadata["attachments"]
        messages.append(item)
    return {
        "query": search,
        "view": normalized_view,
        "conversation_id": target_conversation_id if normalized_view == "current" else "",
        "count": len(messages),
        "messages": messages,
    }


def _conversation_unread_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT conversation_id, COUNT(*) as unread_count
        FROM penguin_connect_messages
        WHERE COALESCE(is_read, 0) = 0
        GROUP BY conversation_id
        """
    ).fetchall()
    return {row["conversation_id"]: int(row["unread_count"] or 0) for row in rows}


def _attach_conversation_unread_counts(conn: sqlite3.Connection, result: dict) -> dict:
    counts = _conversation_unread_counts(conn)
    for conversation in result.get("conversations") or []:
        if isinstance(conversation, dict):
            unread_count = counts.get(conversation.get("conversation_id"), 0)
            conversation["unread_count"] = unread_count
            conversation["has_unread"] = unread_count > 0
    return result


def _preview_attachment_name(attachment: object) -> str:
    if not isinstance(attachment, dict):
        return "attachment"
    for key in ("transfer_name", "filename", "mime_type"):
        value = str(attachment.get(key) or "").strip()
        if value:
            return Path(value).name
    return "attachment"


def _conversation_preview_text(body_text: str | None, metadata: dict) -> tuple[str, bool]:
    text = " ".join((body_text or "").split())
    attachments = metadata.get("attachments") if isinstance(metadata.get("attachments"), list) else []
    has_attachments = bool(attachments)
    if text:
        return text[:180], has_attachments
    if has_attachments:
        names = [_preview_attachment_name(attachment) for attachment in attachments[:2]]
        suffix = f" +{len(attachments) - 2}" if len(attachments) > 2 else ""
        return f"attachment: {', '.join(names)}{suffix}", True
    if metadata.get("manual_attachment_count"):
        return f"attachments: {metadata.get('manual_attachment_count')}", True
    return "", False


def _conversation_preview_sender(row: sqlite3.Row) -> str:
    sender = (row["sender_name"] or row["sender_email"] or "").strip()
    if sender:
        return sender
    return "Me" if row["direction"] in {"manual_to_imessage", "email_to_imessage"} else ""


def _attach_conversation_previews(conn: sqlite3.Connection, result: dict) -> dict:
    conversations = [conversation for conversation in result.get("conversations") or [] if isinstance(conversation, dict)]
    ids = [conversation.get("conversation_id") for conversation in conversations if conversation.get("conversation_id")]
    for conversation in conversations:
        conversation["last_message_preview"] = ""
        conversation["last_message_sender"] = ""
        conversation["last_message_has_attachments"] = False
    if not ids:
        return result

    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM (
            SELECT
                conversation_id,
                provider_message_id,
                direction,
                sender_email,
                sender_name,
                body_text,
                message_timestamp,
                metadata,
                ROW_NUMBER() OVER (
                    PARTITION BY conversation_id
                    ORDER BY message_timestamp DESC, id DESC
                ) as row_number
            FROM penguin_connect_messages
            WHERE conversation_id IN ({placeholders})
        )
        WHERE row_number = 1
        """,
        ids,
    ).fetchall()
    previews = {}
    for row in rows:
        metadata = _message_metadata(row["metadata"])
        preview, has_attachments = _conversation_preview_text(row["body_text"], metadata)
        previews[row["conversation_id"]] = {
            "last_message_provider_id": row["provider_message_id"],
            "last_message_sender": _conversation_preview_sender(row),
            "last_message_preview": preview,
            "last_message_ts": row["message_timestamp"],
            "last_message_has_attachments": has_attachments,
        }

    for conversation in conversations:
        preview = previews.get(conversation.get("conversation_id"))
        if preview:
            conversation.update(preview)
    return result


def _conversation_management_rows(conn: sqlite3.Connection, conversation_ids: list[str]) -> dict[str, dict]:
    ids = [conversation_id for conversation_id in conversation_ids if conversation_id]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""SELECT conversation_id, is_pinned, is_archived, title, note, labels, draft_text, follow_up_at, updated_at
            FROM penguin_connect_conversation_management
            WHERE conversation_id IN ({placeholders})""",
        ids,
    ).fetchall()
    return {
        row["conversation_id"]: {
            "is_pinned": bool(row["is_pinned"]),
            "is_archived": bool(row["is_archived"]),
            "title": row["title"] or "",
            "note": row["note"] or "",
            "labels": _parse_management_labels(row["labels"]),
            "draft_text": row["draft_text"] or "",
            "follow_up_at": row["follow_up_at"] or "",
            "management_updated_at": row["updated_at"],
        }
        for row in rows
    }


def _attach_conversation_management(conn: sqlite3.Connection, result: dict) -> dict:
    conversations = [conversation for conversation in result.get("conversations") or [] if isinstance(conversation, dict)]
    rows = _conversation_management_rows(conn, [conversation.get("conversation_id") for conversation in conversations])
    for conversation in conversations:
        state = rows.get(conversation.get("conversation_id"), {})
        conversation["is_pinned"] = bool(state.get("is_pinned"))
        conversation["is_archived"] = bool(state.get("is_archived"))
        conversation["title"] = state.get("title") or ""
        conversation["note"] = state.get("note") or ""
        conversation["labels"] = state.get("labels") or []
        conversation["draft_text"] = state.get("draft_text") or ""
        conversation["follow_up_at"] = state.get("follow_up_at") or ""
        conversation["management_updated_at"] = state.get("management_updated_at")
    return result


def _parse_management_labels(raw_value: str | None) -> list[str]:
    try:
        parsed = json.loads(raw_value or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return _clean_management_labels([str(value) for value in parsed])


def _clean_management_labels(values: list[str] | None) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        label = re.sub(r"\s+", " ", str(value or "").strip().strip("#")).strip()
        if not label:
            continue
        label = label[:32]
        key = label.lower()
        if key in seen:
            continue
        labels.append(label)
        seen.add(key)
        if len(labels) >= 12:
            break
    return labels


def _clean_management_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:160]


def _clean_management_note(value: str | None) -> str:
    note = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return note[:4000]


def _clean_management_draft(value: str | None) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")[:20000]


def _clean_management_follow_up(value: str | None) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    return cleaned[:64]


def _get_conversation_management(conn: sqlite3.Connection, conversation_id: str) -> dict:
    row = conn.execute(
        """SELECT c.conversation_id, m.is_pinned, m.is_archived, m.title, m.note, m.labels, m.draft_text, m.follow_up_at, m.updated_at
           FROM penguin_connect_conversations c
           LEFT JOIN penguin_connect_conversation_management m
             ON m.conversation_id = c.conversation_id
           WHERE c.conversation_id = ?
           LIMIT 1""",
        (conversation_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return {
        "conversation_id": row["conversation_id"],
        "is_pinned": bool(row["is_pinned"] or 0),
        "is_archived": bool(row["is_archived"] or 0),
        "title": row["title"] or "",
        "note": row["note"] or "",
        "labels": _parse_management_labels(row["labels"]),
        "draft_text": row["draft_text"] or "",
        "follow_up_at": row["follow_up_at"] or "",
        "management_updated_at": row["updated_at"],
    }


def _set_conversation_management(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    pinned: bool | None,
    archived: bool | None,
    title: str | None,
    note: str | None,
    labels: list[str] | None,
    draft_text: str | None,
    follow_up_at: str | None,
) -> dict:
    current = _get_conversation_management(conn, conversation_id)
    is_pinned = current["is_pinned"] if pinned is None else bool(pinned)
    is_archived = current["is_archived"] if archived is None else bool(archived)
    clean_title = current["title"] if title is None else _clean_management_title(title)
    clean_note = current["note"] if note is None else _clean_management_note(note)
    clean_labels = current["labels"] if labels is None else _clean_management_labels(labels)
    clean_draft = current["draft_text"] if draft_text is None else _clean_management_draft(draft_text)
    clean_follow_up = current["follow_up_at"] if follow_up_at is None else _clean_management_follow_up(follow_up_at)
    if archived is True:
        is_pinned = False
    elif pinned is True:
        is_archived = False

    conn.execute(
        """INSERT INTO penguin_connect_conversation_management
           (conversation_id, is_pinned, is_archived, title, note, labels, draft_text, follow_up_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(conversation_id) DO UPDATE SET
             is_pinned = excluded.is_pinned,
             is_archived = excluded.is_archived,
             title = excluded.title,
             note = excluded.note,
             labels = excluded.labels,
             draft_text = excluded.draft_text,
             follow_up_at = excluded.follow_up_at,
             updated_at = datetime('now')""",
        (
            conversation_id,
            1 if is_pinned else 0,
            1 if is_archived else 0,
            clean_title,
            clean_note,
            json.dumps(clean_labels),
            clean_draft,
            clean_follow_up,
        ),
    )
    return _get_conversation_management(conn, conversation_id)


def _set_message_management(
    conn: sqlite3.Connection,
    conversation_id: str,
    provider_message_id: str,
    *,
    starred: bool | None,
    note: str | None = None,
    update_note: bool = False,
) -> dict:
    clean_provider_id = str(provider_message_id or "").strip()[:500]
    if not clean_provider_id:
        raise HTTPException(status_code=400, detail="message_id_required")

    row = conn.execute(
        """SELECT provider_message_id
           FROM penguin_connect_messages
           WHERE conversation_id = ? AND provider_message_id = ?
           LIMIT 1""",
        (conversation_id, clean_provider_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="message_not_found")

    managed = conn.execute(
        """SELECT is_starred, note
           FROM penguin_connect_message_management
           WHERE conversation_id = ? AND provider_message_id = ?
           LIMIT 1""",
        (conversation_id, clean_provider_id),
    ).fetchone()
    current_starred = bool(managed["is_starred"]) if managed else False
    current_note = managed["note"] if managed else ""
    next_starred = current_starred if starred is None else bool(starred)
    next_note = current_note
    if update_note:
        next_note = str(note or "").strip()[:2000]

    conn.execute(
        """INSERT INTO penguin_connect_message_management
           (conversation_id, provider_message_id, is_starred, note, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(conversation_id, provider_message_id) DO UPDATE SET
             is_starred = excluded.is_starred,
             note = excluded.note,
             updated_at = datetime('now')""",
        (conversation_id, clean_provider_id, 1 if next_starred else 0, next_note),
    )
    return {
        "success": True,
        "conversation_id": conversation_id,
        "provider_message_id": clean_provider_id,
        "is_starred": bool(next_starred),
        "message_note": next_note,
        "has_note": bool(next_note),
    }


def _set_conversation_read_state(conn: sqlite3.Connection, conversation_id: str, *, unread: bool) -> dict:
    conv = conn.execute(
        "SELECT conversation_id, status FROM penguin_connect_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not conv:
        return {"success": False, "error": "conversation_not_found"}

    updated = conn.execute(
        "UPDATE penguin_connect_messages SET is_read = ? WHERE conversation_id = ?",
        (0 if unread else 1, conversation_id),
    ).rowcount
    unread_count = conn.execute(
        "SELECT COUNT(*) FROM penguin_connect_messages WHERE conversation_id = ? AND COALESCE(is_read, 0) = 0",
        (conversation_id,),
    ).fetchone()[0]
    return {
        "success": True,
        "conversation_id": conversation_id,
        "updated_messages": updated,
        "unread_count": int(unread_count or 0),
        "has_unread": int(unread_count or 0) > 0,
    }


def _startup_catchup_retry_delay(result: dict, pause_seconds: float) -> float | None:
    if not result.get("success"):
        return None

    if result.get("skipped"):
        retry_after = result.get("retry_after_seconds")
        try:
            retry_seconds = float(retry_after)
        except Exception:
            retry_seconds = 0.0
        if retry_seconds > 0:
            return retry_seconds
        reason = (result.get("reason") or "").strip()
        if reason in {"queue_busy", "initial_backfill_required"}:
            return pause_seconds
        return None

    pending_bootstrap = int(result.get("pending_bootstrap_conversations") or 0)
    pending_full_verify = int(result.get("pending_full_verify_conversations") or 0)
    selected = int(result.get("selected_conversations") or 0)
    wave_advanced = bool(result.get("backfill_wave_advanced"))
    if pending_bootstrap > 0 or pending_full_verify > 0 or selected > 0 or wave_advanced:
        return pause_seconds
    return None

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    conn = get_connection()
    try:
        assert_startup_ready(conn)
    except StartupReadinessError as exc:
        print(f"[PenguinConnect] Startup preflight failed: {exc}")
        raise
    finally:
        conn.close()

    try:
        refresh_result = refresh_contacts_now()
        if not refresh_result.get("success"):
            print(f"[PenguinConnect] Contacts refresh warning: {refresh_result.get('error')}")
    except Exception as exc:
        log_action("contacts_refresh_exception", error=str(exc).strip() or exc.__class__.__name__)
        print(f"[PenguinConnect] Contacts refresh failed: {exc}")

    start_watchers()
    log_action(
        "server_start",
        db_path=str(DB_PATH),
        action_log_path=str(action_log_path()),
        poll_seconds=_poll_seconds(),
    )

    def _run_startup_sync() -> None:
        pause_seconds = _startup_catchup_batch_pause_seconds()
        try:
            while True:
                result = penguinconnect_run_startup_catchup()
                log_action("startup_catchup_result", result=result)
                retry_delay = _startup_catchup_retry_delay(result, pause_seconds)
                if result.get("success"):
                    if retry_delay is not None:
                        if result.get("reason") == "gmail_rate_limited":
                            retry_after = result.get("retry_after_seconds")
                            print(f"[PenguinConnect] Startup catch-up paused for Gmail rate limits ({retry_after}s)")
                        elif result.get("reason") == "initial_backfill_required":
                            print("[PenguinConnect] Startup catch-up waiting for initial backfill")
                        elif result.get("reason") == "backfill_daily_cap_reached":
                            print("[PenguinConnect] Startup catch-up paused after hitting the daily backfill Gmail import cap")
                        elif result.get("reason") == "backfill_rate_limit_guarded":
                            print("[PenguinConnect] Startup catch-up paused because Gmail rate-limit pressure is too high")
                        time.sleep(retry_delay)
                        continue

                    if result.get("skipped"):
                        reason = result.get("reason")
                        if reason == "queue_idle":
                            print("[PenguinConnect] Startup catch-up completed")
                        elif reason not in {"queue_busy", "gmail_rate_limited", "initial_backfill_required"}:
                            print(f"[PenguinConnect] Startup catch-up paused: {reason}")
                        break

                    print("[PenguinConnect] Startup catch-up completed")
                    break

                if result.get("error") != "gmail_not_connected":
                    print(f"[PenguinConnect] Startup catch-up warning: {result.get('error')}")
                break
        except Exception as exc:
            log_action("startup_catchup_exception", error=str(exc).strip() or exc.__class__.__name__)
            print(f"[PenguinConnect] Startup catch-up failed: {exc}")

    threading.Thread(target=_run_startup_sync, daemon=True, name="penguinconnect-startup-catchup").start()

    print(f"[PenguinConnect] API server running - DB at {DB_PATH}")
    yield

    try:
        stop_watchers()
    except Exception:
        pass

    log_action("server_shutdown")
    print("[PenguinConnect] Shutting down")

app = FastAPI(title="PenguinConnect", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3847",
        "http://127.0.0.1:3847",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/penguin-connect/ui", response_class=HTMLResponse)
@app.get("/penguin-connect/ui", response_class=HTMLResponse)
def get_penguinconnect_ui():
    return HTMLResponse((UI_DIR / "index.html").read_text(encoding="utf-8"))

@app.get("/api/penguin-connect/ui/app.css")
@app.get("/penguin-connect/ui/app.css")
def get_penguinconnect_ui_css():
    return _ui_file_response("app.css", "text/css; charset=utf-8")

@app.get("/api/penguin-connect/ui/app.js")
@app.get("/penguin-connect/ui/app.js")
def get_penguinconnect_ui_js():
    return _ui_file_response("app.js", "application/javascript; charset=utf-8")

@app.get("/api/status")
def get_status():
    conn = get_connection()
    try:
        total_conversations = conn.execute("SELECT COUNT(*) FROM penguin_connect_conversations").fetchone()[0]
        active_conversations = conn.execute(
            "SELECT COUNT(*) FROM penguin_connect_conversations WHERE status = 'active' AND COALESCE(exclude_from_sync, 0) = 0"
        ).fetchone()[0]
        gmail = penguinconnect_get_gmail_connection_status(conn)
        sync_metrics = penguinconnect_get_cached_sync_metrics(conn)
    finally:
        conn.close()

    sync_status = _apply_runtime_sync_status(get_sync_status())
    return {
        "service": "penguinconnect-local-bridge",
        "db_path": str(DB_PATH),
        "gmail": gmail,
        "total_conversations": total_conversations,
        "active_conversations": active_conversations,
        "sync_status": sync_status,
        "sync_metrics": sync_metrics,
        "poll_seconds": _poll_seconds(),
    }

@app.get("/api/penguin-connect/gmail/status")
@app.get("/penguin-connect/gmail/status")
def get_penguinconnect_gmail_status():
    conn = get_connection()
    try:
        return penguinconnect_get_gmail_connection_status(conn)
    finally:
        conn.close()

@app.get("/api/penguin-connect/health")
@app.get("/penguin-connect/health")
def get_penguinconnect_health():
    conn = get_connection()
    try:
        gmail = penguinconnect_get_gmail_connection_status(conn)
        conv_rows = conn.execute(
            """
            SELECT status, COUNT(*) as count
            FROM penguin_connect_conversations
            GROUP BY status
            """
        ).fetchall()
        status_counts = {r["status"]: r["count"] for r in conv_rows}
        active = status_counts.get("active", 0)
        disconnected = status_counts.get("disconnected", 0)
        sync_metrics = penguinconnect_get_cached_sync_metrics(conn)
        sync_status = _apply_runtime_sync_status(get_sync_status())

        return {
            "ok": bool(gmail.get("connected")) and active >= 0,
            "gmail": gmail,
            "conversations": {
                "total": active + disconnected,
                "active": active,
                "disconnected": disconnected,
            },
            "poll_seconds": _poll_seconds(),
            "sync_status": sync_status,
            "sync_metrics": sync_metrics,
        }
    finally:
        conn.close()

@app.get("/api/penguin-connect/contacts")
@app.get("/penguin-connect/contacts")
def search_penguinconnect_contacts(search: str = "", limit: int = Query(25, ge=1, le=100), source: str = "all"):
    conn = get_connection()
    try:
        return _search_contacts(conn, search, limit=limit, source=source)
    finally:
        conn.close()

@app.post("/api/penguin-connect/contacts")
@app.post("/penguin-connect/contacts")
def create_penguinconnect_contact(req: PenguinConnectContactCreateRequest):
    contact_id = _create_contact(req)
    refresh_result = None
    if req.refresh_after:
        refresh_result = refresh_contacts_now()
    return {
        "success": True,
        "contact_id": contact_id,
        "refresh": refresh_result,
    }

@app.post("/api/penguin-connect/contacts/refresh")
@app.post("/penguin-connect/contacts/refresh")
def refresh_penguinconnect_contacts():
    result = refresh_contacts_now()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "contacts_refresh_failed"))
    return result

@app.get("/api/penguin-connect/messages/search")
@app.get("/penguin-connect/messages/search")
def search_penguinconnect_messages(
    query: str = "",
    limit: int = Query(25, ge=1, le=100),
    view: str = "all",
    conversation_id: str = "",
):
    conn = get_connection()
    try:
        return _search_messages(conn, query, limit=limit, view=view, conversation_id=conversation_id)
    finally:
        conn.close()

@app.post("/api/penguin-connect/messages/draft")
@app.post("/penguin-connect/messages/draft")
def create_penguinconnect_messages_draft(req: PenguinConnectDraftCreateRequest):
    participants = _clean_text_values(req.participants, max_count=50, max_chars=240)
    if not participants:
        raise HTTPException(status_code=400, detail="draft_requires_participant")

    draft = _build_messages_draft(participants, req.message)
    copied = False
    opened_messages = False
    if req.copy_to_clipboard:
        _copy_to_clipboard(draft)
        copied = True
    if req.open_messages:
        _open_messages_app()
        opened_messages = True
    return {
        "success": True,
        "participants_count": len(participants),
        "draft": draft,
        "copied": copied,
        "opened_messages": opened_messages,
    }

@app.post("/api/penguin-connect/codex/ask")
@app.post("/penguin-connect/codex/ask")
def ask_penguinconnect_codex(req: PenguinConnectCodexAskRequest):
    return _run_codex_prompt(req.prompt)

@app.post("/api/penguin-connect/gmail/connect")
@app.post("/penguin-connect/gmail/connect")
def connect_penguinconnect_gmail(req: PenguinConnectGmailConnectRequest):
    conn = get_connection()
    try:
        result = penguinconnect_connect_gmail_account(conn, req.gmail_email, req.token_json)
        log_action(
            "api_connect_gmail",
            gmail_email=req.gmail_email,
            success=bool(result.get("success")),
            error=result.get("error"),
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "failed_to_connect_gmail"))
        conn.commit()
        return result
    finally:
        conn.close()

@app.get("/api/penguin-connect/conversations")
@app.get("/penguin-connect/conversations")
def get_penguinconnect_conversations():
    conn = get_connection()
    try:
        result = penguinconnect_list_conversations(conn)
        result = _attach_conversation_unread_counts(conn, result)
        result = _attach_conversation_previews(conn, result)
        result = _attach_conversation_management(conn, result)
        conn.commit()
        return result
    except sqlite3.OperationalError as exc:
        raise _map_sqlite_error(exc)
    finally:
        conn.close()

@app.get("/api/penguin-connect/conversations/{conversation_id}/messages")
@app.get("/penguin-connect/conversations/{conversation_id}/messages")
def get_penguinconnect_conversation_messages(conversation_id: str, limit: int = Query(200, ge=1, le=1000)):
    conn = get_connection()
    try:
        result = penguinconnect_get_conversation_messages(conn, conversation_id, limit=limit)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail="conversation_not_found")
        return result
    finally:
        conn.close()


@app.get("/api/penguin-connect/conversations/{conversation_id}/attachments/{attachment_index}")
@app.get("/penguin-connect/conversations/{conversation_id}/attachments/{attachment_index}")
def get_penguinconnect_conversation_attachment(
    conversation_id: str,
    attachment_index: int,
    provider_message_id: str = Query(...),
):
    conn = get_connection()
    try:
        path, display_name, media_type = _stored_message_attachment(
            conn,
            conversation_id,
            provider_message_id,
            attachment_index,
        )
        return FileResponse(path, media_type=media_type, filename=display_name)
    finally:
        conn.close()


@app.post("/api/penguin-connect/conversations/{conversation_id}/management")
@app.post("/penguin-connect/conversations/{conversation_id}/management")
def set_penguinconnect_conversation_management(
    conversation_id: str,
    req: PenguinConnectConversationManagementRequest,
):
    conn = get_connection()
    try:
        result = _set_conversation_management(
            conn,
            conversation_id,
            pinned=req.pinned,
            archived=req.archived,
            title=req.title,
            note=req.note,
            labels=req.labels,
            draft_text=req.draft_text,
            follow_up_at=req.follow_up_at,
        )
        log_action(
            "api_set_conversation_management",
            conversation_id=conversation_id,
            is_pinned=bool(result.get("is_pinned")),
            is_archived=bool(result.get("is_archived")),
            has_title=bool(result.get("title")),
            has_note=bool(result.get("note")),
            label_count=len(result.get("labels") or []),
            has_draft=bool(result.get("draft_text")),
            has_follow_up=bool(result.get("follow_up_at")),
        )
        conn.commit()
        return {"success": True, **result}
    finally:
        conn.close()


@app.post("/api/penguin-connect/conversations/{conversation_id}/read-state")
@app.post("/penguin-connect/conversations/{conversation_id}/read-state")
def set_penguinconnect_conversation_read_state(conversation_id: str, req: PenguinConnectReadStateRequest):
    conn = get_connection()
    try:
        result = _set_conversation_read_state(conn, conversation_id, unread=req.unread)
        log_action(
            "api_set_conversation_read_state",
            conversation_id=conversation_id,
            unread=bool(req.unread),
            success=bool(result.get("success")),
            error=result.get("error"),
            updated_messages=result.get("updated_messages"),
        )
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error", "conversation_not_found"))
        conn.commit()
        return result
    finally:
        conn.close()


@app.post("/api/penguin-connect/conversations/{conversation_id}/messages/management")
@app.post("/penguin-connect/conversations/{conversation_id}/messages/management")
def set_penguinconnect_message_management(
    conversation_id: str,
    req: PenguinConnectMessageManagementRequest,
):
    conn = get_connection()
    try:
        field_set = getattr(req, "model_fields_set", None)
        if field_set is None:
            field_set = getattr(req, "__fields_set__", set())
        field_set = set(field_set)
        result = _set_message_management(
            conn,
            conversation_id,
            req.provider_message_id,
            starred=req.starred if "starred" in field_set else None,
            note=req.note,
            update_note="note" in field_set,
        )
        log_action(
            "api_set_message_management",
            conversation_id=conversation_id,
            provider_message_id=result.get("provider_message_id"),
            is_starred=bool(result.get("is_starred")),
            has_note=bool(result.get("has_note")),
            note_length=len(result.get("message_note") or ""),
        )
        conn.commit()
        return result
    finally:
        conn.close()


@app.get("/api/penguin-connect/conversations/{conversation_id}/alias")
@app.get("/penguin-connect/conversations/{conversation_id}/alias")
def get_penguinconnect_conversation_alias(conversation_id: str):
    conn = get_connection()
    try:
        result = penguinconnect_get_conversation_alias(conn, conversation_id)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail="conversation_not_found")
        return result
    finally:
        conn.close()

@app.post("/api/penguin-connect/conversations/sync")
@app.post("/penguin-connect/conversations/sync")
def sync_penguinconnect_conversations(req: PenguinConnectSyncRequest):
    conn = get_connection()
    try:
        result = penguinconnect_sync_conversations(
            conn,
            mode=req.mode,
            days=req.days or 7,
            hours=req.hours,
            verify_all=req.verify_all,
        )
        log_action(
            "api_sync_request",
            mode=req.mode,
            days=req.days or 7,
            hours=req.hours,
            verify_all=bool(req.verify_all),
            success=bool(result.get("success")),
            skipped=bool(result.get("skipped")),
            reason=result.get("reason"),
            error=result.get("error"),
            queue_job_id=result.get("queue_job_id"),
        )
        if not result.get("success"):
            err = result.get("error")
            if err == "invalid_mode":
                raise HTTPException(status_code=400, detail=err)
            if err in {"imessage_db_unreadable", "database_busy_retry"}:
                raise HTTPException(status_code=503, detail=err)
            raise HTTPException(status_code=400, detail=err or "penguin_connect_sync_failed")
        conn.commit()
        return result
    except sqlite3.OperationalError as exc:
        raise _map_sqlite_error(exc)
    finally:
        conn.close()

@app.post("/api/penguin-connect/conversations/{conversation_id}/disconnect")
@app.post("/penguin-connect/conversations/{conversation_id}/disconnect")
def disconnect_penguinconnect_conversation(conversation_id: str):
    conn = get_connection()
    try:
        result = penguinconnect_disconnect_conversation(conn, conversation_id)
        log_action(
            "api_disconnect_conversation",
            conversation_id=conversation_id,
            success=bool(result.get("success")),
            error=result.get("error"),
        )
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error", "conversation_not_found"))
        conn.commit()
        return result
    finally:
        conn.close()

@app.post("/api/penguin-connect/conversations/{conversation_id}/reconnect")
@app.post("/penguin-connect/conversations/{conversation_id}/reconnect")
def reconnect_penguinconnect_conversation(conversation_id: str):
    conn = get_connection()
    try:
        result = penguinconnect_reconnect_conversation(conn, conversation_id)
        log_action(
            "api_reconnect_conversation",
            conversation_id=conversation_id,
            success=bool(result.get("success")),
            error=result.get("error"),
        )
        if not result.get("success"):
            error = result.get("error", "conversation_not_found")
            if error == "conversation_not_found":
                raise HTTPException(status_code=404, detail=error)
            raise HTTPException(status_code=400, detail=error)
        conn.commit()
        return result
    finally:
        conn.close()

@app.post("/api/penguin-connect/conversations/{conversation_id}/send")
@app.post("/penguin-connect/conversations/{conversation_id}/send")
def send_penguinconnect_conversation_message(conversation_id: str, req: PenguinConnectSendRequest):
    ui_attachment_paths: list[str] = []
    staged_dir: Path | None = None
    conn = get_connection()
    try:
        ui_attachment_paths, staged_dir = _stage_ui_attachments(req.attachments)
        attachment_paths = [str(path) for path in (req.attachment_paths or []) if str(path or "").strip()]
        attachment_paths.extend(ui_attachment_paths)
        result = penguinconnect_send_manual_message(
            conn,
            conversation_id=conversation_id,
            sender_email=req.sender_email,
            body_text=req.message,
            attachment_paths=attachment_paths or None,
        )
        log_action(
            "api_manual_send_request",
            conversation_id=conversation_id,
            sender_email=req.sender_email or None,
            success=bool(result.get("success")),
            error=result.get("error"),
            attachment_count=len(attachment_paths),
        )
        if not result.get("success"):
            if result.get("error") == "sender_not_connected_gmail":
                raise HTTPException(status_code=403, detail="sender_not_connected_gmail")
            if result.get("error") == "gmail_not_connected":
                raise HTTPException(status_code=403, detail="local_sender_not_configured")
            raise HTTPException(status_code=400, detail=result.get("error", "penguin_connect_send_failed"))
        conn.commit()
        return result
    finally:
        if staged_dir:
            shutil.rmtree(staged_dir, ignore_errors=True)
        conn.close()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PENGUIN_CONNECT_PORT", "9000")),
    )
