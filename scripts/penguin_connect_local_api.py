#!/usr/bin/env python3
"""Helpers for resolving the local PenguinConnect API base URL."""

from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_LOCAL_API_PORT = 8888


def resolve_local_api_base(env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ

    explicit = (source.get("PENGUIN_CONNECT_LOCAL_API_BASE") or source.get("PENGUIN_CONNECT_LOCAL_API_BASE") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    raw_port = (source.get("PENGUIN_CONNECT_PORT") or source.get("PENGUIN_CONNECT_PORT") or str(DEFAULT_LOCAL_API_PORT)).strip()
    try:
        port = int(raw_port)
    except Exception:
        port = DEFAULT_LOCAL_API_PORT

    if port < 1 or port > 65535:
        port = DEFAULT_LOCAL_API_PORT

    return f"http://127.0.0.1:{port}"
