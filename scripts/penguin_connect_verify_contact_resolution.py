#!/usr/bin/env python3
"""Verify PenguinConnect contact resolution against local cache data."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except Exception:
        pass


def cache_db_path() -> Path:
    data_dir = Path(
        os.environ.get("PENGUIN_CONNECT_DATA_DIR", str(Path.home() / "penguinconnect-local-bridge-data"))
    ).expanduser()
    return Path(os.environ.get("PENGUIN_CONNECT_DB_PATH", str(data_dir / "cache.db"))).expanduser()


def normalize_phone_for_lookup(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def contact_display_name(row: Optional[sqlite3.Row]) -> Optional[str]:
    if not row:
        return None
    first = (row["first_name"] or "").strip()
    last = (row["last_name"] or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    org = (row["organization"] or "").strip()
    return org or None


def lookup_contact_rows(conn: sqlite3.Connection, participant: str, limit: int = 5) -> list[sqlite3.Row]:
    p = (participant or "").strip()
    if not p:
        return []
    if "@" in p and not p.startswith("+"):
        return conn.execute(
            """SELECT first_name, last_name, organization, phone, phone_normalized, email
               FROM contacts
               WHERE lower(email) = lower(?)
               ORDER BY imported_at DESC
               LIMIT ?""",
            (p, max(1, limit)),
        ).fetchall()

    phone_key = normalize_phone_for_lookup(p)
    if not phone_key:
        return []
    return conn.execute(
        """SELECT first_name, last_name, organization, phone, phone_normalized, email
           FROM contacts
           WHERE phone_normalized LIKE ?
           ORDER BY imported_at DESC
           LIMIT ?""",
        (f"%{phone_key}", max(1, limit)),
    ).fetchall()


def resolve_contact_name(conn: sqlite3.Connection, participant: str) -> Optional[str]:
    rows = lookup_contact_rows(conn, participant, limit=1)
    return contact_display_name(rows[0]) if rows else None


def _format_row(row: sqlite3.Row) -> str:
    name = contact_display_name(row) or "(no name)"
    phone = (row["phone"] or row["phone_normalized"] or "").strip()
    email = (row["email"] or "").strip()
    extras = ", ".join(x for x in [phone, email] if x)
    return f"{name} [{extras}]".rstrip()


def _refresh_contacts_if_requested(refresh: bool) -> None:
    if not refresh:
        return
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "import_contacts.py")]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    if result.returncode != 0:
        raise RuntimeError("contact import failed")


def _check_handles(conn: sqlite3.Connection, handles: list[str]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    unresolved = 0
    for raw in handles:
        participant = (raw or "").strip()
        rows = lookup_contact_rows(conn, participant, limit=5)
        resolved = contact_display_name(rows[0]) if rows else None
        if not resolved:
            unresolved += 1
        results.append(
            {
                "participant": participant,
                "resolved_name": resolved,
                "matches": [_format_row(r) for r in rows],
            }
        )
    return {"checked": len(results), "unresolved": unresolved, "results": results}


def _maybe_parse_participants(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        text = (item or "").strip() if isinstance(item, str) else ""
        if text:
            out.append(text)
    return out


def _looks_like_handle(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return False
    return ("@" in candidate) or bool(re.search(r"\d", candidate))


def _audit_active_conversations(
    conn: sqlite3.Connection,
    *,
    gmail_email: Optional[str],
    limit: int,
    show_unresolved: int,
) -> dict[str, Any]:
    params: list[Any] = []
    where = "WHERE status = 'active'"
    if gmail_email:
        where += " AND lower(gmail_email) = lower(?)"
        params.append(gmail_email)
    params.append(max(1, limit))

    rows = conn.execute(
        f"""SELECT conversation_id, display_name, participants
            FROM penguin_connect_conversations
            {where}
            ORDER BY updated_at DESC
            LIMIT ?""",
        params,
    ).fetchall()

    participant_checks = 0
    resolved_count = 0
    unresolved: list[dict[str, str]] = []

    for row in rows:
        participants = _maybe_parse_participants(row["participants"] or "[]")
        for participant in participants:
            if not _looks_like_handle(participant):
                continue
            participant_checks += 1
            resolved = resolve_contact_name(conn, participant)
            if resolved:
                resolved_count += 1
                continue
            if len(unresolved) < max(1, show_unresolved):
                unresolved.append(
                    {
                        "conversation_id": row["conversation_id"],
                        "display_name": row["display_name"] or "",
                        "participant": participant,
                    }
                )

    return {
        "conversations_checked": len(rows),
        "participants_checked": participant_checks,
        "participants_resolved": resolved_count,
        "participants_unresolved": max(0, participant_checks - resolved_count),
        "unresolved_samples": unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PenguinConnect contact resolution")
    parser.add_argument(
        "--handle",
        action="append",
        default=[],
        help="Specific phone/email handle to verify (repeatable)",
    )
    parser.add_argument(
        "--all-active",
        action="store_true",
        help="Audit participant resolution across active conversations",
    )
    parser.add_argument(
        "--gmail",
        default=None,
        help="Optional Gmail filter when auditing active conversations",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max active conversations to audit when --all-active is set",
    )
    parser.add_argument(
        "--show-unresolved",
        type=int,
        default=10,
        help="How many unresolved participant samples to print",
    )
    parser.add_argument(
        "--refresh-contacts",
        action="store_true",
        help="Run import_contacts.py before verification",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any requested handle or audited participant is unresolved",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    _load_env()
    _refresh_contacts_if_requested(args.refresh_contacts)
    db_path = cache_db_path()
    if not db_path.exists():
        print(f"[error] cache DB missing: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    contacts_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    output: dict[str, Any] = {
        "db_path": str(db_path),
        "contacts_count": contacts_count,
    }

    if args.handle:
        output["handle_check"] = _check_handles(conn, args.handle)

    if args.all_active or not args.handle:
        output["active_audit"] = _audit_active_conversations(
            conn,
            gmail_email=args.gmail,
            limit=args.limit,
            show_unresolved=args.show_unresolved,
        )

    conn.close()

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print("PenguinConnect Contact Resolution Verify")
        print(f"- DB: {output['db_path']}")
        print(f"- contacts rows: {output['contacts_count']}")

        handle_check = output.get("handle_check")
        if handle_check:
            print(f"- explicit handles checked: {handle_check['checked']}")
            print(f"- explicit unresolved: {handle_check['unresolved']}")
            for row in handle_check["results"]:
                name = row["resolved_name"] or "(unresolved)"
                print(f"  - {row['participant']} -> {name}")
                if row["matches"]:
                    print(f"    matches: {', '.join(row['matches'])}")

        active = output.get("active_audit")
        if active:
            print(f"- active conversations checked: {active['conversations_checked']}")
            print(f"- participants checked: {active['participants_checked']}")
            print(
                f"- participants resolved: {active['participants_resolved']} / {active['participants_checked']}"
            )
            if active["participants_unresolved"] > 0:
                print(f"- unresolved participants: {active['participants_unresolved']}")
                for item in active["unresolved_samples"]:
                    print(
                        f"  - {item['participant']} "
                        f"(conversation={item['conversation_id']}, name={item['display_name'] or 'n/a'})"
                    )

    unresolved = 0
    if output.get("handle_check"):
        unresolved += int(output["handle_check"]["unresolved"])
    if output.get("active_audit"):
        unresolved += int(output["active_audit"]["participants_unresolved"])

    if args.strict and unresolved > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
