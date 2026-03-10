"""Polling watcher for the PenguinConnect bridge."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

_sync_status = {
    "penguin_connect": {
        "last_sync": None,
        "polling": False,
    }
}
_status_lock = threading.Lock()
_shutdown_event = threading.Event()
_thread: threading.Thread | None = None
_last_error_code: str | None = None


def _update_sync_status() -> None:
    with _status_lock:
        _sync_status["penguin_connect"]["last_sync"] = datetime.now(timezone.utc).isoformat()


def _poll_interval_seconds() -> int:
    interval = int(os.environ.get("PENGUIN_CONNECT_POLL_SECONDS", "30"))
    return max(10, min(interval, 300))


def _poll_initial_delay_seconds(interval: int) -> int:
    raw = os.environ.get("PENGUIN_CONNECT_POLL_INITIAL_DELAY_SECONDS", "")
    try:
        delay = int(raw) if raw else interval
    except Exception:
        delay = interval
    return max(0, min(delay, 300))


def _penguin_connect_polling_loop() -> None:
    global _last_error_code
    interval = _poll_interval_seconds()
    initial_delay = _poll_initial_delay_seconds(interval)

    if _shutdown_event.wait(initial_delay):
        return

    while not _shutdown_event.is_set():
        try:
            from penguin_connect import run_incremental_sync

            result = run_incremental_sync()
            if result.get("success") and not result.get("skipped"):
                _last_error_code = None
                _update_sync_status()
            elif result.get("skipped"):
                err = result.get("reason") or "skipped"
                if err in {"queue_idle", "queue_busy"}:
                    _last_error_code = None
                    _shutdown_event.wait(interval)
                    continue
                if _last_error_code != err:
                    if err == "gmail_rate_limited":
                        retry_after = result.get("retry_after_seconds")
                        print(
                            "[Watcher] PenguinConnect pausing Gmail sync to respect Google rate limits"
                            f" ({retry_after}s)."
                        )
                    else:
                        print("[Watcher] PenguinConnect waiting for initial backfill before polling starts.")
                _last_error_code = err
            else:
                err = result.get("error")
                if err in ("gmail_not_connected", "invalid_keychain_token_json"):
                    pass
                elif err == "imessage_db_unreadable":
                    if _last_error_code != err:
                        print(
                            "[Watcher] PenguinConnect sync blocked: iMessage DB unreadable "
                            "(grant Full Disk Access to Terminal.app)."
                        )
                elif _last_error_code != err:
                    print(f"[Watcher] PenguinConnect incremental sync warning: {err}")
                _last_error_code = err
        except Exception as exc:
            print(f"[Watcher] PenguinConnect polling error: {exc}")

        _shutdown_event.wait(interval)


def start_watchers() -> None:
    global _thread
    _shutdown_event.clear()

    if _thread and _thread.is_alive():
        return

    _thread = threading.Thread(target=_penguin_connect_polling_loop, daemon=True, name="penguin-connect-poller")
    _thread.start()

    with _status_lock:
        _sync_status["penguin_connect"]["polling"] = True

    print("[Watcher] PenguinConnect: polling every PENGUIN_CONNECT_POLL_SECONDS")


def stop_watchers() -> None:
    global _thread
    _shutdown_event.set()

    if _thread:
        try:
            _thread.join(timeout=5)
        except Exception:
            pass

    _thread = None
    with _status_lock:
        _sync_status["penguin_connect"]["polling"] = False

    print("[Watcher] Stopped")


def get_sync_status() -> dict:
    with _status_lock:
        return {k: dict(v) for k, v in _sync_status.items()}
