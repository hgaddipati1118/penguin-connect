#!/usr/bin/env python3
"""Validated, non-secret policy for PenguinConnect's installable remote MCP."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

VALID_REMOTE_SCOPES = frozenset(
    {
        "messages.read",
        "contacts.read",
        "messages.send",
        "contacts.write",
    }
)
VALID_REMOTE_PROVIDERS = frozenset({"imessage", "whatsapp"})


@dataclass(frozen=True)
class RemoteAccessPolicy:
    profile: str
    scopes: tuple[str, ...]
    providers: tuple[str, ...]
    local_approval_required: bool = True

    def allows(self, scope: str) -> bool:
        return scope in self.scopes

    def as_json_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["scopes"] = list(self.scopes)
        value["providers"] = list(self.providers)
        return value


_PROFILES = {
    "whatsapp": RemoteAccessPolicy(
        profile="whatsapp",
        scopes=("messages.read", "messages.send"),
        providers=("whatsapp",),
    ),
    "read-only": RemoteAccessPolicy(
        profile="read-only",
        scopes=("messages.read", "contacts.read"),
        providers=("imessage", "whatsapp"),
    ),
    "slashy": RemoteAccessPolicy(
        profile="slashy",
        scopes=(
            "messages.read",
            "contacts.read",
            "messages.send",
            "contacts.write",
        ),
        providers=("imessage", "whatsapp"),
    ),
}


def default_remote_policy_path() -> Path:
    configured = os.environ.get("PENGUIN_CONNECT_MCP_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "PenguinConnect"
        / "remote-mcp.json"
    )


def policy_for_profile(profile: str) -> RemoteAccessPolicy:
    try:
        return _PROFILES[(profile or "").strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown remote MCP profile: {profile}") from exc


def _validated_policy(data: object) -> RemoteAccessPolicy:
    if not isinstance(data, dict):
        raise ValueError("Remote MCP policy must be an object")
    profile = str(data.get("profile") or "").strip().lower()
    scopes = tuple(str(value or "").strip() for value in data.get("scopes") or [])
    providers = tuple(str(value or "").strip().lower() for value in data.get("providers") or [])
    approval_required = data.get("local_approval_required")
    if not profile or not scopes or not providers:
        raise ValueError("Remote MCP policy is incomplete")
    if len(scopes) != len(set(scopes)) or not set(scopes).issubset(VALID_REMOTE_SCOPES):
        raise ValueError("Remote MCP policy contains an unsupported scope")
    if len(providers) != len(set(providers)) or not set(providers).issubset(VALID_REMOTE_PROVIDERS):
        raise ValueError("Remote MCP policy contains an unsupported provider")
    if approval_required is not True:
        raise ValueError("Remote MCP writes must require local approval")
    expected = policy_for_profile(profile)
    candidate = RemoteAccessPolicy(profile, scopes, providers, True)
    if candidate != expected:
        raise ValueError("Remote MCP profile does not match its built-in policy")
    return candidate


def load_remote_policy(path: Path | None = None) -> RemoteAccessPolicy:
    """Load policy, preserving the original WhatsApp-only boundary on any error."""
    policy_path = path or default_remote_policy_path()
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
        return _validated_policy(data)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return policy_for_profile("whatsapp")


def save_remote_policy(policy: RemoteAccessPolicy, path: Path | None = None) -> Path:
    """Atomically save a validated policy with owner-only permissions."""
    validated = _validated_policy(policy.as_json_dict())
    policy_path = path or default_remote_policy_path()
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=policy_path.parent,
            prefix=f".{policy_path.name}.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(validated.as_json_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, policy_path)
        os.chmod(policy_path, 0o600)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return policy_path
