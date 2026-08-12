#!/usr/bin/env python3
"""Configure and call a loopback-only BlueBubbles iMessage backend."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


KEYCHAIN_SERVICE = "com.penguinconnect.bluebubbles.server-password"
KEYCHAIN_ACCOUNT = "penguin-connect-bluebubbles"
CONFIG_ENV_VAR = "PENGUIN_CONNECT_IMESSAGE_BACKEND_CONFIG_PATH"
DEFAULT_BLUEBUBBLES_PORT = 1234


class BlueBubblesError(RuntimeError):
    """A sanitized BlueBubbles configuration or request failure."""


@dataclass(frozen=True)
class BlueBubblesConfig:
    api_base: str


def default_config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "PenguinConnect"
        / "imessage-backend.json"
    )


def _is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_api_base(value: str) -> str:
    """Normalize a BlueBubbles origin while refusing non-loopback or secret-bearing URLs."""
    raw = (value or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("BlueBubbles URL must use http or https")
    if not parsed.hostname or not _is_loopback_host(parsed.hostname):
        raise ValueError("BlueBubbles must remain on a loopback address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("BlueBubbles URL must not contain credentials, query parameters, or fragments")
    if parsed.path.rstrip("/") not in {"", "/api/v1"}:
        raise ValueError("BlueBubbles URL path must be empty or /api/v1")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("BlueBubbles URL contains an invalid port") from exc
    if port is None:
        port = DEFAULT_BLUEBUBBLES_PORT
    if not 1 <= port <= 65535:
        raise ValueError("BlueBubbles URL contains an invalid port")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return urllib.parse.urlunsplit(
        (parsed.scheme, f"{host}:{port}", "/api/v1", "", "")
    )


def load_config(path: Path | None = None) -> BlueBubblesConfig | None:
    config_path = path or default_config_path()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("backend") != "bluebubbles":
            return None
        return BlueBubblesConfig(api_base=validate_api_base(str(payload.get("api_base") or "")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_config(config: BlueBubblesConfig, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    normalized = BlueBubblesConfig(api_base=validate_api_base(config.api_base))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(
                {"backend": "bluebubbles", "api_base": normalized.api_base},
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, config_path)
        os.chmod(config_path, 0o600)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return config_path


def _security(
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        capture_output=True,
        text=True,
        input=input_text,
        timeout=10,
    )


def read_keychain_password() -> str:
    result = _security(
        "find-generic-password",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def store_keychain_password(password: str) -> None:
    clean = (password or "").strip()
    if not clean:
        raise ValueError("BlueBubbles password must not be empty")
    result = _security(
        "add-generic-password",
        "-U",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
        input_text=clean + "\n",
    )
    if result.returncode != 0:
        raise BlueBubblesError("Could not store the BlueBubbles password in macOS Keychain")


def delete_keychain_password() -> None:
    _security(
        "delete-generic-password",
        "-a",
        KEYCHAIN_ACCOUNT,
        "-s",
        KEYCHAIN_SERVICE,
    )


class BlueBubblesClient:
    """Minimal REST client for BlueBubbles' loopback server."""

    def __init__(
        self,
        config: BlueBubblesConfig,
        password: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        self._api_base = validate_api_base(config.api_base)
        self._password = (password or "").strip()
        if not self._password:
            raise ValueError("BlueBubbles password must not be empty")
        self._opener = opener

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 45,
    ) -> dict[str, Any]:
        encoded_auth = urllib.parse.urlencode({"guid": self._password})
        url = f"{self._api_base.rstrip('/')}/{path.lstrip('/')}?{encoded_auth}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout) as response:
                result = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise BlueBubblesError(
                f"BlueBubbles rejected the request (HTTP {exc.code})"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BlueBubblesError("BlueBubbles is unavailable on this Mac") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BlueBubblesError("BlueBubbles returned an invalid response") from None
        if not isinstance(result, dict):
            raise BlueBubblesError("BlueBubbles returned an invalid response")
        response_status = result.get("status")
        if isinstance(response_status, int) and response_status >= 400:
            raise BlueBubblesError(
                f"BlueBubbles rejected the request (HTTP {response_status})"
            )
        if result.get("error") and not result.get("data"):
            raise BlueBubblesError("BlueBubbles rejected the request")
        return result

    def ping(self) -> None:
        self._request("GET", "ping", timeout=10)

    def create_group(
        self,
        participants: list[str],
        *,
        first_message: str,
        name: str = "",
    ) -> dict[str, Any]:
        if len(participants) < 2:
            raise ValueError("An iMessage group requires at least two participants")
        clean_message = (first_message or "").strip()
        if not clean_message:
            raise ValueError("BlueBubbles requires a first message to create an iMessage group")
        created = self._request(
            "POST",
            "chat/new",
            payload={
                "addresses": participants,
                "message": clean_message,
                "method": "private-api",
                "service": "iMessage",
            },
        )
        data = created.get("data")
        group_id = str(data.get("guid") or "").strip() if isinstance(data, dict) else ""
        if not group_id:
            raise BlueBubblesError("BlueBubbles did not return the created iMessage group")

        clean_name = (name or "").strip()
        name_applied = not clean_name
        name_error = ""
        if clean_name:
            encoded_group_id = urllib.parse.quote(group_id, safe="")
            try:
                self._request(
                    "PUT",
                    f"chat/{encoded_group_id}",
                    payload={"displayName": clean_name},
                )
                name_applied = True
            except BlueBubblesError:
                # Creation and the first send already happened; report the partial result.
                name_error = "group_created_but_name_not_applied"

        return {
            "group_id": group_id,
            "name_applied": name_applied,
            "name_error": name_error,
            "first_message_sent": True,
        }


def load_client(*, config_path: Path | None = None) -> BlueBubblesClient | None:
    config = load_config(config_path)
    if config is None:
        return None
    password = read_keychain_password()
    if not password:
        return None
    try:
        return BlueBubblesClient(config, password)
    except ValueError:
        return None


def _clipboard_text() -> str:
    result = subprocess.run(
        ["pbpaste"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _clear_clipboard() -> None:
    subprocess.run(["pbcopy"], input="", text=True, timeout=10, check=False)


def _configure(api_url: str, *, password: str | None = None) -> int:
    try:
        config = BlueBubblesConfig(api_base=validate_api_base(api_url))
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    using_clipboard = password is None
    clean_password = _clipboard_text() if using_clipboard else (password or "").strip()
    if not clean_password:
        instruction = "Copy the BlueBubbles server password first." if using_clipboard else "Enter the BlueBubbles server password."
        print(f"[error] {instruction}", file=sys.stderr)
        return 1
    try:
        BlueBubblesClient(config, clean_password).ping()
        store_keychain_password(clean_password)
        save_config(config)
    except (ValueError, BlueBubblesError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    finally:
        if using_clipboard:
            _clear_clipboard()
    suffix = "; clipboard cleared" if using_clipboard else ""
    print(f"[ok] BlueBubbles enhanced iMessage backend connected{suffix}.")
    return 0


def _stdin_password() -> str:
    value = sys.stdin.read(4097)
    if len(value) > 4096:
        raise ValueError("BlueBubbles password is too long")
    return value.strip()


def _status() -> int:
    client = load_client()
    if client is None:
        print("[not configured] Enhanced iMessage group creation is disabled.")
        return 1
    try:
        client.ping()
    except BlueBubblesError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print("[ok] BlueBubbles enhanced iMessage backend is reachable on loopback.")
    return 0


def _disconnect() -> int:
    delete_keychain_password()
    config_path = default_config_path()
    try:
        config_path.unlink()
    except FileNotFoundError:
        pass
    print("[ok] BlueBubbles enhanced iMessage backend disconnected.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--configure",
        metavar="URL",
        help="Verify a loopback BlueBubbles URL using the password on the clipboard, then store it.",
    )
    actions.add_argument(
        "--configure-stdin",
        metavar="URL",
        help=argparse.SUPPRESS,
    )
    actions.add_argument(
        "--disconnect",
        action="store_true",
        help="Remove Penguin's BlueBubbles configuration and Keychain password.",
    )
    actions.add_argument(
        "--status",
        action="store_true",
        help="Verify the configured loopback backend without printing its password (default).",
    )
    args = parser.parse_args()
    if args.configure:
        return _configure(args.configure)
    if args.configure_stdin:
        try:
            password = _stdin_password()
        except ValueError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 1
        return _configure(args.configure_stdin, password=password)
    if args.disconnect:
        return _disconnect()
    return _status()


if __name__ == "__main__":
    raise SystemExit(main())
