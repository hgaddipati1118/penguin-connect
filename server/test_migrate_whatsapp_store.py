"""Tests for the one-time packaged WhatsApp session migration."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "migrate_whatsapp_store.py"
SPEC = importlib.util.spec_from_file_location("migrate_whatsapp_store", SCRIPT_PATH)
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)


def write_session(store: Path, devices: int, marker: str) -> None:
    store.mkdir(parents=True)
    with sqlite3.connect(store / "whatsapp.db") as conn:
        conn.execute("CREATE TABLE whatsmeow_device (id TEXT)")
        conn.executemany(
            "INSERT INTO whatsmeow_device VALUES (?)",
            [(f"synthetic-{index}",) for index in range(devices)],
        )
    with sqlite3.connect(store / "messages.db") as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES (?)", (marker,))


class MigrateWhatsAppStoreTests(unittest.TestCase):
    def test_paired_legacy_replaces_unpaired_destination_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy" / "store"
            destination = root / "destination" / "store"
            write_session(legacy, devices=1, marker="legacy")
            write_session(destination, devices=0, marker="unpaired")

            backup = migration.migrate_legacy_store(legacy, destination)

            self.assertIsNotNone(backup)
            self.assertEqual(migration.paired_device_count(destination), 1)
            with sqlite3.connect(destination / "messages.db") as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "legacy")
            with sqlite3.connect(backup / "messages.db") as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "unpaired")
            self.assertEqual((destination / "whatsapp.db").stat().st_mode & 0o777, 0o600)

    def test_existing_paired_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy" / "store"
            destination = root / "destination" / "store"
            write_session(legacy, devices=1, marker="legacy")
            write_session(destination, devices=1, marker="destination")

            backup = migration.migrate_legacy_store(legacy, destination)

            self.assertIsNone(backup)
            with sqlite3.connect(destination / "messages.db") as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "destination")


if __name__ == "__main__":
    unittest.main()
