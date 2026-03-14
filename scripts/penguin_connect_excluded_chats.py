#!/usr/bin/env python3
"""Manage PenguinConnect conversation exclusions from a user-editable JSON file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT_DIR / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import browse_sources
import db
from conversation_exclusions import (
    apply_excluded_chats_to_account,
    build_excluded_chat_entry,
    exclusion_identity_key,
    find_matching_excluded_chat,
    load_excluded_chats,
    resolve_excluded_chats_path,
    write_excluded_chats,
)


def _service_rank(service_name: str | None) -> int:
    normalized = (service_name or "").strip().lower()
    if normalized == "imessage":
        return 0
    if normalized == "rcs":
        return 1
    if normalized == "sms":
        return 2
    return 3


def _select_active_chat(chats: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        chats,
        key=lambda chat: (
            chat.get("last_message_at") or "",
            -_service_rank(chat.get("service") or chat.get("source_provider")),
            chat.get("chat_id") or "",
        ),
        reverse=True,
    )[0]


def _connected_gmail_email() -> str:
    if not db.DB_PATH.exists():
        return ""
    conn = db.get_connection()
    try:
        row = conn.execute(
            """SELECT gmail_email
               FROM penguin_connect_accounts
               WHERE status = 'connected'
               ORDER BY updated_at DESC, id DESC
               LIMIT 1"""
        ).fetchone()
        return (row["gmail_email"] or "").strip() if row else ""
    finally:
        conn.close()


def _build_logical_chat_rows(
    chats: list[dict[str, Any]],
    *,
    gmail_email: str,
    exclusions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    ordered_keys: list[str] = []
    for chat in chats:
        entry = build_excluded_chat_entry(chat, gmail_email=gmail_email)
        thread_key = entry.get("thread_key") or entry.get("chat_id")
        if not thread_key:
            continue
        if thread_key not in grouped:
            ordered_keys.append(thread_key)
            grouped[thread_key] = []
        grouped[thread_key].append(chat)

    logical_rows: list[dict[str, Any]] = []
    for index, thread_key in enumerate(ordered_keys, start=1):
        thread_chats = grouped[thread_key]
        active_chat = _select_active_chat(thread_chats)
        entry = build_excluded_chat_entry(active_chat, gmail_email=gmail_email)
        existing = find_matching_excluded_chat(
            {
                entry.get("conversation_id", ""),
                entry.get("thread_key", ""),
                entry.get("chat_id", ""),
                entry.get("chat_identifier", ""),
            },
            exclusions=exclusions,
        )
        logical_rows.append(
            {
                "index": index,
                "display_name": active_chat.get("name") or active_chat.get("chat_identifier") or active_chat.get("chat_id"),
                "chat_type": active_chat.get("chat_type") or "group",
                "participants": active_chat.get("participants") or [],
                "last_message_at": active_chat.get("last_message_at") or "",
                "last_message_preview": active_chat.get("last_message_preview") or "",
                "routes": sorted(
                    {
                        (chat.get("service") or chat.get("source_provider") or "iMessage")
                        for chat in thread_chats
                    }
                ),
                "entry": entry,
                "existing_entry": existing,
                "excluded": bool(existing),
            }
        )
    return logical_rows


def _print_rows(rows: list[dict[str, Any]], file_path: Path, gmail_email: str) -> None:
    print(f"Excluded chats file: {file_path}")
    if gmail_email:
        print(f"Connected Gmail account: {gmail_email}")
    print("")
    if not rows:
        print("No Apple Messages chats found.")
        return

    for row in rows:
        marker = "x" if row["excluded"] else " "
        routes = ",".join(row["routes"])
        participants = ", ".join(row["participants"][:3]) or "n/a"
        if len(row["participants"]) > 3:
            participants += f" +{len(row['participants']) - 3}"
        print(
            f"{row['index']:>3}. [{marker}] {row['display_name']} | {row['chat_type']} | "
            f"routes={routes} | last={row['last_message_at'] or 'n/a'}"
        )
        print(f"     participants={participants}")
        if row["last_message_preview"]:
            print(f"     preview={row['last_message_preview']}")


def _parse_selection(raw_value: str, max_index: int) -> set[int]:
    selected: set[int] = set()
    for chunk in raw_value.replace(",", " ").split():
        token = chunk.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left)
            end = int(right)
            if start > end:
                start, end = end, start
            for value in range(start, end + 1):
                if value < 1 or value > max_index:
                    raise ValueError(f"Selection {value} is out of range.")
                selected.add(value)
            continue
        value = int(token)
        if value < 1 or value > max_index:
            raise ValueError(f"Selection {value} is out of range.")
        selected.add(value)
    return selected


def _toggle_entries(
    rows: list[dict[str, Any]],
    existing_entries: list[dict[str, Any]],
    *,
    selected_indexes: set[int],
    reason: str = "",
) -> list[dict[str, Any]]:
    entries_by_identity = {
        identity: dict(entry)
        for entry in existing_entries
        for identity in [exclusion_identity_key(entry)]
        if identity
    }

    rows_by_index = {row["index"]: row for row in rows}
    for index in selected_indexes:
        row = rows_by_index[index]
        entry = dict(row["existing_entry"] or row["entry"])
        if reason:
            entry["reason"] = reason
        identity = exclusion_identity_key(entry)
        if not identity:
            continue
        if identity in entries_by_identity:
            entries_by_identity.pop(identity, None)
        else:
            entries_by_identity[identity] = entry
    return list(entries_by_identity.values())


def _apply_to_local_cache(exclusions: list[dict[str, Any]]) -> None:
    if not db.DB_PATH.exists():
        print(f"[info] Cache DB not found at {db.DB_PATH}; saved file only.")
        return

    db.init_db()
    conn = db.get_connection()
    try:
        row = conn.execute(
            """SELECT gmail_email
               FROM penguin_connect_accounts
               WHERE status = 'connected'
               ORDER BY updated_at DESC, id DESC
               LIMIT 1"""
        ).fetchone()
        if not row:
            print("[info] No connected Gmail account in cache; saved file only.")
            return
        result = apply_excluded_chats_to_account(conn, row["gmail_email"], exclusions=exclusions)
        conn.commit()
        print(
            f"[ok] Applied exclusions to local cache for {row['gmail_email']} "
            f"(updated={result['updated']}, excluded={result['excluded']}, included={result['included']})."
        )
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage PenguinConnect excluded chats")
    parser.add_argument("--search", help="Filter Apple Messages chats by text")
    parser.add_argument("--limit", type=int, default=200, help="Max Apple Messages chats to browse (default 200)")
    parser.add_argument(
        "--file",
        help="Override exclusion JSON path (defaults to PENGUIN_CONNECT_EXCLUDED_CHATS_FILE or the repo default)",
    )
    parser.add_argument("--list", action="store_true", help="Print chats and current exclusion state, then exit")
    parser.add_argument("--toggle", help="Toggle exclusions by number (comma or range syntax, for example 1,3-5)")
    parser.add_argument("--reason", default="", help="Optional reason to store on newly added exclusions")
    args = parser.parse_args()

    file_path = Path(args.file).expanduser() if args.file else resolve_excluded_chats_path()
    exclusions = load_excluded_chats(file_path)
    gmail_email = _connected_gmail_email()

    result = browse_sources.browse_imessage_chats(search=args.search, limit=args.limit)
    if not result.get("available"):
        print(f"[error] {result.get('reason') or 'Unable to browse Apple Messages chats.'}", file=sys.stderr)
        return 1

    rows = _build_logical_chat_rows(result.get("chats", []), gmail_email=gmail_email, exclusions=exclusions)
    _print_rows(rows, file_path, gmail_email)

    if args.list:
        return 0

    if not rows:
        print("[info] Nothing to update.")
        return 0

    raw_selection = (args.toggle or "").strip()
    if not raw_selection:
        raw_selection = input("\nToggle exclusions by number (for example 1,3-5). Press Enter to keep current: ").strip()
        if not raw_selection:
            print("[ok] No changes.")
            return 0

    try:
        selected_indexes = _parse_selection(raw_selection, len(rows))
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    updated_entries = _toggle_entries(rows, exclusions, selected_indexes=selected_indexes, reason=args.reason.strip())
    written_path = write_excluded_chats(updated_entries, file_path)
    print(f"[ok] Saved {len(updated_entries)} excluded chat entry(s) to {written_path}")
    if args.file:
        print(
            "[note] Point PENGUIN_CONNECT_EXCLUDED_CHATS_FILE at the same path "
            "if you want the bridge runtime to use this custom file."
        )
    _apply_to_local_cache(load_excluded_chats(written_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
