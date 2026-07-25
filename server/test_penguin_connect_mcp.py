"""Tests for PenguinConnect MCP search/send routing helpers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "penguin_connect_mcp.py"
SPEC = importlib.util.spec_from_file_location("penguin_connect_mcp", SCRIPT_PATH)
assert SPEC and SPEC.loader
penguin_connect_mcp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = penguin_connect_mcp
SPEC.loader.exec_module(penguin_connect_mcp)


class PenguinConnectMcpTests(unittest.TestCase):
    def test_existing_conversation_send_requires_confirmation(self):
        conversation = {
            "conversation_id": "conversation-1",
            "display_name": "Synthetic Contact",
            "source_provider": "whatsapp",
            "participants": ["15555550123"],
        }
        with mock.patch.object(
            penguin_connect_mcp,
            "_recipient_handle",
            return_value=("+15555550123", []),
        ), mock.patch.object(
            penguin_connect_mcp,
            "_matching_cached_conversations",
            return_value=[conversation],
        ):
            result = penguin_connect_mcp.send_message_data(
                "Synthetic Contact",
                "Synthetic message",
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["confirmation_required"])
        self.assertEqual(
            result["preview"]["conversation_id"],
            "conversation-1",
        )

    def test_new_whatsapp_recipient_can_be_previewed_without_cached_chat(self):
        with mock.patch.object(
            penguin_connect_mcp,
            "_recipient_handle",
            return_value=("+15555550123", []),
        ), mock.patch.object(
            penguin_connect_mcp,
            "_matching_cached_conversations",
            return_value=[],
        ):
            result = penguin_connect_mcp.send_message_data(
                "+15555550123",
                "Synthetic message",
                provider="whatsapp",
            )

        self.assertTrue(result["confirmation_required"])
        self.assertEqual(
            result["preview"]["recipient"],
            "15555550123@s.whatsapp.net",
        )

    def test_ambiguous_contact_is_not_sent(self):
        candidates = [
            {"name": "Synthetic A", "primary_handle": "+15555550111"},
            {"name": "Synthetic B", "primary_handle": "+15555550122"},
        ]
        with mock.patch.object(
            penguin_connect_mcp,
            "_recipient_handle",
            return_value=("", candidates),
        ):
            result = penguin_connect_mcp.send_message_data(
                "Synthetic",
                "Synthetic message",
                confirm=True,
            )

        self.assertEqual(result["error"], "recipient_ambiguous_or_not_found")
        self.assertEqual(len(result["contact_candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
