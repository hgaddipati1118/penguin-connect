#!/usr/bin/env python3
"""Remove Penguin background access, with optional explicit deletion of local data."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

LAUNCH_AGENT_LABELS = (
    "com.penguinconnect.remote-tunnel",
    "com.penguinconnect.remote-mcp",
    "com.penguinconnect.whatsapp-bridge",
    "com.penguinconnect.local.bridge",
    "com.penguinconnect.local.bridge.watchdog",
)
KEYCHAIN_ITEMS = (
    ("penguin-connect-remote-mcp", "com.penguinconnect.remote-mcp.bearer-token"),
    (
        "penguin-connect-remote-mcp-daily-code",
        "com.penguinconnect.remote-mcp.daily-code-secret",
    ),
    (
        "penguin-connect-bluebubbles",
        "com.penguinconnect.bluebubbles.server-password",
    ),
)
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "PenguinConnect"
ENDPOINT_STATE = APP_SUPPORT / "remote-endpoint.json"
IMESSAGE_BACKEND_CONFIG = APP_SUPPORT / "imessage-backend.json"
LOCAL_DATA = Path.home() / "penguinconnect-local-bridge-data"
TAILSCALE_PORT = 10000


def run_quiet(arguments: list[str], *, timeout: int = 20) -> None:
    subprocess.run(
        arguments,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def endpoint_uses_tailscale() -> bool:
    try:
        payload = json.loads(ENDPOINT_STATE.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return False
    return payload.get("tunnel") == "tailscale"


def find_tailscale() -> Path | None:
    configured = os.environ.get("PENGUIN_CONNECT_TAILSCALE_BIN", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    discovered = shutil.which("tailscale")
    if discovered:
        candidates.append(Path(discovered))
    candidates.extend(
        (
            Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
            Path.home() / "Applications/Tailscale.app/Contents/MacOS/Tailscale",
        )
    )
    return next(
        (path for path in candidates if path.is_file() and os.access(path, os.X_OK)),
        None,
    )


def remove_imessage_backend_config() -> None:
    try:
        IMESSAGE_BACKEND_CONFIG.unlink()
    except FileNotFoundError:
        pass


def stop_background_access() -> None:
    if endpoint_uses_tailscale() and (tailscale := find_tailscale()) is not None:
        run_quiet(
            [
                str(tailscale),
                "funnel",
                f"--https={TAILSCALE_PORT}",
                "8765",
                "off",
            ],
            timeout=30,
        )
    domain = f"gui/{os.getuid()}"
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    for label in LAUNCH_AGENT_LABELS:
        run_quiet(["/bin/launchctl", "bootout", f"{domain}/{label}"])
        run_quiet(["/bin/launchctl", "disable", f"{domain}/{label}"])
        plist = launch_agents / f"{label}.plist"
        if plist.is_file():
            plist.unlink()
    for account, service in KEYCHAIN_ITEMS:
        run_quiet(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                account,
                "-s",
                service,
            ]
        )
    if ENDPOINT_STATE.is_file():
        ENDPOINT_STATE.unlink()
    remove_imessage_backend_config()


def delete_local_data() -> None:
    for path in (APP_SUPPORT, LOCAL_DATA):
        if path.is_dir():
            shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete-data",
        action="store_true",
        help="Also permanently delete Penguin runtime, logs, indexes, and WhatsApp session data.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm permanent local-data deletion; required with --delete-data.",
    )
    args = parser.parse_args()
    if args.delete_data and not args.yes:
        parser.error("--delete-data requires --yes")

    stop_background_access()
    if args.delete_data:
        delete_local_data()
        print("[ok] Penguin background access and local data were removed.")
    else:
        print("[ok] Penguin background access and remote credentials were removed.")
        print("Local indexes and the WhatsApp session were preserved. Use --delete-data --yes to delete them.")
    print("Move /Applications/Penguin.app to Trash to remove the application itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
