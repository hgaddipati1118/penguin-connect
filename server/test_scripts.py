import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import penguin_connect_local_api
import import_contacts
import penguin_connect_backfill
import penguin_connect_setup
import penguin_connect_verify_contact_resolution


class ScriptTests(unittest.TestCase):
    def test_resolve_local_api_base_prefers_explicit_base(self):
        base = penguin_connect_local_api.resolve_local_api_base(
            {
                "PENGUIN_CONNECT_LOCAL_API_BASE": "http://127.0.0.1:8899/",
                "PENGUIN_CONNECT_PORT": "9001",
            }
        )

        self.assertEqual(base, "http://127.0.0.1:8899")

    def test_resolve_local_api_base_falls_back_to_port(self):
        base = penguin_connect_local_api.resolve_local_api_base({"PENGUIN_CONNECT_PORT": "9001"})
        self.assertEqual(base, "http://127.0.0.1:9001")

    def test_resolve_local_api_base_handles_invalid_port(self):
        base = penguin_connect_local_api.resolve_local_api_base({"PENGUIN_CONNECT_PORT": "99999"})
        self.assertEqual(base, "http://127.0.0.1:8888")

    def test_import_contacts_counts_only_inserted_rows(self):
        contacts = [
            {
                "first_name": "Ava",
                "last_name": "Stone",
                "organization": "",
                "phone": "+1 (415) 555-0101",
                "phone_normalized": "+14155550101",
                "email": None,
                "source_db": "addressbook-1",
            },
            {
                "first_name": "Ava",
                "last_name": "Stone",
                "organization": "",
                "phone": "+1 415-555-0101",
                "phone_normalized": "+14155550101",
                "email": None,
                "source_db": "addressbook-1",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cache.db"
            db_path.touch()
            original_cache_db = import_contacts.CACHE_DB
            import_contacts.CACHE_DB = db_path
            try:
                inserted = import_contacts.import_to_cache(contacts)
                conn = sqlite3.connect(str(db_path))
                try:
                    row_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
                finally:
                    conn.close()
            finally:
                import_contacts.CACHE_DB = original_cache_db

        self.assertEqual(inserted, 1)
        self.assertEqual(row_count, 1)

    def test_verify_contact_resolution_normalizes_us_phone(self):
        normalized = penguin_connect_verify_contact_resolution.normalize_phone_for_lookup("+1 (404) 729-4874")
        self.assertEqual(normalized, "4047294874")

    def test_verify_contact_resolution_picks_contact_name_for_phone(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE contacts (
                   first_name TEXT,
                   last_name TEXT,
                   organization TEXT,
                   phone TEXT,
                   phone_normalized TEXT,
                   email TEXT,
                   imported_at TEXT
               )"""
        )
        conn.execute(
            """INSERT INTO contacts(first_name, last_name, organization, phone, phone_normalized, email, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("Julian", "Joseph", "", "+15127436385", "+15127436385", None),
        )

        resolved = penguin_connect_verify_contact_resolution.resolve_contact_name(conn, "+1 (512) 743-6385")
        conn.close()
        self.assertEqual(resolved, "Julian Joseph")

    def test_backfill_script_wait_seconds_clamped(self):
        wait = penguin_connect_backfill._rate_limit_wait_seconds({"retry_after_seconds": "2400"}, 900)
        self.assertEqual(wait, 900)

    def test_backfill_script_retries_after_rate_limit(self):
        with mock.patch(
            "penguin_connect_backfill._call_sync",
            side_effect=[
                {
                    "success": True,
                    "skipped": True,
                    "reason": "gmail_rate_limited",
                    "retry_after_seconds": 3,
                },
                {"success": True, "mode": "backfill"},
            ],
        ) as mock_call, mock.patch("penguin_connect_backfill.time.sleep") as mock_sleep:
            code, result = penguin_connect_backfill.run_backfill(
                api_base="http://127.0.0.1:8888",
                payload={"mode": "backfill", "verify_all": True, "days": 30},
                max_attempts=3,
                wait_on_rate_limit=True,
                max_wait_seconds=120,
                timeout_seconds=30,
            )

        self.assertEqual(code, 0)
        self.assertTrue(result["success"])
        self.assertEqual(mock_call.call_count, 2)
        mock_sleep.assert_called_once_with(3)

    def test_setup_persists_signature_markers_to_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("PENGUIN_CONNECT_PORT=8888\n", encoding="utf-8")
            env_values = penguin_connect_setup._read_env_file(env_path)

            penguin_connect_setup._configure_signature_markers(
                Path(tmp),
                env_path,
                cli_markers=["External email:", "Company Confidential"],
                assume_yes=True,
                env_file=env_values,
            )

            prefs_path = Path(tmp) / penguin_connect_setup.DEFAULT_SIGNATURE_MARKERS_FILE
            contents = prefs_path.read_text(encoding="utf-8")

        self.assertIn('"signature_markers"', contents)
        self.assertIn('"External email:"', contents)
        self.assertIn('"Company Confidential"', contents)


if __name__ == "__main__":
    unittest.main()
