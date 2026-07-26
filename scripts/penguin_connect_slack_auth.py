#!/usr/bin/env python3
"""Store and verify Penguin's Slack user OAuth token in macOS Keychain."""

from __future__ import annotations

import argparse
import subprocess
import sys

import requests

KEYCHAIN_SERVICE = "com.penguinconnect.slack.oauth-token"
KEYCHAIN_ACCOUNT = "penguin-connect-slack-user"


def _security(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _read_token() -> str:
    result = _security(
        "find-generic-password",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _clipboard_token() -> str:
    result = subprocess.run(
        ["pbpaste"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _clear_clipboard() -> None:
    subprocess.run(
        ["pbcopy"],
        input="",
        text=True,
        timeout=10,
        check=False,
    )


def _store_from_clipboard() -> int:
    token = _clipboard_token()
    if not token.startswith("xoxp-"):
        print("[error] Clipboard does not contain a Slack user OAuth token.", file=sys.stderr)
        return 1
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
    _clear_clipboard()
    if result.returncode != 0:
        print("[error] Could not store the Slack token in macOS Keychain.", file=sys.stderr)
        return 1
    print("[ok] Slack user token stored in macOS Keychain; clipboard cleared.")
    return 0


def _status() -> int:
    token = _read_token()
    if not token:
        print("[not connected] No Slack user token found in macOS Keychain.")
        return 1
    try:
        response = requests.get(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        print("[error] Could not verify the Slack token.", file=sys.stderr)
        return 1
    if not payload.get("ok"):
        print(f"[error] Slack rejected the stored token: {payload.get('error') or 'unknown_error'}")
        return 1
    print(f"[ok] Connected to Slack workspace: {payload.get('team') or 'unknown'}")
    return 0


def _disconnect() -> int:
    result = _security(
        "delete-generic-password",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
    )
    if result.returncode != 0:
        print("[not connected] No Slack user token was stored.")
        return 0
    print("[ok] Removed the Slack user token from macOS Keychain.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--store-from-clipboard",
        action="store_true",
        help="Store an xoxp user token currently on the clipboard, then clear it.",
    )
    action.add_argument(
        "--disconnect",
        action="store_true",
        help="Delete Penguin's Slack user token from macOS Keychain.",
    )
    action.add_argument(
        "--status",
        action="store_true",
        help="Verify the stored token (default).",
    )
    args = parser.parse_args()
    if args.store_from_clipboard:
        return _store_from_clipboard()
    if args.disconnect:
        return _disconnect()
    return _status()


if __name__ == "__main__":
    raise SystemExit(main())
