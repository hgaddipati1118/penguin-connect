"""Startup readiness checks for PenguinConnect."""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

from db import APPLE_MESSAGES_DB, get_connection
from penguin_connect import _build_gmail_service, get_connected_account


class StartupReadinessError(RuntimeError):
    def __init__(self, failures: list[dict[str, Any]]):
        self.failures = failures
        summary = "; ".join(f"{item['name']}: {item['detail']}" for item in failures)
        super().__init__(summary)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _allow_missing_gmail_startup() -> bool:
    return _env_flag("PENGUIN_CONNECT_ALLOW_MISSING_GMAIL_STARTUP", default=False)


def _check_apple_messages_access() -> dict[str, Any]:
    if not APPLE_MESSAGES_DB.exists():
        return {
            "name": "apple_messages",
            "ok": False,
            "blocking": True,
            "reason": "imessage_db_missing",
            "detail": f"Apple Messages DB is missing at {APPLE_MESSAGES_DB}.",
        }

    try:
        conn = sqlite3.connect(f"file:{APPLE_MESSAGES_DB}?mode=ro", uri=True)
        try:
            conn.execute("SELECT COUNT(*) FROM chat").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        reason = "imessage_db_unreadable"
        return {
            "name": "apple_messages",
            "ok": False,
            "blocking": True,
            "reason": reason,
            "detail": f"Apple Messages DB is not readable at {APPLE_MESSAGES_DB}: {exc}",
        }

    return {
        "name": "apple_messages",
        "ok": True,
        "blocking": True,
        "reason": None,
        "detail": f"Apple Messages DB readable at {APPLE_MESSAGES_DB}.",
    }


def _check_gmail_access(conn: sqlite3.Connection, allow_missing_gmail: bool) -> dict[str, Any]:
    account = get_connected_account(conn)
    if not account:
        return {
            "name": "gmail",
            "ok": False,
            "blocking": not allow_missing_gmail,
            "reason": "gmail_not_connected",
            "detail": "No connected Gmail account is configured for the bridge.",
        }

    gmail_email = account["gmail_email"]
    gmail_service, err = _build_gmail_service(gmail_email, account["keychain_service"])
    if gmail_service is not None and not err:
        return {
            "name": "gmail",
            "ok": True,
            "blocking": True,
            "reason": None,
            "detail": f"Gmail OAuth is ready for {gmail_email}.",
        }

    detail = {
        "gmail_not_connected": f"Gmail OAuth token is missing for {gmail_email}.",
        "invalid_keychain_token_json": f"Gmail OAuth token stored in Keychain for {gmail_email} is invalid.",
        "failed_to_initialize_gmail_service": f"Gmail API client could not be initialized for {gmail_email}.",
    }.get(err or "", f"Gmail startup check failed for {gmail_email}: {err}")

    return {
        "name": "gmail",
        "ok": False,
        "blocking": True,
        "reason": err or "gmail_not_connected",
        "detail": detail,
    }


def get_startup_readiness(
    conn: sqlite3.Connection | None = None,
    *,
    allow_missing_gmail: bool | None = None,
) -> dict[str, Any]:
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    if allow_missing_gmail is None:
        allow_missing_gmail = _allow_missing_gmail_startup()

    try:
        checks = [
            _check_apple_messages_access(),
            _check_gmail_access(conn, allow_missing_gmail),
        ]
    finally:
        if close_conn:
            conn.close()

    blocking_failures = [item for item in checks if not item["ok"] and item.get("blocking", True)]
    return {
        "ok": not blocking_failures,
        "allow_missing_gmail_startup": allow_missing_gmail,
        "checks": checks,
    }


def assert_startup_ready(
    conn: sqlite3.Connection | None = None,
    *,
    allow_missing_gmail: bool | None = None,
) -> dict[str, Any]:
    readiness = get_startup_readiness(conn, allow_missing_gmail=allow_missing_gmail)
    failures = [item for item in readiness["checks"] if not item["ok"] and item.get("blocking", True)]
    if failures:
        raise StartupReadinessError(failures)
    return readiness


def _suggested_fix(failure: dict[str, Any]) -> str:
    reason = failure.get("reason")
    if reason in {"imessage_db_missing", "imessage_db_unreadable"}:
        return (
            "Grant Full Disk Access to Terminal.app and confirm "
            f"{APPLE_MESSAGES_DB} is present and readable."
        )
    if reason == "gmail_not_connected":
        return (
            "Finish first-run setup with ./scripts/penguin_connect_setup.py --gmail <you@gmail.com> "
            "or temporarily start with PENGUIN_CONNECT_ALLOW_MISSING_GMAIL_STARTUP=1 only during setup."
        )
    if reason in {"invalid_keychain_token_json", "failed_to_initialize_gmail_service"}:
        return "Reconnect Gmail with ./scripts/penguin_connect_connect.py --gmail <you@gmail.com>."
    return "Review the startup configuration and rerun the bridge."


def main() -> int:
    readiness = get_startup_readiness()
    blocking_failures = [item for item in readiness["checks"] if not item["ok"] and item.get("blocking", True)]
    allowed_failures = [item for item in readiness["checks"] if not item["ok"] and not item.get("blocking", True)]

    for failure in blocking_failures:
        print(f"[startup-check] {failure['detail']}", file=sys.stderr)
        print(f"[startup-check] Fix: {_suggested_fix(failure)}", file=sys.stderr)

    for failure in allowed_failures:
        print(
            "[startup-check] Allowing startup without Gmail because "
            "PENGUIN_CONNECT_ALLOW_MISSING_GMAIL_STARTUP=1 is set.",
            file=sys.stderr,
        )
        print(f"[startup-check] {failure['detail']}", file=sys.stderr)

    return 0 if not blocking_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
