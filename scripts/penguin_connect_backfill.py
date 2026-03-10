#!/usr/bin/env python3
"""Run PenguinConnect backfill sync with optional automatic rate-limit waiting."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from penguin_connect_local_api import resolve_local_api_base


def _decode_json_bytes(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {"raw": text[:1000]}
    if isinstance(data, dict):
        return data
    return {"raw": data}


def _call_sync(api_base: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    url = api_base.rstrip("/") + "/penguin-connect/conversations/sync"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = _decode_json_bytes(resp.read())
            if not isinstance(data, dict):
                return {"success": False, "error": "invalid_response_payload"}
            return data
    except urllib.error.HTTPError as exc:
        detail = _decode_json_bytes(exc.read())
        detail_value = detail.get("detail") if isinstance(detail, dict) else detail
        return {
            "success": False,
            "error": f"http_{exc.code}",
            "detail": detail_value or detail,
        }
    except Exception as exc:
        return {"success": False, "error": "sync_request_failed", "detail": str(exc)}


def _rate_limit_wait_seconds(result: dict[str, Any], max_wait_seconds: int) -> int:
    try:
        retry_after = int(result.get("retry_after_seconds") or 0)
    except Exception:
        retry_after = 0
    if retry_after <= 0:
        retry_after = 30
    return max(1, min(retry_after, max_wait_seconds))


def run_backfill(
    api_base: str,
    payload: dict[str, Any],
    *,
    max_attempts: int,
    wait_on_rate_limit: bool,
    max_wait_seconds: int,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        print(f"[info] Backfill attempt {attempt}/{attempts}")
        result = _call_sync(api_base, payload, timeout_seconds)
        print(json.dumps(result, indent=2))

        if result.get("success") and result.get("skipped") and result.get("reason") == "gmail_rate_limited":
            if not wait_on_rate_limit:
                return 2, result
            if attempt >= attempts:
                return 3, result
            wait_seconds = _rate_limit_wait_seconds(result, max_wait_seconds)
            print(f"[wait] Gmail rate limited; sleeping {wait_seconds}s before retry.")
            time.sleep(wait_seconds)
            continue

        return (0, result) if result.get("success") else (1, result)

    return 3, {"success": False, "error": "max_attempts_exceeded"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run controlled PenguinConnect backfill sync")
    parser.add_argument(
        "--api-base",
        default=None,
        help="Local API base URL (defaults to PENGUIN_CONNECT_LOCAL_API_BASE or PENGUIN_CONNECT_PORT)",
    )
    parser.add_argument("--days", type=int, default=30, help="Backfill window in days when using --recent")
    parser.add_argument("--hours", type=int, default=None, help="Optional backfill window in hours")
    parser.add_argument(
        "--recent",
        action="store_true",
        help="Use recent-window backfill instead of full verify-all pass",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=20,
        help="Max API attempts (retries apply only for gmail_rate_limited skips)",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=900,
        help="Upper bound for each rate-limit wait sleep",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=1800,
        help="HTTP timeout for each sync request",
    )
    parser.add_argument(
        "--no-wait-rate-limit",
        action="store_true",
        help="Exit immediately when API reports gmail_rate_limited",
    )
    args = parser.parse_args()

    api_base = (args.api_base or resolve_local_api_base()).strip()
    payload: dict[str, Any] = {
        "mode": "backfill",
        "days": max(1, int(args.days)),
        "verify_all": not bool(args.recent),
    }
    if args.hours is not None:
        payload["hours"] = max(1, int(args.hours))

    print(f"[info] API base: {api_base}")
    print("[info] Payload:")
    print(json.dumps(payload, indent=2))
    code, _result = run_backfill(
        api_base=api_base,
        payload=payload,
        max_attempts=max(1, int(args.max_attempts)),
        wait_on_rate_limit=not bool(args.no_wait_rate_limit),
        max_wait_seconds=max(1, int(args.max_wait_seconds)),
        timeout_seconds=max(5, int(args.timeout_seconds)),
    )
    if code == 0:
        print("[ok] Backfill sync completed.")
    elif code == 2:
        print("[warn] Backfill paused by Gmail rate limits (auto-wait disabled).", file=sys.stderr)
    elif code == 3:
        print("[warn] Backfill stopped after max attempts due Gmail rate limits.", file=sys.stderr)
    else:
        print("[error] Backfill sync failed.", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
