"""Tests for PenguinConnect MCP search/send routing helpers."""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr
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


class PenguinConnectRemoteMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_static_bearer_verifier_accepts_only_exact_token(self):
        token = "synthetic-secret-with-at-least-thirty-two-characters"
        verifier = penguin_connect_mcp.StaticBearerTokenVerifier(token)

        accepted = await verifier.verify_token(token)
        rejected = await verifier.verify_token(f"{token}-with-suffix")

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.scopes, ["penguin-connect"])
        self.assertIsNone(rejected)

    def test_remote_server_uses_loopback_and_stateless_http(self):
        server = penguin_connect_mcp.create_mcp_server(
            host="127.0.0.1",
            port=8765,
            bearer_token="synthetic-secret-with-at-least-thirty-two-characters",
        )

        self.assertEqual(server.settings.host, "127.0.0.1")
        self.assertEqual(server.settings.port, 8765)
        self.assertTrue(server.settings.stateless_http)
        self.assertIsNotNone(server._token_verifier)

    def test_remote_server_rejects_non_loopback_bind(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            penguin_connect_mcp.create_mcp_server(
                host="0.0.0.0",
                port=8765,
                bearer_token="synthetic-secret-with-at-least-thirty-two-characters",
            )

    def test_remote_server_rejects_weak_bearer_token(self):
        with self.assertRaisesRegex(ValueError, "at least 32"):
            penguin_connect_mcp.create_mcp_server(
                host="127.0.0.1",
                port=8765,
                bearer_token="too-short",
            )

    def test_remote_http_rejects_missing_or_wrong_bearer_token(self):
        from starlette.testclient import TestClient

        server = penguin_connect_mcp.create_mcp_server(
            host="127.0.0.1",
            port=8765,
            bearer_token="synthetic-secret-with-at-least-thirty-two-characters",
        )
        with TestClient(server.streamable_http_app()) as client:
            missing = client.post(
                "/mcp",
                json={},
                headers={"Accept": "application/json, text/event-stream"},
            )
            wrong = client.post(
                "/mcp",
                json={},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": "Bearer wrong-secret",
                },
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)

    def test_streamable_http_startup_requires_configured_token(self):
        with mock.patch.object(
            penguin_connect_mcp,
            "load_token",
            return_value="",
        ), mock.patch.object(
            sys,
            "argv",
            ["penguin_connect_mcp.py", "--transport", "streamable-http"],
        ):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    penguin_connect_mcp.main()

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
