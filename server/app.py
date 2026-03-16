"""FastAPI app for the macOS-local PenguinConnect bridge."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
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

class PenguinConnectGmailConnectRequest(BaseModel):
    gmail_email: str
    token_json: dict

class PenguinConnectSyncRequest(BaseModel):
    mode: str = "incremental"
    days: int = 7
    hours: int | None = None
    verify_all: bool = False

class PenguinConnectSendRequest(BaseModel):
    sender_email: str
    message: str

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
    if pending_bootstrap > 0 or pending_full_verify > 0 or selected > 0:
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

@app.post("/api/penguin-connect/gmail/connect")
@app.post("/penguin-connect/gmail/connect")
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
@app.post("/api/penguin-connect/conversations/{conversation_id}/send")
@app.post("/penguin-connect/conversations/{conversation_id}/send")
def send_penguinconnect_conversation_message(conversation_id: str, req: PenguinConnectSendRequest):
    conn = get_connection()
    try:
        result = penguinconnect_send_manual_message(
            conn,
            conversation_id=conversation_id,
            sender_email=req.sender_email,
            body_text=req.message,
        )
        log_action(
            "api_manual_send_request",
            conversation_id=conversation_id,
            sender_email=req.sender_email,
            success=bool(result.get("success")),
            error=result.get("error"),
        )
        if not result.get("success"):
            if result.get("error") == "sender_not_connected_gmail":
                raise HTTPException(status_code=403, detail="sender_not_connected_gmail")
            raise HTTPException(status_code=400, detail=result.get("error", "penguin_connect_send_failed"))
        conn.commit()
        return result
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PENGUIN_CONNECT_PORT", "9000")),
    )
