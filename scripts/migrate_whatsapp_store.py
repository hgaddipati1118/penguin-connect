#!/usr/bin/env python3
"""Safely migrate a paired legacy WhatsApp session into Penguin Application Support."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SESSION_DATABASE = "whatsapp.db"
MESSAGE_DATABASE = "messages.db"


def paired_device_count(store: Path) -> int:
    database = store / SESSION_DATABASE
    if not database.is_file():
        return 0
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT COUNT(*) FROM whatsmeow_device").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def migrate_legacy_store(legacy_store: Path, destination_store: Path) -> Path | None:
    """Copy only session/message databases when legacy is paired and destination is not."""
    if paired_device_count(legacy_store) < 1 or paired_device_count(destination_store) > 0:
        return None

    destination_parent = destination_store.parent
    destination_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination_parent, 0o700)
    staging = Path(tempfile.mkdtemp(prefix=".store-migration-", dir=destination_parent))
    backup: Path | None = None
    try:
        for name in (SESSION_DATABASE, MESSAGE_DATABASE):
            source = legacy_store / name
            if source.is_file():
                copied = staging / name
                shutil.copy2(source, copied)
                os.chmod(copied, 0o600)
        if not (staging / SESSION_DATABASE).is_file():
            raise RuntimeError("The paired legacy WhatsApp session database is unavailable")
        os.chmod(staging, 0o700)

        if destination_store.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = destination_parent / f"store-unpaired-backup-{timestamp}"
            os.replace(destination_store, backup)
        os.replace(staging, destination_store)
        return backup
    except Exception:
        if backup is not None and backup.exists() and not destination_store.exists():
            os.replace(backup, destination_store)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    backup = migrate_legacy_store(args.legacy.expanduser(), args.destination.expanduser())
    if backup is None:
        print("[skip] No paired legacy WhatsApp session needs migration.")
    else:
        print("[ok] Migrated the paired legacy WhatsApp session into Penguin Application Support.")
        print(f"[ok] Preserved the previous unpaired store at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
