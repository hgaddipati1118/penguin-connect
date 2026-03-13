#!/usr/bin/env python3
"""Guided setup CLI for PenguinConnect local bridge.

This script explains and runs the setup sequence in one place:
1) Bootstrap local env + Python deps
2) Ensure local bridge is running
3) Connect Gmail via OAuth helper
4) Run doctor checks
5) Run sync smoke test
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from penguin_connect_local_api import resolve_local_api_base

DEFAULT_SIGNATURE_MARKERS_FILE = ".penguin_connect_signature_markers.json"


def _header(text: str) -> None:
    print(f"\n=== {text} ===")


def _print_full_disk_access_note() -> None:
    print(
        "[note] iMessage access requires Full Disk Access for Terminal.app.\n"
        "[note] If doctor reports imessage_db unreadable, enable:\n"
        "[note] System Settings -> Privacy & Security -> Full Disk Access -> Terminal"
    )


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {shlex.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd), check=check)


def _confirm(prompt: str, default_yes: bool, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"[auto] {prompt}: {'yes' if default_yes else 'no'}")
        return default_yes

    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        raw = input(f"{prompt} {suffix} ").strip().lower()
        if not raw:
            return default_yes
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_value(key: str, env_file: dict[str, str]) -> str:
    val = os.environ.get(key)
    if val is not None and val != "":
        return val
    return env_file.get(key, "")


def _prompt_text(prompt: str, default: str = "") -> str:
    raw = input(prompt).strip()
    if raw:
        return raw
    return default


def _upsert_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            if not updated:
                new_lines.append(f"{key}={value}")
                updated = True
            continue
        new_lines.append(line)
    if not updated:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _normalize_signature_markers(markers: list[str]) -> list[str]:
    normalized: list[str] = []
    for marker in markers:
        for chunk in marker.replace("\r", "\n").split("\n"):
            for part in chunk.split("||"):
                cleaned = part.strip()
                if cleaned:
                    normalized.append(cleaned)
    return list(dict.fromkeys(normalized))


def _resolve_signature_markers_path(repo_root: Path, env_file: dict[str, str]) -> Path:
    configured = _env_value("PENGUIN_CONNECT_SIGNATURE_MARKERS_FILE", env_file).strip()
    if configured:
        return Path(configured).expanduser()
    return repo_root / DEFAULT_SIGNATURE_MARKERS_FILE


def _write_signature_markers_file(path: Path, markers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"signature_markers": markers}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_signature_markers_file(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        markers = payload.get("signature_markers")
    else:
        markers = payload
    if not isinstance(markers, list):
        return []
    return [marker.strip() for marker in markers if isinstance(marker, str) and marker.strip()]


def _configure_signature_markers(
    repo_root: Path,
    env_path: Path,
    *,
    cli_markers: list[str],
    assume_yes: bool,
    env_file: dict[str, str],
) -> None:
    markers_path = _resolve_signature_markers_path(repo_root, env_file)
    existing_markers = _load_signature_markers_file(markers_path)
    markers = _normalize_signature_markers(cli_markers)
    if markers:
        _write_signature_markers_file(markers_path, markers)
        print(f"[ok] Saved {len(markers)} signature marker(s) to {markers_path}")
        return

    if assume_yes:
        if existing_markers:
            print(f"[ok] Keeping existing signature markers in {markers_path}.")
        return

    if existing_markers:
        print(f"[info] Existing signature markers file: {markers_path}")
        print(f"[info] Current markers: {' || '.join(existing_markers)}")
        keep_existing = _confirm(
            "Keep the current email signature/disclaimer cutoff markers?",
            default_yes=True,
            assume_yes=assume_yes,
        )
        if keep_existing:
            return

    should_configure = _confirm(
        "Add custom email signature/disclaimer cutoff markers?",
        default_yes=False,
        assume_yes=assume_yes,
    )
    if not should_configure:
        return

    print("[info] Enter phrases that start the content you want removed.")
    print(f"[info] Markers will be saved to {markers_path}")
    print("[info] Example: External email:||Company Confidential||Automated footer")
    raw_value = _prompt_text("Markers (separate multiple values with ||): ")
    markers = _normalize_signature_markers([raw_value])
    if not markers:
        print("[skip] No custom markers entered.")
        return

    _write_signature_markers_file(markers_path, markers)
    print(f"[ok] Saved {len(markers)} signature marker(s) to {markers_path}")


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _wait_for_health(api_base: str, timeout_seconds: int) -> bool:
    health_url = api_base.rstrip("/") + "/penguin-connect/health"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _http_ok(health_url):
            return True
        time.sleep(1)
    return False


def _bootstrap(repo_root: Path, skip_install: bool) -> Path:
    _header("Step 1/5: Bootstrap Local Environment")
    env_file = repo_root / ".env"
    env_example = repo_root / ".env.example"

    if env_file.exists():
        print(f"[ok] Found {env_file}")
    elif env_example.exists():
        shutil.copyfile(env_example, env_file)
        print(f"[ok] Created {env_file} from {env_example}")
    else:
        raise RuntimeError(f"Missing {env_example}. Cannot initialize .env")

    venv_python = repo_root / "server" / "venv" / "bin" / "python"
    if skip_install:
        print("[skip] Skipping dependency install (--skip-install).")
        return venv_python

    if not venv_python.exists():
        print("[info] Creating virtual environment at server/venv")
        _run([sys.executable, "-m", "venv", "server/venv"], cwd=repo_root)
    else:
        print("[ok] Virtual environment already exists")

    print("[info] Installing/updating Python dependencies")
    _run([str(repo_root / "server" / "venv" / "bin" / "pip"), "install", "-r", "server/requirements.txt"], cwd=repo_root)

    return venv_python


def _ensure_server_running(repo_root: Path, api_base: str, no_open_terminal: bool, assume_yes: bool) -> None:
    _header("Step 2/5: Ensure Local Bridge Is Running")

    if _wait_for_health(api_base, timeout_seconds=2):
        print(f"[ok] Local API is already reachable at {api_base}")
        return

    print(f"[info] Local API is not reachable at {api_base}")

    if no_open_terminal:
        raise RuntimeError(
            "Bridge is not running. Start it first with "
            "PENGUIN_CONNECT_ALLOW_MISSING_GMAIL_STARTUP=1 ./scripts/run_penguin_connect_bridge.sh "
            "and rerun setup."
        )

    should_open = _confirm(
        "Open a new Terminal window and start the bridge now?",
        default_yes=True,
        assume_yes=assume_yes,
    )
    if not should_open:
        raise RuntimeError("Setup requires local bridge API. Start it and rerun this script.")

    _run(["./scripts/open_penguin_connect_bridge_terminal.sh", "--allow-missing-gmail-startup"], cwd=repo_root)
    print("[info] Waiting for bridge health endpoint...")
    if not _wait_for_health(api_base, timeout_seconds=45):
        raise RuntimeError(
            "Bridge did not become healthy in time. Check the opened Terminal logs, then rerun setup."
        )

    print(f"[ok] Local API is healthy at {api_base}")


def _resolve_gmail(args_gmail: str | None, assume_yes: bool) -> str:
    if args_gmail:
        gmail = args_gmail.strip().lower()
        if "@" not in gmail:
            raise RuntimeError("--gmail must be a valid email address")
        return gmail

    if assume_yes:
        raise RuntimeError("--gmail is required when using --yes")

    while True:
        value = input("Enter your Gmail address to connect: ").strip().lower()
        if "@" in value:
            return value
        print("Please enter a valid Gmail address.")


def _connect_gmail(
    repo_root: Path,
    venv_python: Path,
    gmail: str,
    api_base: str,
    client_secrets: str | None,
    oauth_port: int,
    assume_yes: bool,
    skip_connect: bool,
) -> None:
    _header("Step 3/5: Connect Gmail")
    if skip_connect:
        print("[skip] Skipping Gmail connect (--skip-connect).")
        return

    should_run = _confirm(
        f"Start browser OAuth flow for {gmail}?",
        default_yes=True,
        assume_yes=assume_yes,
    )
    if not should_run:
        raise RuntimeError("Gmail connection is required for bridge setup.")

    cmd = [
        str(venv_python),
        "scripts/penguin_connect_connect.py",
        "--gmail",
        gmail,
        "--api-base",
        api_base,
        "--oauth-port",
        str(oauth_port),
    ]
    if client_secrets:
        cmd.extend(["--client-secrets", client_secrets])

    try:
        _run(cmd, cwd=repo_root)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Gmail OAuth connect failed. See the error above for exact Google OAuth fix steps "
            "(Desktop OAuth client JSON and browser-agent-assisted setup are supported)."
        ) from exc


def _run_doctor(repo_root: Path, venv_python: Path, skip_doctor: bool) -> None:
    _header("Step 4/5: Run Doctor Checks")
    if skip_doctor:
        print("[skip] Skipping doctor checks (--skip-doctor).")
        return

    _run([str(venv_python), "scripts/penguin_connect_doctor.py"], cwd=repo_root, check=False)


def _run_sync_smoke(api_base: str, skip_sync_smoke: bool) -> None:
    _header("Step 5/5: Run Sync Smoke Test")
    if skip_sync_smoke:
        print("[skip] Skipping sync smoke (--skip-sync-smoke).")
        return

    url = api_base.rstrip("/") + "/penguin-connect/conversations/sync"
    payload = json.dumps({"mode": "incremental", "days": 1}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = (resp.read() or b"{}").decode("utf-8", errors="replace")
            print(f"[ok] Sync smoke passed ({resp.status})")
            print(body[:400])
            return
    except urllib.error.HTTPError as exc:
        body = (exc.read() or b"").decode("utf-8", errors="replace")
        if "imessage_db_unreadable" in body:
            raise RuntimeError(
                "Sync smoke failed: imessage_db_unreadable. "
                "Grant Full Disk Access to Terminal.app and rerun."
            ) from exc
        raise RuntimeError(f"Sync smoke failed ({exc.code}): {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Sync smoke failed to reach API: {exc}") from exc


def _print_explain_plan(repo_root: Path, args: argparse.Namespace) -> None:
    print("PenguinConnect setup plan (explain-only):")
    print("0. Run from Terminal.app with Full Disk Access enabled.")
    print("1. Ensure .env exists (copy from .env.example if missing).")
    print("2. Optionally save custom email signature/disclaimer cutoff markers in a local JSON preferences file.")
    print("3. Ensure server/venv exists and install server/requirements.txt.")
    print(
        "4. Ensure local bridge API is running at /penguin-connect/health "
        "(bootstrap may use PENGUIN_CONNECT_ALLOW_MISSING_GMAIL_STARTUP=1 before OAuth connect)."
    )
    print("5. Connect Gmail using scripts/penguin_connect_connect.py.")
    print("6. Run scripts/penguin_connect_doctor.py and show final status.")
    print("7. Run sync smoke test via /penguin-connect/conversations/sync.")
    print("\nExpected command entry point:")
    cmd = ["./scripts/penguin_connect_setup.py"]
    if args.gmail:
        cmd.extend(["--gmail", args.gmail])
    if args.client_secrets:
        cmd.extend(["--client-secrets", args.client_secrets])
    print("$ " + shlex.join(cmd))
    print(f"\nRepo: {repo_root}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guided setup wizard for PenguinConnect local bridge"
    )
    parser.add_argument("--gmail", help="Gmail address to connect")
    parser.add_argument(
        "--signature-marker",
        action="append",
        default=[],
        help="Custom line prefix that should strip signatures/disclaimers and everything after it",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="Local API base URL (defaults to PENGUIN_CONNECT_LOCAL_API_BASE or PENGUIN_CONNECT_PORT)",
    )
    parser.add_argument("--client-secrets", help="Path to Google OAuth Desktop client_secret JSON")
    parser.add_argument("--oauth-port", type=int, default=8765, help="Local callback port for OAuth helper")
    parser.add_argument("--yes", action="store_true", help="Assume default yes for prompts")
    parser.add_argument("--skip-install", action="store_true", help="Skip virtualenv/dependency bootstrap")
    parser.add_argument("--skip-connect", action="store_true", help="Skip Gmail connect step")
    parser.add_argument(
        "--skip-inbox",
        action="store_true",
        help="Deprecated no-op flag retained for backward compatibility",
    )
    parser.add_argument("--skip-doctor", action="store_true", help="Skip final doctor checks")
    parser.add_argument("--skip-sync-smoke", action="store_true", help="Skip final sync smoke test")
    parser.add_argument(
        "--no-open-terminal",
        action="store_true",
        help="Do not auto-open Terminal to start the bridge if API is not running",
    )
    parser.add_argument(
        "--explain-only",
        action="store_true",
        help="Print the setup plan and exit without running commands",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    print("PenguinConnect Setup Wizard")
    print("This tool explains each setup phase and runs it in the required order.")
    _print_full_disk_access_note()

    if args.explain_only:
        _print_explain_plan(repo_root, args)
        return 0

    venv_python = _bootstrap(repo_root, skip_install=args.skip_install)
    env_path = repo_root / ".env"
    env_values = _read_env_file(env_path)
    _configure_signature_markers(
        repo_root,
        env_path,
        cli_markers=args.signature_marker,
        assume_yes=args.yes,
        env_file=env_values,
    )
    env_values = _read_env_file(env_path)
    local_api_env = dict(env_values)
    local_api_env.update({k: v for k, v in os.environ.items() if v != ""})
    api_base = (args.api_base or resolve_local_api_base(local_api_env)).strip()

    if not venv_python.exists():
        raise RuntimeError(
            f"Missing virtualenv python at {venv_python}. Remove --skip-install or create venv first."
        )

    _ensure_server_running(
        repo_root,
        api_base=api_base,
        no_open_terminal=args.no_open_terminal,
        assume_yes=args.yes,
    )

    if args.skip_inbox:
        print("[info] --skip-inbox is deprecated and has no effect.")

    gmail_required = not args.skip_connect
    gmail = _resolve_gmail(args.gmail, assume_yes=args.yes) if gmail_required else ""

    _connect_gmail(
        repo_root=repo_root,
        venv_python=venv_python,
        gmail=gmail,
        api_base=api_base,
        client_secrets=args.client_secrets,
        oauth_port=args.oauth_port,
        assume_yes=args.yes,
        skip_connect=args.skip_connect,
    )

    _run_doctor(repo_root=repo_root, venv_python=venv_python, skip_doctor=args.skip_doctor)
    _run_sync_smoke(api_base=api_base, skip_sync_smoke=args.skip_sync_smoke)

    _header("Done")
    print("[ok] Setup flow completed.")
    print("[next] Start bridge anytime with: ./scripts/run_penguin_connect_bridge.sh")
    print(f"[next] Health check: curl -s {api_base}/penguin-connect/health | jq")
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
