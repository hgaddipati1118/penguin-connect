#!/usr/bin/env python3
"""Import macOS Contacts into the local PenguinConnect bridge DB."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

AB_BASE = Path.home() / "Library" / "Application Support" / "AddressBook"
DATA_DIR = Path.home() / "penguinconnect-local-bridge-data"
CACHE_DB = DATA_DIR / "cache.db"


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    cleaned = re.sub(r"[^\d+]", "", phone)
    if not cleaned:
        return None
    if len(cleaned) >= 10 and not cleaned.startswith("+"):
        if cleaned.startswith("1") and len(cleaned) == 11:
            cleaned = "+" + cleaned
        elif len(cleaned) == 10:
            cleaned = "+1" + cleaned
    return cleaned


def find_addressbook_dbs() -> list[Path]:
    dbs: list[Path] = []
    sources_dir = AB_BASE / "Sources"
    if sources_dir.exists():
        for source in sources_dir.iterdir():
            db_path = source / "AddressBook-v22.abcddb"
            if db_path.exists():
                dbs.append(db_path)

    main_db = AB_BASE / "AddressBook-v22.abcddb"
    if main_db.exists():
        dbs.append(main_db)

    return dbs


def read_contacts_from_db(db_path: Path) -> list[dict]:
    contacts: list[dict] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()

        cur.execute(
            """
            SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER
            FROM ZABCDPHONENUMBER p
            JOIN ZABCDRECORD r ON p.ZOWNER = r.Z_PK
            WHERE p.ZFULLNUMBER IS NOT NULL AND p.ZFULLNUMBER != ''
            """
        )
        for first, last, org, phone in cur.fetchall():
            contacts.append(
                {
                    "first_name": first or "",
                    "last_name": last or "",
                    "organization": org or "",
                    "phone": phone,
                    "phone_normalized": normalize_phone(phone),
                    "email": None,
                    "source_db": str(db_path),
                }
            )

        cur.execute(
            """
            SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, e.ZADDRESS
            FROM ZABCDEMAILADDRESS e
            JOIN ZABCDRECORD r ON e.ZOWNER = r.Z_PK
            WHERE e.ZADDRESS IS NOT NULL AND e.ZADDRESS != ''
            """
        )
        for first, last, org, email in cur.fetchall():
            contacts.append(
                {
                    "first_name": first or "",
                    "last_name": last or "",
                    "organization": org or "",
                    "phone": None,
                    "phone_normalized": None,
                    "email": (email or "").lower().strip(),
                    "source_db": str(db_path),
                }
            )

        conn.close()
    except Exception as exc:
        print(f"[warn] Error reading {db_path}: {exc}", file=sys.stderr)
    return contacts


def import_to_cache(contacts: list[dict]) -> int:
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT,
            last_name TEXT,
            organization TEXT,
            phone TEXT,
            phone_normalized TEXT,
            email TEXT,
            source_db TEXT,
            imported_at TEXT DEFAULT (datetime('now')),
            UNIQUE(phone_normalized, first_name, last_name)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone_normalized)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email)")

    conn.execute("DELETE FROM contacts")

    inserted = 0
    for c in contacts:
        try:
            if c["phone_normalized"]:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO contacts
                    (first_name, last_name, organization, phone, phone_normalized, email, source_db)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c["first_name"],
                        c["last_name"],
                        c["organization"],
                        c["phone"],
                        c["phone_normalized"],
                        c["email"],
                        c["source_db"],
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            elif c["email"]:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO contacts
                    (first_name, last_name, organization, phone, phone_normalized, email, source_db)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c["first_name"],
                        c["last_name"],
                        c["organization"],
                        None,
                        None,
                        c["email"],
                        c["source_db"],
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
        except Exception:
            pass

    conn.commit()
    conn.close()
    return inserted


def main() -> int:
    dbs = find_addressbook_dbs()
    if not dbs:
        print("No AddressBook databases found")
        return 0

    print(f"Found {len(dbs)} AddressBook database(s)")
    all_contacts: list[dict] = []
    for db_path in dbs:
        contacts = read_contacts_from_db(db_path)
        print(f"  {db_path}: {len(contacts)} entries")
        all_contacts.extend(contacts)

    print(f"Total entries: {len(all_contacts)}")
    inserted = import_to_cache(all_contacts)
    print(f"Imported {inserted} contacts into {CACHE_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
