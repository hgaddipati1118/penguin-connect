#!/usr/bin/env python3
"""Start-only watchdog for the local PenguinConnect bridge."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from penguin_connect_local_api import resolve_local_api_base

REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINAL_LAUNCHER = REPO_ROOT / "scripts" / "open_penguin_connect_bridge_terminal.sh"
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class WatchdogStatus:
    api_base: str
    host: str
    port: int
    listener_alive: bool
    health_reachable: bool
    health_http_status: int | None
    health_ok: bool | None
    should_start: bool
    detail: str


def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_defaults(repo_root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    env_path = repo_root / ".env"
    if not env_path.exists():
        return merged

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in merged:
            continue
        merged[key] = _parse_env_value(value)

    return merged


def _parse_api_host_port(api_base: str) -> tuple[str, int]:
    parsed = urlparse(api_base)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        return host, parsed.port
    if parsed.scheme == "https":
        return host, 443
    return host, 80


def probe_listener(host: str, port: int, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def fetch_health(api_base: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[bool, int | None, bool | None]:
    health_url = api_base.rstrip("/") + "/penguin-connect/health"
    request = None
    try:
        with urlopen(health_url, timeout=timeout_seconds) as response:
            request = response
            payload = json.loads(response.read().decode("utf-8"))
            return True, response.status, bool(payload.get("ok"))
    except HTTPError as exc:
        request = exc
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            health_ok = bool(payload.get("ok")) if isinstance(payload, dict) else None
        except Exception:
            health_ok = None
        return True, exc.code, health_ok
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return False, None, None
    finally:
        if request is not None:
            try:
                request.close()
            except Exception:
                pass


def inspect_bridge_status(
    env: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    *,
    listener_probe: Callable[[str, int, float], bool] = probe_listener,
    health_fetcher: Callable[[str, float], tuple[bool, int | None, bool | None]] = fetch_health,
) -> WatchdogStatus:
    merged_env = load_env_defaults(REPO_ROOT, env)
    api_base = resolve_local_api_base(merged_env)
    host, port = _parse_api_host_port(api_base)
    listener_alive = listener_probe(host, port, timeout_seconds)
    if listener_alive:
        return WatchdogStatus(
            api_base=api_base,
            host=host,
            port=port,
            listener_alive=True,
            health_reachable=False,
            health_http_status=None,
            health_ok=None,
            should_start=False,
            detail="listener_present",
        )

    health_reachable, health_http_status, health_ok = health_fetcher(api_base, timeout_seconds)
    should_start = not health_reachable
    detail = "bridge_missing" if should_start else "health_reachable_without_listener_probe"
    return WatchdogStatus(
        api_base=api_base,
        host=host,
        port=port,
        listener_alive=False,
        health_reachable=health_reachable,
        health_http_status=health_http_status,
        health_ok=health_ok,
        should_start=should_start,
        detail=detail,
    )


def launch_bridge(repo_root: Path, *, allow_missing_gmail_startup: bool = False) -> None:
    cmd = [str(TERMINAL_LAUNCHER)]
    if allow_missing_gmail_startup:
        cmd.append("--allow-missing-gmail-startup")
    subprocess.run(cmd, cwd=repo_root, check=True)


def run_watchdog_once(
    env: dict[str, str] | None = None,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    allow_missing_gmail_startup: bool = False,
    launcher: Callable[..., None] = launch_bridge,
    listener_probe: Callable[[str, int, float], bool] = probe_listener,
    health_fetcher: Callable[[str, float], tuple[bool, int | None, bool | None]] = fetch_health,
) -> WatchdogStatus:
    status = inspect_bridge_status(
        env,
        timeout_seconds,
        listener_probe=listener_probe,
        health_fetcher=health_fetcher,
    )
    if status.should_start:
        launcher(REPO_ROOT, allow_missing_gmail_startup=allow_missing_gmail_startup)
    return status


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ensure the local PenguinConnect bridge is running without killing it.")
    parser.add_argument(
        "--allow-missing-gmail-startup",
        action="store_true",
        help="Pass through the bootstrap-only Gmail startup bypass when launching the bridge.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Socket/HTTP probe timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the watchdog status as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        status = run_watchdog_once(
            timeout_seconds=args.timeout_seconds,
            allow_missing_gmail_startup=args.allow_missing_gmail_startup,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[watchdog] failed to launch bridge: exit={exc.returncode}", file=sys.stderr)
        return exc.returncode or 1

    if args.json:
        print(json.dumps(asdict(status), indent=2, sort_keys=True))
    elif status.should_start:
        print(f"[watchdog] bridge missing on {status.host}:{status.port}; launched Terminal starter")
    else:
        print(f"[watchdog] bridge already present on {status.host}:{status.port} ({status.detail})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
