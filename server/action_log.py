"""Durable JSONL action log for PenguinConnect server operations."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any

DEFAULT_ACTION_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_ACTION_LOG_BACKUPS = 5
_LOG_LOCK = threading.Lock()


def _data_dir() -> Path:
    raw = os.environ.get("PENGUIN_CONNECT_DATA_DIR", str(Path.home() / "penguinconnect-local-bridge-data"))
    return Path(raw).expanduser()


def action_log_path() -> Path:
    raw = os.environ.get("PENGUIN_CONNECT_ACTION_LOG_PATH")
    if raw:
        return Path(raw).expanduser()
    return _data_dir() / "actions.jsonl"


def _action_log_max_bytes() -> int:
    raw = os.environ.get("PENGUIN_CONNECT_ACTION_LOG_MAX_BYTES", str(DEFAULT_ACTION_LOG_MAX_BYTES))
    try:
        value = int(raw)
    except Exception:
        return DEFAULT_ACTION_LOG_MAX_BYTES
    return max(1024, value)


def _action_log_backups() -> int:
    raw = os.environ.get("PENGUIN_CONNECT_ACTION_LOG_BACKUPS", str(DEFAULT_ACTION_LOG_BACKUPS))
    try:
        value = int(raw)
    except Exception:
        return DEFAULT_ACTION_LOG_BACKUPS
    return max(1, min(value, 20))


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _coerce(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce(item) for item in value]
    return str(value)


def _rotate_if_needed(path: Path) -> None:
    if not path.exists():
        return
    if path.stat().st_size < _action_log_max_bytes():
        return

    backups = _action_log_backups()
    oldest = path.with_name(f"{path.name}.{backups}")
    if oldest.exists():
        oldest.unlink()
    for index in range(backups - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            source.replace(target)
    path.replace(path.with_name(f"{path.name}.1"))


def message_fingerprint(text: str | None) -> dict[str, Any]:
    normalized = (text or "").strip()
    return {
        "body_length": len(normalized),
        "body_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None,
    }


def log_action(event: str, /, **fields: Any) -> dict[str, Any]:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{key: _coerce(value) for key, value in fields.items()},
    }
    path = action_log_path()

    with _LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    return record
