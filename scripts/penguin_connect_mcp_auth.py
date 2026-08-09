#!/usr/bin/env python3
"""Manage the remote PenguinConnect MCP bearer token in macOS Keychain."""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys

KEYCHAIN_SERVICE = "com.penguinconnect.remote-mcp.bearer-token"
KEYCHAIN_ACCOUNT = "penguin-connect-remote-mcp"
TOKEN_ENV_VAR = "PENGUIN_CONNECT_MCP_BEARER_TOKEN"
LAUNCH_AGENT_LABEL = "com.penguinconnect.remote-mcp"


def _security(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def generate_token() -> str:
    """Generate a URL-safe bearer token with 384 bits of entropy."""
    return secrets.token_urlsafe(48)


def read_keychain_token() -> str:
    result = _security(
        "find-generic-password",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def store_keychain_token(token: str) -> None:
    result = _security(
        "add-generic-password",
        "-U",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
        token,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not store the remote MCP token in macOS Keychain")


def load_token() -> str:
    """Load a development override first, then the macOS Keychain secret."""
    configured = os.environ.get(TOKEN_ENV_VAR, "").strip()
    return configured or read_keychain_token()


def ensure_token() -> tuple[str, bool]:
    existing = read_keychain_token()
    if existing:
        return existing, False
    token = generate_token()
    store_keychain_token(token)
    return token, True


def restart_remote_mcp_service() -> bool:
    target = f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def _copy_token() -> int:
    token = load_token()
    if not token:
        print("[missing] No remote MCP token is stored. Run with --ensure first.", file=sys.stderr)
        return 1
    result = subprocess.run(
        ["pbcopy"],
        input=token,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        print("[error] Could not copy the remote MCP token.", file=sys.stderr)
        return 1
    print("[ok] Remote MCP token copied to the clipboard.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--ensure",
        action="store_true",
        help="Create and store a token only when one is not already present.",
    )
    actions.add_argument(
        "--rotate",
        action="store_true",
        help="Replace the stored token, invalidating remote clients using the old token.",
    )
    actions.add_argument(
        "--copy",
        action="store_true",
        help="Copy the current token to the clipboard without printing it.",
    )
    actions.add_argument(
        "--status",
        action="store_true",
        help="Report whether a token is available without printing it (default).",
    )
    args = parser.parse_args()

    if args.copy:
        return _copy_token()
    if args.rotate:
        store_keychain_token(generate_token())
        if restart_remote_mcp_service():
            print("[ok] Rotated the remote MCP token and restarted the remote MCP service.")
        else:
            print(
                "[ok] Rotated the remote MCP token. Start or reinstall the remote MCP service "
                "before using the endpoint."
            )
        print("Existing remote clients must be updated.")
        return 0
    if args.ensure:
        _, created = ensure_token()
        if created:
            print("[ok] Created the remote MCP token in macOS Keychain.")
        else:
            print("[ok] Remote MCP token already exists; left it unchanged.")
        return 0

    if load_token():
        print("[ok] Remote MCP token is available.")
        return 0
    print("[missing] No remote MCP token is configured.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
