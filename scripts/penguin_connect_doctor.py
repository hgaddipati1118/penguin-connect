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
            elif f.name == "penguin_connect_poll_seconds":
                print("- Set PENGUIN_CONNECT_POLL_SECONDS to a valid integer (recommended: 30).")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
