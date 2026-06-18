"""FastAPI app for the macOS-local PenguinConnect bridge."""

from __future__ import annotations

import base64
import binascii
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
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
    sender_email: str
    message: str = ""
    attachment_paths: list[str] | None = None
    attachments: list[PenguinConnectBrowserAttachment] | None = None

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


def _contact_display_name(row: sqlite3.Row) -> str:
    name = " ".join(part for part in [row["first_name"], row["last_name"]] if part).strip()
    return name or row["organization"] or row["email"] or row["phone"] or "Contact"


def _contact_primary_handle(row: sqlite3.Row) -> str:
    return row["email"] or row["phone"] or row["phone_normalized"] or ""


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
    }


def _search_contacts(conn: sqlite3.Connection, search: str, *, limit: int) -> dict:
    query = (search or "").strip()
    pattern = f"%{query.lower()}%"
    where = ""
    params: list[object] = []
    if query:
        where = """
            WHERE lower(
                COALESCE(first_name, '') || ' ' ||
                COALESCE(last_name, '') || ' ' ||
                COALESCE(organization, '') || ' ' ||
                COALESCE(phone, '') || ' ' ||
                COALESCE(phone_normalized, '') || ' ' ||
                COALESCE(email, '')
            ) LIKE ?
        """
        params.append(pattern)
    params.append(max(1, min(limit, 100)))
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
    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    return {
        "query": query,
        "count": len(rows),
        "total_contacts": total_contacts,
        "contacts": [_contact_to_dict(row) for row in rows],
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
def search_penguinconnect_contacts(search: str = "", limit: int = Query(25, ge=1, le=100)):
    conn = get_connection()
    try:
        return _search_contacts(conn, search, limit=limit)
    finally:
        conn.close()

@app.post("/api/penguin-connect/contacts/refresh")
@app.post("/penguin-connect/contacts/refresh")
def refresh_penguinconnect_contacts():
    result = refresh_contacts_now()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "contacts_refresh_failed"))
    return result

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
            sender_email=req.sender_email,
            success=bool(result.get("success")),
            error=result.get("error"),
            attachment_count=len(attachment_paths),
        )
        if not result.get("success"):
            if result.get("error") == "sender_not_connected_gmail":
                raise HTTPException(status_code=403, detail="sender_not_connected_gmail")
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
