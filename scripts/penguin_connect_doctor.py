#!/usr/bin/env python3
"""Readiness checks for PenguinConnect local bridge."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
from penguin_connect_local_api import resolve_local_api_base

REPO_ROOT = Path(__file__).resolve().parent.parent
IMESSAGE_DB = Path.home() / "Library" / "Messages" / "chat.db"
_DEFAULT_WHATSAPP_DB = Path.home() / "whatsapp-mcp" / "whatsapp-bridge" / "store" / "messages.db"
_DEFAULT_TELEGRAM_SESSION = Path.home() / "penguin-connect-data" / "telegram.session"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _load_env():
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except Exception:
        pass


def _cache_db_path() -> Path:
    data_dir = Path(
        os.environ.get("PENGUIN_CONNECT_DATA_DIR", str(Path.home() / "penguinconnect-local-bridge-data"))
    ).expanduser()
    return Path(os.environ.get("PENGUIN_CONNECT_DB_PATH", str(data_dir / "cache.db"))).expanduser()


def _check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    return CheckResult("python", ok, f"{major}.{minor}")


def _check_imessage_access() -> CheckResult:
    if not IMESSAGE_DB.exists():
        return CheckResult("imessage_db", False, f"missing: {IMESSAGE_DB}")
    try:
        conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
        count = conn.execute("SELECT COUNT(*) FROM chat").fetchone()[0]
        conn.close()
        return CheckResult("imessage_db", True, f"readable ({count} chats)")
    except Exception as exc:
        return CheckResult("imessage_db", False, f"not readable: {exc}")


def _check_cache_db() -> CheckResult:
    cache_db = _cache_db_path()
    if not cache_db.exists():
        return CheckResult("cache_db", False, f"missing: {cache_db}")
    try:
        conn = sqlite3.connect(str(cache_db))
        conn.execute("SELECT 1").fetchone()
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'penguin_connect_%'"
        ).fetchone()[0]
        conn.close()
        return CheckResult("cache_db", tables >= 5, f"penguin_connect_tables={tables}")
    except Exception as exc:
        return CheckResult("cache_db", False, str(exc))


def _check_backend(api_base: str) -> CheckResult:
    try:
        r = requests.get(api_base.rstrip("/") + "/api/status", timeout=5)
        if r.status_code != 200:
            return CheckResult("api_status", False, f"http {r.status_code}")
        return CheckResult("api_status", True, "reachable")
    except Exception as exc:
        return CheckResult("api_status", False, str(exc))


def _check_gmail_status(api_base: str) -> CheckResult:
    try:
        r = requests.get(api_base.rstrip("/") + "/penguin-connect/gmail/status", timeout=5)
        if r.status_code != 200:
            return CheckResult("gmail_status", False, f"http {r.status_code}")
        data = r.json()
        if not data.get("connected"):
            return CheckResult("gmail_status", False, "not connected")
        addr = data.get("gmail_email") or "unknown"
        return CheckResult("gmail_status", True, f"connected: {addr}")
    except Exception as exc:
        return CheckResult("gmail_status", False, str(exc))


def _check_penguinconnect_conversations(api_base: str) -> CheckResult:
    try:
        r = requests.get(api_base.rstrip("/") + "/penguin-connect/conversations", timeout=8)
        if r.status_code != 200:
            return CheckResult("penguinconnect_conversations", False, f"http {r.status_code}")
        data = r.json()
        if not data.get("connected"):
            return CheckResult("penguinconnect_conversations", False, "gmail not connected")
        convs = data.get("conversations", [])
        active = sum(1 for c in convs if c.get("status") == "active")
        return CheckResult("penguinconnect_conversations", active > 0, f"active={active}, total={len(convs)}")
    except Exception as exc:
        return CheckResult("penguinconnect_conversations", False, str(exc))


def _check_whatsapp_bridge() -> CheckResult:
    db_path = Path(os.environ.get("PENGUIN_CONNECT_WHATSAPP_DB_PATH", str(_DEFAULT_WHATSAPP_DB)))
    if not db_path.exists():
        return CheckResult("whatsapp_bridge", False, f"messages.db not found: {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        count = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        return CheckResult("whatsapp_bridge", True, f"readable ({count} chats, {msg_count} messages)")
    except Exception as exc:
        return CheckResult("whatsapp_bridge", False, f"not readable: {exc}")


def _check_whatsapp_api() -> CheckResult:
    api_url = os.environ.get("PENGUIN_CONNECT_WHATSAPP_API_URL", "http://localhost:8080/api")
    try:
        r = requests.get(api_url.rstrip("/").rsplit("/api", 1)[0] + "/health", timeout=5)
        if r.status_code == 200:
            return CheckResult("whatsapp_api", True, f"reachable at {api_url}")
        return CheckResult("whatsapp_api", False, f"http {r.status_code}")
    except requests.ConnectionError:
        return CheckResult("whatsapp_api", False, f"not reachable at {api_url} — is whatsapp-mcp bridge running?")
    except Exception as exc:
        return CheckResult("whatsapp_api", False, str(exc))


def _check_telegram_auth() -> CheckResult:
    session_path = Path(os.environ.get(
        "PENGUIN_CONNECT_TELEGRAM_SESSION_PATH",
        str(_DEFAULT_TELEGRAM_SESSION).replace(".session", ""),
    ))
    session_file = Path(str(session_path) + ".session") if not str(session_path).endswith(".session") else session_path
    if not session_file.exists():
        return CheckResult("telegram_auth", False, f"session file not found: {session_file}")
    api_id = os.environ.get("PENGUIN_CONNECT_TELEGRAM_API_ID")
    api_hash = os.environ.get("PENGUIN_CONNECT_TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        return CheckResult("telegram_auth", False, "PENGUIN_CONNECT_TELEGRAM_API_ID/API_HASH not set")
    try:
        size = session_file.stat().st_size
        if size < 100:
            return CheckResult("telegram_auth", False, f"session file too small ({size} bytes) — may be invalid")
        return CheckResult("telegram_auth", True, f"session exists ({size} bytes), credentials set")
    except Exception as exc:
        return CheckResult("telegram_auth", False, str(exc))


def _check_required_env() -> CheckResult:
    val = os.environ.get("PENGUIN_CONNECT_POLL_SECONDS", "30")
    try:
        interval = int(val)
        if interval <= 0:
            raise ValueError("must be > 0")
        return CheckResult("penguin_connect_poll_seconds", True, str(interval))
    except Exception:
        return CheckResult("penguin_connect_poll_seconds", False, f"invalid value: {val}")


def run_checks(api_base: str) -> list[CheckResult]:
    checks: list[Callable[[], CheckResult]] = [
        _check_python,
        _check_required_env,
        _check_imessage_access,
        _check_whatsapp_bridge,
        _check_whatsapp_api,
        _check_telegram_auth,
        _check_cache_db,
        lambda: _check_backend(api_base),
        lambda: _check_gmail_status(api_base),
        lambda: _check_penguinconnect_conversations(api_base),
    ]
    return [fn() for fn in checks]


def main() -> int:
    _load_env()
    api_base = resolve_local_api_base()
    results = run_checks(api_base)

    width = max(len(r.name) for r in results)
    print("\nPenguinConnect Doctor\n")
    for r in results:
        mark = "OK" if r.ok else "FAIL"
        print(f"[{mark}] {r.name.ljust(width)}  {r.detail}")

    failed = [r for r in results if not r.ok]
    output = {
        "ok": len(failed) == 0,
        "checks": [r.__dict__ for r in results],
    }

    print("\nJSON:")
    print(json.dumps(output, indent=2))

    if failed:
        print("\nSuggested fixes:")
        for f in failed:
            if f.name == "imessage_db":
                print("- Grant Full Disk Access to Terminal.app and rerun.")
            elif f.name == "cache_db":
                print("- Initialize DB: python3 server/db.py")
            elif f.name == "api_status":
                print("- Start server: ./scripts/run_penguin_connect_bridge.sh")
            elif f.name == "gmail_status":
                print("- Connect Gmail: ./scripts/penguin_connect_connect.py --gmail <you@gmail.com>")
            elif f.name == "penguinconnect_conversations":
                print(
                    "- Run sync: "
                    f"curl -X POST {api_base}/penguin-connect/conversations/sync "
                    "-H 'Content-Type: application/json' "
                    "-d '{\"mode\":\"startup_catchup\"}'"
                )
            elif f.name == "whatsapp_bridge":
                print("- Install and start the whatsapp-mcp bridge (Go): https://github.com/nicebytes/whatsapp-mcp")
                print("  DB path configurable via PENGUIN_CONNECT_WHATSAPP_DB_PATH.")
            elif f.name == "whatsapp_api":
                print("- Start the whatsapp-mcp bridge: cd ~/whatsapp-mcp && go run .")
                print("  API URL configurable via PENGUIN_CONNECT_WHATSAPP_API_URL (default: http://localhost:8080/api).")
            elif f.name == "telegram_auth":
                print("- Set PENGUIN_CONNECT_TELEGRAM_API_ID and PENGUIN_CONNECT_TELEGRAM_API_HASH")
                print("  (from https://my.telegram.org → API development tools)")
                print("  Then run: python scripts/telegram_auth.py")
            elif f.name == "penguin_connect_poll_seconds":
                print("- Set PENGUIN_CONNECT_POLL_SECONDS to a valid integer (recommended: 30).")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
