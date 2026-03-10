#!/usr/bin/env python3
"""Audit stored Gmail-to-chat deliveries against the current quote parser."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from db import get_connection
from penguin_connect import _build_gmail_service, _extract_gmail_body_variants, get_connected_account
from quoted_content import extract_latest_email_text

QUOTE_MARKER_RE = re.compile(r"Quoted context:|^>+|On .+ wrote:|Forwarded message", re.IGNORECASE | re.MULTILINE)


def _preview(value: str, limit: int = 140) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _audit(limit: int, *, rewrite_db: bool = False) -> dict:
    conn = get_connection()
    try:
        account = get_connected_account(conn)
        if not account:
            return {"success": False, "error": "gmail_not_connected"}

        gmail_service, err = _build_gmail_service(account["gmail_email"], account["keychain_service"])
        if err or not gmail_service:
            return {"success": False, "error": err or "failed_to_build_gmail_service"}

        rows = conn.execute(
            """
            SELECT m.provider_message_id, m.gmail_message_id, m.gmail_thread_id, m.body_text, m.message_timestamp,
                   c.conversation_id, c.display_name, c.source_provider, c.imessage_chat_id
            FROM penguin_connect_messages m
            JOIN penguin_connect_conversations c ON c.conversation_id = m.conversation_id
            WHERE m.provider = 'gmail'
              AND m.direction = 'email_to_imessage'
              AND m.gmail_message_id IS NOT NULL
            ORDER BY m.message_timestamp DESC, m.id DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()

        issues = []
        audited = 0
        fetch_errors = 0
        clean = 0
        updated = 0
        for row in rows:
            audited += 1
            try:
                full = gmail_service.users().messages().get(
                    userId="me",
                    id=row["gmail_message_id"],
                    format="full",
                ).execute()
            except Exception as exc:
                fetch_errors += 1
                issues.append(
                    {
                        "conversation_id": row["conversation_id"],
                        "display_name": row["display_name"],
                        "gmail_message_id": row["gmail_message_id"],
                        "status": "fetch_error",
                        "error": str(exc).strip() or exc.__class__.__name__,
                    }
                )
                continue

            payload = full.get("payload") or {}
            plain_text, html_text = _extract_gmail_body_variants(payload)
            parsed = extract_latest_email_text(
                plain_text=plain_text,
                html_text=html_text,
                snippet=full.get("snippet") or "",
            )
            stored = (row["body_text"] or "").strip()
            stored_reparsed = extract_latest_email_text(plain_text=stored)

            reasons = []
            if stored != parsed.text:
                reasons.append("stored_differs_from_current_parser")
            if QUOTE_MARKER_RE.search(stored):
                reasons.append("stored_contains_quote_markers")
            if stored_reparsed.text != stored:
                reasons.append("stored_body_still_contains_noise")

            if reasons:
                if rewrite_db and "fetch_error" not in reasons:
                    metadata_row = conn.execute(
                        "SELECT id, metadata FROM penguin_connect_messages WHERE provider_message_id = ? LIMIT 1",
                        (row["provider_message_id"],),
                    ).fetchone()
                    if metadata_row:
                        metadata = json.loads(metadata_row["metadata"] or "{}")
                        metadata["source_body_text"] = parsed.text
                        if plain_text:
                            metadata["source_body_text_raw"] = plain_text
                        if html_text:
                            metadata["source_body_html_raw"] = html_text
                        metadata["gmail_body_source"] = parsed.source
                        metadata["gmail_quoted_content_removed"] = parsed.quoted_content_removed
                        metadata["gmail_signature_removed"] = parsed.signature_removed
                        metadata["gmail_body_safe_for_send"] = parsed.safe_for_send
                        metadata["gmail_body_safety_flags"] = list(parsed.safety_flags)
                        conn.execute(
                            "UPDATE penguin_connect_messages SET body_text = ?, metadata = ? WHERE id = ?",
                            (parsed.text[:20000], json.dumps(metadata), metadata_row["id"]),
                        )
                        conn.commit()
                        updated += 1
                issues.append(
                    {
                        "conversation_id": row["conversation_id"],
                        "display_name": row["display_name"],
                        "source_provider": row["source_provider"],
                        "source_chat_id": row["imessage_chat_id"],
                        "gmail_message_id": row["gmail_message_id"],
                        "gmail_thread_id": row["gmail_thread_id"],
                        "message_timestamp": row["message_timestamp"],
                        "reasons": reasons,
                        "stored_preview": _preview(stored),
                        "parsed_preview": _preview(parsed.text),
                    }
                )
            else:
                clean += 1

        return {
            "success": True,
            "gmail_email": account["gmail_email"],
            "audited": audited,
            "clean": clean,
            "issues": len(issues),
            "fetch_errors": fetch_errors,
            "updated": updated,
            "issue_samples": issues[:20],
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="number of recent Gmail-to-chat deliveries to audit")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON only")
    parser.add_argument("--rewrite-db", action="store_true", help="rewrite stored Gmail-to-chat bodies when the parser now produces cleaner text")
    args = parser.parse_args()

    result = _audit(args.limit, rewrite_db=args.rewrite_db)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if not result.get("success"):
            print(json.dumps(result, indent=2))
            return 1
        print("PenguinConnect Quote Parsing Audit")
        print(f"- gmail: {result['gmail_email']}")
        print(f"- audited: {result['audited']}")
        print(f"- clean: {result['clean']}")
        print(f"- issues: {result['issues']}")
        print(f"- fetch_errors: {result['fetch_errors']}")
        print(f"- updated: {result['updated']}")
        if result["issue_samples"]:
            print("- samples:")
            for sample in result["issue_samples"]:
                print(
                    f"  - {sample['display_name'] or sample['conversation_id']} | "
                    f"{sample.get('message_timestamp') or 'unknown'} | "
                    f"{', '.join(sample.get('reasons') or [])}"
                )
                print(f"    stored: {sample.get('stored_preview')}")
                print(f"    parsed: {sample.get('parsed_preview')}")
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
