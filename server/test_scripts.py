import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import penguin_connect_local_api
import import_contacts


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


if __name__ == "__main__":
    unittest.main()
