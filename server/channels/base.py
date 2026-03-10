"""Provider adapter contracts for source messaging channels."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable, Optional, Protocol

LookupContactName = Callable[[sqlite3.Connection, str], Optional[str]]
LooksLikeUnresolvedHandle = Callable[[str], bool]


class MessagingChannelAdapter(Protocol):
    provider: str
    provider_label: str

    def list_conversations(self, search: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
        """Return provider conversations for discovery."""

    def list_recent_activity(self, since: str, limit: int = 500) -> dict[str, Any]:
        """Return provider conversations with activity since the given timestamp."""

    def fetch_messages(self, chat_id: str, limit: int = 50, since: Optional[str] = None) -> list[dict[str, Any]]:
        """Return provider messages for the source conversation."""

    def send_message(
        self,
        chat_identifier: str,
        message_text: str,
        attachment_paths: Optional[list[str]] = None,
    ) -> tuple[bool, Optional[str]]:
        """Send an outbound message to the source provider."""

    def get_unread_count(self, chat_identifier: str) -> Optional[int]:
        """Return an unread-count hint when the provider supports it."""

    def resolve_sender_and_subject(
        self,
        conn: sqlite3.Connection,
        conv: sqlite3.Row,
        msg: dict[str, Any],
        *,
        lookup_contact_name: LookupContactName,
        looks_like_unresolved_handle: LooksLikeUnresolvedHandle,
    ) -> tuple[str, str]:
        """Return sender and subject labels for Gmail mirroring."""
