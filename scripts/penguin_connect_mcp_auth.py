#!/usr/bin/env python3
"""Manage the remote PenguinConnect MCP bearer token in macOS Keychain."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import os
import secrets
import subprocess
import sys

KEYCHAIN_SERVICE = "com.penguinconnect.remote-mcp.bearer-token"
KEYCHAIN_ACCOUNT = "penguin-connect-remote-mcp"
TOKEN_ENV_VAR = "PENGUIN_CONNECT_MCP_BEARER_TOKEN"
DAILY_CODE_KEYCHAIN_SERVICE = "com.penguinconnect.remote-mcp.daily-code-secret"
DAILY_CODE_KEYCHAIN_ACCOUNT = "penguin-connect-remote-mcp-daily-code"
DAILY_CODE_SECRET_ENV_VAR = "PENGUIN_CONNECT_MCP_DAILY_CODE_SECRET"
DAILY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DAILY_CODE_LENGTH = 6
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


def generate_daily_code_secret() -> str:
    """Generate a separate high-entropy secret used only for rotating access codes."""
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


def read_keychain_daily_code_secret() -> str:
    result = _security(
        "find-generic-password",
        "-a",
        DAILY_CODE_KEYCHAIN_ACCOUNT,
        "-s",
        DAILY_CODE_KEYCHAIN_SERVICE,
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


def store_keychain_daily_code_secret(secret: str) -> None:
    result = _security(
        "add-generic-password",
        "-U",
        "-a",
        DAILY_CODE_KEYCHAIN_ACCOUNT,
        "-s",
        DAILY_CODE_KEYCHAIN_SERVICE,
        "-w",
        secret,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not store the daily access-code secret in macOS Keychain")


def load_token() -> str:
    """Load a development override first, then the macOS Keychain secret."""
    configured = os.environ.get(TOKEN_ENV_VAR, "").strip()
    return configured or read_keychain_token()


def load_daily_code_secret() -> str:
    configured = os.environ.get(DAILY_CODE_SECRET_ENV_VAR, "").strip()
    return configured or read_keychain_daily_code_secret()


def ensure_token() -> tuple[str, bool]:
    existing = read_keychain_token()
    if existing:
        return existing, False
    token = generate_token()
    store_keychain_token(token)
    return token, True


def ensure_daily_code_secret() -> tuple[str, bool]:
    existing = read_keychain_daily_code_secret()
    if existing:
        return existing, False
    secret = generate_daily_code_secret()
    store_keychain_daily_code_secret(secret)
    return secret, True


def daily_access_code(
    secret: str | None = None,
    *,
    day: dt.date | None = None,
) -> str:
    """Derive one human-friendly six-character code for the Mac's local calendar day."""
    key = (secret if secret is not None else load_daily_code_secret()).strip()
    if not key:
        raise RuntimeError("The daily access-code secret is missing; run with --ensure first")
    selected_day = day or dt.datetime.now().astimezone().date()
    digest = hmac.new(
        key.encode("utf-8"),
        f"penguin-connect-daily-access:{selected_day.isoformat()}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    value = int.from_bytes(digest[:4], "big") >> 2
    characters: list[str] = []
    for _ in range(DAILY_CODE_LENGTH):
        characters.append(DAILY_CODE_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(characters))


def connection_token(token: str, code: str | None = None) -> str:
    """Bind the install bearer to today's rotating code for wire authentication."""
    clean_token = (token or "").strip()
    if not clean_token:
        raise RuntimeError("The remote MCP token is missing")
    clean_code = (code or daily_access_code()).strip().upper()
    if len(clean_code) != DAILY_CODE_LENGTH or any(
        character not in DAILY_CODE_ALPHABET for character in clean_code
    ):
        raise ValueError("The daily access code is invalid")
    return f"{clean_token}.{clean_code}"


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
    try:
        access_token = connection_token(token)
    except RuntimeError as exc:
        print(f"[missing] {exc}", file=sys.stderr)
        return 1
    result = subprocess.run(
        ["pbcopy"],
        input=access_token,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        print("[error] Could not copy the remote MCP token.", file=sys.stderr)
        return 1
    print("[ok] Today's remote MCP access token copied to the clipboard.")
    return 0


def _copy_daily_code() -> int:
    try:
        code = daily_access_code()
    except RuntimeError as exc:
        print(f"[missing] {exc}", file=sys.stderr)
        return 1
    result = subprocess.run(
        ["pbcopy"],
        input=code,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        print("[error] Could not copy today's access code.", file=sys.stderr)
        return 1
    print("[ok] Today's six-character access code copied to the clipboard.")
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
        help="Copy today's bearer-plus-code access token without printing it.",
    )
    actions.add_argument(
        "--daily-code",
        action="store_true",
        help="Print today's six-character access code for display inside the local app.",
    )
    actions.add_argument(
        "--copy-daily-code",
        action="store_true",
        help="Copy today's six-character access code without printing it.",
    )
    actions.add_argument(
        "--status",
        action="store_true",
        help="Report whether a token is available without printing it (default).",
    )
    args = parser.parse_args()

    if args.daily_code:
        try:
            print(daily_access_code())
            return 0
        except RuntimeError as exc:
            print(f"[missing] {exc}", file=sys.stderr)
            return 1
    if args.copy_daily_code:
        return _copy_daily_code()
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
        _, token_created = ensure_token()
        _, code_secret_created = ensure_daily_code_secret()
        if token_created:
            print("[ok] Created the remote MCP token in macOS Keychain.")
        else:
            print("[ok] Remote MCP token already exists; left it unchanged.")
        if code_secret_created:
            print("[ok] Created the rotating access-code secret in macOS Keychain.")
        else:
            print("[ok] Rotating access-code secret already exists; left it unchanged.")
        return 0

    if load_token() and load_daily_code_secret():
        print("[ok] Remote MCP bearer and rotating access-code secret are available.")
        return 0
    print("[missing] Remote MCP credentials are incomplete.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
