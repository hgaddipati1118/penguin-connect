#!/usr/bin/env python3
"""Interactive Gmail OAuth helper for PenguinConnect local bridge.

This script runs a local browser OAuth flow, then connects Gmail to the
local bridge by POSTing token_json to /penguin-connect/gmail/connect.

Usage:
  python scripts/penguin_connect_connect.py --gmail user@gmail.com
  python scripts/penguin_connect_connect.py --gmail user@gmail.com --api-base http://127.0.0.1:9000
  python scripts/penguin_connect_connect.py --gmail user@gmail.com --client-secrets /path/to/client_secret.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from penguin_connect_local_api import resolve_local_api_base

SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]


def _oauth_help_text(port: int) -> str:
    redirect_uri = f"http://localhost:{port}/"
    return (
        "OAuth setup fix:\n"
        "1) In Google Cloud Console, enable Gmail API for your project.\n"
        "2) Configure OAuth consent screen and add your Gmail as test user (if app is in Testing).\n"
        "3) Create OAuth client ID with Application type: Desktop app.\n"
        "4) Download JSON and use it as ./client_secret.json or pass --client-secrets PATH.\n"
        f"5) If you must use a Web client instead, add Authorized redirect URI: {redirect_uri}\n"
        "6) Approve full Gmail mailbox access during consent so PenguinConnect can permanently delete its own mirrored messages when needed.\n"
        "Tip: You can use a browser agent to click through these Google Console steps."
    )


def _load_client_secret_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid client secret JSON at {path}: {exc}") from exc


def _client_secret_kind(payload: dict) -> str:
    if isinstance(payload, dict):
        if "installed" in payload:
            return "desktop"
        if "web" in payload:
            return "web"
    return "unknown"


def _load_env(env_path: Path):
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except Exception:
        pass


def _resolve_client_secret_path(args_path: str | None, repo_root: Path) -> Path | None:
    candidates = []
    if args_path:
        candidates.append(Path(args_path).expanduser())
    env_path = os.environ.get("PENGUIN_CONNECT_GOOGLE_CLIENT_SECRETS")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            repo_root / "client_secret.json",
            repo_root / "google_client_secret.json",
            Path.home() / "penguinconnect-local-bridge-data" / "google_client_secret.json",
        ]
    )
    for p in candidates:
        if p.exists():
            return p
    return None


def _run_oauth(client_secret_path: Path, port: int) -> dict:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "google-auth-oauthlib not installed. Run: cd server && venv/bin/pip install -r requirements.txt"
        ) from exc

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    try:
        creds = flow.run_local_server(port=port, access_type="offline", prompt="consent")
    except Exception as exc:
        msg = str(exc).lower()
        if "redirect_uri_mismatch" in msg:
            raise RuntimeError(
                "Google returned redirect_uri_mismatch.\n"
                + _oauth_help_text(port)
            ) from exc
        raise

    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": sorted(list(creds.scopes or [])),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }

    if not payload.get("refresh_token"):
        raise RuntimeError(
            "OAuth completed but refresh_token is missing. Revoke app access in Google Account and rerun."
        )

    return payload


def _post_connect(api_base: str, gmail_email: str, token_json: dict) -> dict:
    url = api_base.rstrip("/") + "/penguin-connect/gmail/connect"
    resp = requests.post(
        url,
        json={"gmail_email": gmail_email, "token_json": token_json},
        timeout=25,
    )
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text[:500]}

    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise RuntimeError(f"connect failed ({resp.status_code}): {detail}")

    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect Gmail to PenguinConnect local bridge")
    parser.add_argument("--gmail", required=True, help="Primary Gmail address to connect")
    parser.add_argument(
        "--api-base",
        default=None,
        help="Local API base URL (defaults to PENGUIN_CONNECT_LOCAL_API_BASE or PENGUIN_CONNECT_PORT)",
    )
    parser.add_argument("--client-secrets", default=None, help="Path to Google OAuth Desktop client_secret JSON")
    parser.add_argument("--oauth-port", type=int, default=8765, help="Local port for OAuth callback")
    args = parser.parse_args()

    gmail = (args.gmail or "").strip().lower()
    if "@" not in gmail:
        print("[error] --gmail must be a valid email", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    _load_env(repo_root / ".env")
    api_base = (args.api_base or resolve_local_api_base()).strip()

    client_secret_path = _resolve_client_secret_path(args.client_secrets, repo_root)
    if not client_secret_path:
        print(
            "[error] Google OAuth client secret JSON not found.\n"
            "Place one at ./client_secret.json or pass --client-secrets PATH.",
            file=sys.stderr,
        )
        return 2

    client_secret_json = _load_client_secret_json(client_secret_path)
    client_kind = _client_secret_kind(client_secret_json)
    if client_kind != "desktop":
        if client_kind == "web":
            print(
                "[error] OAuth JSON is a Web client, but this script expects a Desktop client.",
                file=sys.stderr,
            )
        else:
            print(
                "[error] Could not detect OAuth client type in JSON. Expected a Desktop client JSON.",
                file=sys.stderr,
            )
        print(_oauth_help_text(args.oauth_port), file=sys.stderr)
        return 2

    print(f"[info] Using client secrets: {client_secret_path}")
    print("[info] Requesting Gmail scopes:")
    for scope in SCOPES:
        print(f"  - {scope}")
    print("[info] This includes full Gmail mailbox access so the bridge can permanently delete only its own mirrored messages when needed.")
    print("[info] Opening browser for Google OAuth consent...")
    token_json = _run_oauth(client_secret_path, args.oauth_port)

    print("[info] Exchanging token with local PenguinConnect API...")
    result = _post_connect(api_base, gmail, token_json)

    safe_result = {
        "success": result.get("success"),
        "gmail_email": result.get("gmail_email"),
        "primary_send_as": result.get("primary_send_as"),
        "send_as_aliases": result.get("send_as_aliases", []),
    }
    print(json.dumps(safe_result, indent=2))
    print("[ok] Gmail connected and token stored in local Keychain")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[error] cancelled", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
