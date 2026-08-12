#!/usr/bin/env python3
"""Install, inspect, and connect PenguinConnect's authenticated remote MCP."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from penguin_connect_mcp_auth import (
    connection_token,
    daily_access_code,
    ensure_daily_code_secret,
    ensure_token,
    load_token,
)
from penguin_connect_mcp_config import (
    RemoteAccessPolicy,
    policy_for_profile,
    save_remote_policy,
)

QUICK_TUNNEL_PATTERN = re.compile(
    r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com"
    r"(?![a-z0-9.-])",
    re.IGNORECASE,
)
TAILSCALE_FUNNEL_PORT = 10000
TAILSCALE_APP_PATHS = (
    Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
    Path.home() / "Applications/Tailscale.app/Contents/MacOS/Tailscale",
)


def default_data_dir() -> Path:
    configured = os.environ.get("PENGUIN_CONNECT_DATA_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / "penguinconnect-local-bridge-data"


def default_endpoint_state_path() -> Path:
    configured = os.environ.get("PENGUIN_CONNECT_ENDPOINT_STATE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "PenguinConnect"
        / "remote-endpoint.json"
    )


def normalize_public_origin(value: str) -> str:
    """Validate a bare public HTTPS origin and discard only a trailing slash."""
    parsed = urllib.parse.urlsplit((value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("The public MCP address must be a bare HTTPS origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The public MCP address has an invalid port") from exc
    hostname = parsed.hostname.lower().rstrip(".")
    if not hostname or hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("The public MCP address must use a public hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname or hostname.endswith((".local", ".internal", ".localhost")):
            raise ValueError("The public MCP address must use a public hostname")
    else:
        if not address.is_global:
            raise ValueError("The public MCP address must not use a private IP address")
    authority = hostname if port in {None, 443} else f"{hostname}:{port}"
    return f"https://{authority}"


def extract_quick_tunnel_url(log_text: str) -> str:
    matches = QUICK_TUNNEL_PATTERN.findall(log_text or "")
    return normalize_public_origin(matches[-1]) if matches else ""


def build_connection_bundle(
    origin: str,
    token: str,
    policy: RemoteAccessPolicy,
    *,
    daily_code: str | None = None,
) -> dict[str, Any]:
    if not token:
        raise ValueError("The remote MCP token is unavailable")
    return {
        "name": "PenguinConnect",
        "server_url": f"{normalize_public_origin(origin)}/mcp",
        "token": connection_token(token, daily_code),
        "transport": "streamable_http",
        "profile": policy.profile,
        "scopes": list(policy.scopes),
        "providers": list(policy.providers),
    }


def save_endpoint_state(
    origin: str,
    policy: RemoteAccessPolicy,
    *,
    tunnel: str = "cloudflare-quick",
    path: Path | None = None,
) -> Path:
    """Save non-secret endpoint metadata atomically; the bearer stays in Keychain."""
    state_path = path or default_endpoint_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "origin": normalize_public_origin(origin),
        "profile": policy.profile,
        "scopes": list(policy.scopes),
        "providers": list(policy.providers),
        "tunnel": tunnel,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, state_path)
        os.chmod(state_path, 0o600)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return state_path


def load_endpoint_state(path: Path | None = None) -> tuple[str, RemoteAccessPolicy]:
    state_path = path or default_endpoint_state_path()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        policy = policy_for_profile(str(payload.get("profile") or ""))
        return normalize_public_origin(str(payload.get("origin") or "")), policy
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("No valid remote endpoint is saved; run setup first") from exc


def endpoint_tunnel_kind(path: Path | None = None) -> str:
    state_path = path or default_endpoint_state_path()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        tunnel = str(payload.get("tunnel") or "cloudflare-quick")
    except (OSError, TypeError, json.JSONDecodeError):
        return "cloudflare-quick"
    return tunnel if tunnel in {"tailscale", "cloudflare-quick"} else "cloudflare-quick"


def copy_connection_bundle(origin: str, token: str, policy: RemoteAccessPolicy) -> None:
    bundle = build_connection_bundle(
        origin,
        token,
        policy,
        daily_code=daily_access_code(),
    )
    completed = subprocess.run(
        ["/usr/bin/pbcopy"],
        input=json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Could not copy the connection bundle to the clipboard")


def wait_for_tunnel_origin(timeout_seconds: int = 45) -> str:
    configured = os.environ.get("PENGUIN_CONNECT_PUBLIC_MCP_URL", "").strip()
    if configured:
        return normalize_public_origin(configured)
    log_path = default_data_dir() / "logs" / "remote-tunnel.err.log"
    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        try:
            origin = extract_quick_tunnel_url(log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            origin = ""
        if origin:
            return origin
        time.sleep(1)
    raise RuntimeError(f"The HTTPS tunnel did not publish an address; inspect {log_path}")


def find_tailscale_binary() -> Path:
    configured = os.environ.get("PENGUIN_CONNECT_TAILSCALE_BIN", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    discovered = shutil.which("tailscale")
    if discovered:
        candidates.append(Path(discovered))
    candidates.extend(TAILSCALE_APP_PATHS)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(
        "Tailscale is not installed. Install and sign in to Tailscale, then try again: "
        "https://tailscale.com/download/mac"
    )


def tailscale_public_origin(binary: Path) -> str:
    completed = subprocess.run(
        [str(binary), "status", "--json"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Tailscale is installed but not signed in or running")
    try:
        payload = json.loads(completed.stdout)
        hostname = str(payload["Self"]["DNSName"]).strip().rstrip(".").lower()
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Tailscale did not report a stable device hostname") from exc
    if not hostname.endswith(".ts.net"):
        raise RuntimeError("Tailscale returned an unexpected device hostname")
    return normalize_public_origin(f"https://{hostname}:{TAILSCALE_FUNNEL_PORT}")


def start_tailscale_funnel() -> str:
    binary = find_tailscale_binary()
    origin = tailscale_public_origin(binary)
    completed = subprocess.run(
        [
            str(binary),
            "funnel",
            "--bg",
            "--yes",
            f"--https={TAILSCALE_FUNNEL_PORT}",
            "8765",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Tailscale Funnel could not start: {detail or 'unknown error'}")
    return origin


def stop_tailscale_funnel() -> None:
    try:
        binary = find_tailscale_binary()
    except RuntimeError:
        return
    subprocess.run(
        [
            str(binary),
            "funnel",
            f"--https={TAILSCALE_FUNNEL_PORT}",
            "8765",
            "off",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )


def _run_installer(name: str) -> None:
    path = SCRIPTS_DIR / name
    completed = subprocess.run(
        ["/bin/bash", str(path)],
        cwd=ROOT_DIR,
        timeout=240,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{name} failed")


def _stop_and_disable_launch_agent(label: str) -> None:
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["/bin/launchctl", "bootout", f"{domain}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    subprocess.run(
        ["/bin/launchctl", "disable", f"{domain}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )


def setup_remote(profile_name: str, tunnel: str) -> str:
    if sys.platform != "darwin":
        raise RuntimeError("PenguinConnect remote setup currently requires macOS")
    policy = policy_for_profile(profile_name)
    save_remote_policy(policy)
    token, _created = ensure_token()
    ensure_daily_code_secret()
    _run_installer("install_launchd_penguin_connect_bridge.sh")
    _run_installer("install_launchd_whatsapp_bridge.sh")
    _run_installer("install_launchd_remote_mcp.sh")
    if tunnel == "tailscale":
        _stop_and_disable_launch_agent("com.penguinconnect.remote-tunnel")
        origin = start_tailscale_funnel()
    elif tunnel == "cloudflare-quick":
        if endpoint_tunnel_kind() == "tailscale":
            stop_tailscale_funnel()
        _run_installer("install_launchd_remote_tunnel.sh")
        origin = wait_for_tunnel_origin()
    else:
        raise ValueError("Unknown tunnel provider")
    save_endpoint_state(origin, policy, tunnel=tunnel)
    copy_connection_bundle(origin, token, policy)
    return origin


def stop_remote() -> None:
    if endpoint_tunnel_kind() == "tailscale":
        stop_tailscale_funnel()
    for label in ("com.penguinconnect.remote-tunnel", "com.penguinconnect.remote-mcp"):
        _stop_and_disable_launch_agent(label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("slashy", "read-only", "whatsapp"),
        default="slashy",
        help="Remote capability profile (default: slashy)",
    )
    parser.add_argument(
        "--tunnel",
        choices=("tailscale", "cloudflare-quick"),
        default="tailscale",
        help="Public HTTPS provider (default: stable Tailscale Funnel)",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--status", action="store_true", help="Show the saved endpoint and profile"
    )
    actions.add_argument(
        "--copy", action="store_true", help="Copy the saved Slashy connection bundle"
    )
    actions.add_argument(
        "--stop", action="store_true", help="Stop the remote tunnel and MCP services"
    )
    args = parser.parse_args()

    try:
        if args.stop:
            stop_remote()
            print("[ok] Remote PenguinConnect services stopped.")
            return 0
        if args.status or args.copy:
            origin, policy = load_endpoint_state()
            if args.copy:
                token = load_token()
                if not token:
                    raise RuntimeError("The Keychain token is missing; run setup again")
                copy_connection_bundle(origin, token, policy)
                print("[ok] Slashy MCP connection bundle copied to the clipboard.")
            print(f"Endpoint: {origin}/mcp")
            print(f"Profile: {policy.profile} ({', '.join(policy.scopes)})")
            return 0

        origin = setup_remote(args.profile, args.tunnel)
        policy = policy_for_profile(args.profile)
        print("[ok] PenguinConnect remote MCP is running.")
        print(f"Endpoint: {origin}/mcp")
        print(f"Profile: {policy.profile} ({', '.join(policy.scopes)})")
        print(
            "[ok] Slashy MCP connection bundle copied to the clipboard; "
            "paste it into MCP Settings."
        )
        print("The bearer token was not printed and remains stored in macOS Keychain.")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
