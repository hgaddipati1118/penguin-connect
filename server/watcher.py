"""Polling watcher for the PenguinConnect bridge."""

from __future__ import annotations

import os
import random
import threading
from datetime import datetime, timedelta, timezone

from action_log import log_action

_sync_status = {
    "penguin_connect": {
        "last_sync": None,
        "polling": False,
        "last_poll_started_at": None,
        "last_poll_result_at": None,
        "watcher_thread_alive": False,
        "watchdog_thread_alive": False,
        "watchdog_restart_count": 0,
        "last_watchdog_restart_at": None,
    }
}
_status_lock = threading.Lock()
_shutdown_event = threading.Event()
_thread: threading.Thread | None = None
_watchdog_thread: threading.Thread | None = None
_last_error_code: str | None = None
_next_contacts_refresh_at: datetime | None = None
_poller_generation = 0


def _update_sync_status() -> None:
    with _status_lock:
        _sync_status["penguin_connect"]["last_sync"] = datetime.now(timezone.utc).isoformat()


def _mark_poll_started() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with _status_lock:
        _sync_status["penguin_connect"]["last_poll_started_at"] = now_iso
        _sync_status["penguin_connect"]["watcher_thread_alive"] = True


def _mark_poll_result() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with _status_lock:
        _sync_status["penguin_connect"]["last_poll_result_at"] = now_iso
        _sync_status["penguin_connect"]["watcher_thread_alive"] = True


def _set_thread_health(*, watcher_alive: bool | None = None, watchdog_alive: bool | None = None) -> None:
    with _status_lock:
        if watcher_alive is not None:
            _sync_status["penguin_connect"]["watcher_thread_alive"] = watcher_alive
        if watchdog_alive is not None:
            _sync_status["penguin_connect"]["watchdog_thread_alive"] = watchdog_alive


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


def _watchdog_stale_after_seconds(interval: int) -> int:
    raw = os.environ.get("PENGUIN_CONNECT_WATCHDOG_STALE_AFTER_SECONDS", "").strip()
    if raw:
        try:
            value = int(raw)
        except Exception:
            value = 0
        if value > 0:
            return max(60, min(value, 3600))
    return max(interval * 12, 600)


def _watchdog_check_interval_seconds(interval: int) -> int:
    return max(10, min(interval, 60))


def _schedule_next_contacts_refresh(now: datetime | None = None) -> str:
    global _next_contacts_refresh_at
    from penguin_connect import DEFAULT_CONTACT_REFRESH_MINUTES_MAX, DEFAULT_CONTACT_REFRESH_MINUTES_MIN

    current = now or datetime.now(timezone.utc)
    delay_minutes = random.randint(DEFAULT_CONTACT_REFRESH_MINUTES_MIN, DEFAULT_CONTACT_REFRESH_MINUTES_MAX)
    _next_contacts_refresh_at = current + timedelta(minutes=delay_minutes)
    return _next_contacts_refresh_at.isoformat()


def _maybe_refresh_contacts(*, force: bool = False) -> dict:
    global _next_contacts_refresh_at
    now = datetime.now(timezone.utc)
    if not force and _next_contacts_refresh_at and now < _next_contacts_refresh_at:
        return {
            "success": True,
            "skipped": True,
            "reason": "not_due",
            "next_run_at": _next_contacts_refresh_at.isoformat(),
        }

    try:
        from penguin_connect import refresh_contacts_and_repair_display_names

        result = refresh_contacts_and_repair_display_names()
    except Exception as exc:
        result = {"success": False, "error": str(exc).strip() or exc.__class__.__name__}

    result["next_run_at"] = _schedule_next_contacts_refresh()
    return result


def refresh_contacts_now() -> dict:
    return _maybe_refresh_contacts(force=True)


def _poller_should_exit(generation: int) -> bool:
    return _shutdown_event.is_set() or generation != _poller_generation


def _restart_polling_thread(reason: str) -> bool:
    global _thread, _poller_generation
    if _shutdown_event.is_set():
        return False
    if _thread and _thread.is_alive():
        return False

    _poller_generation += 1
    generation = _poller_generation
    _thread = threading.Thread(
        target=_penguin_connect_polling_loop,
        args=(generation,),
        daemon=True,
        name=f"penguin-connect-poller-{generation}",
    )
    _thread.start()
    restart_at = datetime.now(timezone.utc).isoformat()
    with _status_lock:
        info = _sync_status["penguin_connect"]
        info["polling"] = True
        info["watcher_thread_alive"] = True
        info["watchdog_restart_count"] = int(info.get("watchdog_restart_count") or 0) + 1
        info["last_watchdog_restart_at"] = restart_at
    log_action("watcher_restarted", reason=reason, generation=generation)
    return True


def _watchdog_loop() -> None:
    interval = _poll_interval_seconds()
    stale_after = _watchdog_stale_after_seconds(interval)
    check_interval = _watchdog_check_interval_seconds(interval)
    _set_thread_health(watchdog_alive=True)

    while not _shutdown_event.wait(check_interval):
        alive = bool(_thread and _thread.is_alive())
        _set_thread_health(watcher_alive=alive, watchdog_alive=True)

        if alive:
            last_activity_iso = None
            with _status_lock:
                info = _sync_status["penguin_connect"]
                last_activity_iso = info.get("last_poll_result_at") or info.get("last_poll_started_at")
            last_activity_dt = None
            if last_activity_iso:
                try:
                    last_activity_dt = datetime.fromisoformat(last_activity_iso)
                except Exception:
                    last_activity_dt = None
            if last_activity_dt and (datetime.now(timezone.utc) - last_activity_dt).total_seconds() <= stale_after:
                continue
            if last_activity_dt is None:
                continue

        _restart_polling_thread("thread_dead_or_stale")

    _set_thread_health(watchdog_alive=False)


def _penguin_connect_polling_loop(generation: int | None = None) -> None:
    global _last_error_code
    current_generation = _poller_generation if generation is None else generation
    interval = _poll_interval_seconds()
    initial_delay = _poll_initial_delay_seconds(interval)

    if _shutdown_event.wait(initial_delay):
        _set_thread_health(watcher_alive=False)
        return

    while not _poller_should_exit(current_generation):
        try:
            from penguin_connect import run_incremental_sync

            _mark_poll_started()
            log_action("watcher_poll_tick", interval_seconds=interval)
            result = run_incremental_sync()
            _mark_poll_result()
            log_action(
                "watcher_poll_result",
                success=bool(result.get("success")),
                skipped=bool(result.get("skipped")),
                reason=result.get("reason"),
                error=result.get("error"),
                queue_job_id=result.get("queue_job_id"),
            )
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
            _mark_poll_result()
            log_action("watcher_poll_exception", error=str(exc).strip() or exc.__class__.__name__)
            print(f"[Watcher] PenguinConnect polling error: {exc}")

        refresh_result = _maybe_refresh_contacts()
        log_action(
            "contacts_refresh_tick",
            success=bool(refresh_result.get("success")),
            skipped=bool(refresh_result.get("skipped")),
            reason=refresh_result.get("reason"),
            error=refresh_result.get("error"),
            next_run_at=refresh_result.get("next_run_at"),
            contacts_count=refresh_result.get("contacts_count"),
            display_names_updated=refresh_result.get("display_names_updated"),
        )
        if not refresh_result.get("success") and refresh_result.get("error"):
            print(f"[Watcher] PenguinConnect contacts refresh warning: {refresh_result.get('error')}")

        _shutdown_event.wait(interval)

    _set_thread_health(watcher_alive=False)


def start_watchers() -> None:
    global _thread, _watchdog_thread, _poller_generation
    _shutdown_event.clear()
    if _next_contacts_refresh_at is None:
        _schedule_next_contacts_refresh()

    if _thread and _thread.is_alive() and _watchdog_thread and _watchdog_thread.is_alive():
        return

    _poller_generation += 1
    generation = _poller_generation
    _thread = threading.Thread(
        target=_penguin_connect_polling_loop,
        args=(generation,),
        daemon=True,
        name=f"penguin-connect-poller-{generation}",
    )
    _thread.start()
    if not _watchdog_thread or not _watchdog_thread.is_alive():
        _watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True, name="penguin-connect-watchdog")
        _watchdog_thread.start()

    with _status_lock:
        _sync_status["penguin_connect"]["polling"] = True
        _sync_status["penguin_connect"]["watcher_thread_alive"] = True
        _sync_status["penguin_connect"]["watchdog_thread_alive"] = True

    log_action("watcher_started", poll_interval_seconds=_poll_interval_seconds())
    print("[Watcher] PenguinConnect: polling every PENGUIN_CONNECT_POLL_SECONDS")


def stop_watchers() -> None:
    global _thread, _watchdog_thread
    _shutdown_event.set()

    if _thread:
        try:
            _thread.join(timeout=5)
        except Exception:
            pass

    if _watchdog_thread:
        try:
            _watchdog_thread.join(timeout=5)
        except Exception:
            pass

    _thread = None
    _watchdog_thread = None
    with _status_lock:
        info = _sync_status["penguin_connect"]
        info["polling"] = False
        info["watcher_thread_alive"] = False
        info["watchdog_thread_alive"] = False

    log_action("watcher_stopped")
    print("[Watcher] Stopped")


def get_sync_status() -> dict:
    with _status_lock:
        return {k: dict(v) for k, v in _sync_status.items()}
