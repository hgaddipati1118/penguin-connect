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

    def test_remote_server_exposes_only_whatsapp_tools(self):
        server = penguin_connect_mcp.create_mcp_server(
            host="127.0.0.1",
            port=8765,
            bearer_token="synthetic-secret-with-at-least-thirty-two-characters",
            remote_policy=penguin_connect_mcp.policy_for_profile("whatsapp"),
        )

        self.assertEqual(
            [tool.name for tool in server._tool_manager.list_tools()],
            ["search_whatsapp", "send_whatsapp"],
        )

    def test_slashy_profile_exposes_scoped_personal_messaging_tools(self):
        policy = penguin_connect_mcp.policy_for_profile("slashy")
        server = penguin_connect_mcp.create_mcp_server(
            host="127.0.0.1",
            port=8765,
            bearer_token="synthetic-secret-with-at-least-thirty-two-characters",
            remote_policy=policy,
        )

        self.assertEqual(
            [tool.name for tool in server._tool_manager.list_tools()],
            [
                "get_capabilities",
                "search_messages",
                "read_messages",
                "search_contacts",
                "send_message",
                "upsert_contact",
            ],
        )

    def test_read_only_profile_omits_all_remote_write_tools(self):
        policy = penguin_connect_mcp.policy_for_profile("read-only")
        server = penguin_connect_mcp.create_mcp_server(
            host="127.0.0.1",
            port=8765,
            bearer_token="synthetic-secret-with-at-least-thirty-two-characters",
            remote_policy=policy,
        )

        self.assertEqual(
            [tool.name for tool in server._tool_manager.list_tools()],
            ["get_capabilities", "search_messages", "read_messages", "search_contacts"],
        )

    def test_local_stdio_server_retains_full_toolset(self):
        server = penguin_connect_mcp.create_mcp_server()

        self.assertEqual(
            [tool.name for tool in server._tool_manager.list_tools()],
            [
                "search_contacts",
                "search_messages",
                "search_files",
                "semantic_search",
                "rebuild_local_search_index",
                "send_message",
            ],
        )

    def test_remote_whatsapp_search_does_not_return_contacts_or_other_channels(self):
        def fake_api(_method, path, **_kwargs):
            if path.startswith("/penguin-connect/conversations"):
                return {
                    "conversations": [
                        {
                            "conversation_id": "synthetic-chat",
                            "source_provider": "whatsapp",
                        },
                        {
                            "conversation_id": "private-imessage-chat",
                            "source_provider": "imessage",
                        },
                    ]
                }
            return {
                "messages": [
                    {
                        "conversation_id": "synthetic-chat",
                        "source_provider": "whatsapp",
                    },
                    {
                        "conversation_id": "private-imessage-chat",
                        "source_provider": "imessage",
                    },
                ]
            }

        whatsapp_adapter = mock.Mock()
        whatsapp_adapter.list_conversations.return_value = {
            "available": True,
            "chats": [{"chat_id": "synthetic-chat"}],
        }
        with mock.patch.object(
            penguin_connect_mcp,
            "_api_json",
            side_effect=fake_api,
        ), mock.patch.object(
            penguin_connect_mcp,
            "get_channel_adapter",
            return_value=whatsapp_adapter,
        ), mock.patch.object(
            penguin_connect_mcp,
            "search_contacts_data",
            side_effect=AssertionError("remote search must not access Mac Contacts"),
        ):
            result = penguin_connect_mcp.search_whatsapp_data("synthetic")

        self.assertNotIn("contacts", result)
        self.assertEqual(list(result["native_sources"]), ["whatsapp"])
        self.assertEqual(len(result["conversations"]), 1)
        self.assertEqual(len(result["messages"]), 1)

    def test_remote_send_requires_matching_one_time_confirmation_token(self):
        confirmations = penguin_connect_mcp.RemoteConfirmationStore()

        def fake_send(_recipient, _message, **kwargs):
            if kwargs.get("confirm"):
                return {"success": True}
            return {
                "success": False,
                "confirmation_required": True,
                "preview": {"action": "send_whatsapp"},
            }

        with mock.patch.object(
            penguin_connect_mcp,
            "send_message_data",
            side_effect=fake_send,
        ) as send, mock.patch.object(
            penguin_connect_mcp,
            "request_local_whatsapp_send_approval",
            return_value=True,
        ):
            preview = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Synthetic message",
            )
            rejected = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Different message",
                confirmation_token=preview["confirmation_token"],
            )
            replayed = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Synthetic message",
                confirmation_token=preview["confirmation_token"],
            )

        self.assertTrue(preview["confirmation_required"])
        self.assertEqual(rejected["error"], "invalid_or_expired_confirmation")
        self.assertEqual(replayed["error"], "invalid_or_expired_confirmation")
        self.assertFalse(any(call.kwargs.get("confirm") for call in send.mock_calls))

    def test_remote_send_consumes_valid_confirmation_before_sending(self):
        confirmations = penguin_connect_mcp.RemoteConfirmationStore()

        def fake_send(_recipient, _message, **kwargs):
            if kwargs.get("confirm"):
                return {"success": True}
            return {
                "success": False,
                "confirmation_required": True,
                "preview": {"action": "send_whatsapp"},
            }

        with mock.patch.object(
            penguin_connect_mcp,
            "send_message_data",
            side_effect=fake_send,
        ) as send, mock.patch.object(
            penguin_connect_mcp,
            "request_local_whatsapp_send_approval",
            return_value=True,
        ):
            preview = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Synthetic message",
            )
            sent = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Synthetic message",
                confirmation_token=preview["confirmation_token"],
            )
            replayed = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Synthetic message",
                confirmation_token=preview["confirmation_token"],
            )

        self.assertTrue(sent["success"])
        self.assertEqual(replayed["error"], "invalid_or_expired_confirmation")
        self.assertEqual(sum(bool(call.kwargs.get("confirm")) for call in send.mock_calls), 1)

    def test_remote_send_does_not_send_when_local_approval_is_denied(self):
        confirmations = penguin_connect_mcp.RemoteConfirmationStore()

        def fake_send(_recipient, _message, **kwargs):
            if kwargs.get("confirm"):
                return {"success": True}
            return {
                "success": False,
                "confirmation_required": True,
                "preview": {"action": "send_whatsapp"},
            }

        with mock.patch.object(
            penguin_connect_mcp,
            "send_message_data",
            side_effect=fake_send,
        ) as send, mock.patch.object(
            penguin_connect_mcp,
            "request_local_whatsapp_send_approval",
            return_value=False,
        ) as approve:
            preview = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Synthetic message",
            )
            denied = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "+15555550123",
                "Synthetic message",
                confirmation_token=preview["confirmation_token"],
            )

        self.assertEqual(denied["error"], "local_approval_denied_or_timed_out")
        approve.assert_called_once()
        self.assertFalse(any(call.kwargs.get("confirm") for call in send.mock_calls))

    def test_remote_send_rejects_contact_names_and_non_whatsapp_conversations(self):
        confirmations = penguin_connect_mcp.RemoteConfirmationStore()
        named = penguin_connect_mcp.remote_send_whatsapp_data(
            confirmations,
            "Synthetic Contact",
            "Synthetic message",
        )
        with mock.patch.object(
            penguin_connect_mcp,
            "_api_json",
            return_value={
                "conversations": [
                    {
                        "conversation_id": "private-imessage-chat",
                        "source_provider": "imessage",
                    }
                ]
            },
        ):
            wrong_provider = penguin_connect_mcp.remote_send_whatsapp_data(
                confirmations,
                "",
                "Synthetic message",
                conversation_id="private-imessage-chat",
            )

        self.assertEqual(named["error"], "whatsapp_recipient_must_be_phone_or_jid")
        self.assertEqual(wrong_provider["error"], "whatsapp_conversation_not_found")

    def test_remote_read_messages_rejects_provider_outside_policy(self):
        policy = penguin_connect_mcp.policy_for_profile("slashy")
        with mock.patch.object(
            penguin_connect_mcp,
            "_api_json",
            return_value={
                "conversations": [
                    {
                        "conversation_id": "private-slack-chat",
                        "source_provider": "slack",
                    }
                ]
            },
        ) as api:
            result = penguin_connect_mcp.read_remote_messages_data(
                "private-slack-chat",
                providers=policy.providers,
            )

        self.assertEqual(result["error"], "conversation_not_allowed")
        self.assertEqual(api.call_count, 1)

    def test_remote_read_messages_uses_compact_path_safe_response(self):
        policy = penguin_connect_mcp.policy_for_profile("slashy")

        def fake_api(_method, path, **_kwargs):
            if path.startswith("/penguin-connect/conversations?"):
                return {
                    "conversations": [
                        {
                            "conversation_id": "synthetic-imessage-chat",
                            "source_provider": "imessage",
                        }
                    ]
                }
            self.assertIn("compact=true", path)
            self.assertIn("sparse=true", path)
            return {
                "found": True,
                "messages": [
                    {
                        "provider_message_id": "synthetic-message",
                        "body_text": "Synthetic text",
                        "attachments": [{"filename": "example.pdf", "availability": "local"}],
                    }
                ],
            }

        with mock.patch.object(penguin_connect_mcp, "_api_json", side_effect=fake_api):
            result = penguin_connect_mcp.read_remote_messages_data(
                "synthetic-imessage-chat",
                providers=policy.providers,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["provider"], "imessage")
        self.assertEqual(result["messages"][0]["body_text"], "Synthetic text")
        self.assertNotIn("path", result["messages"][0]["attachments"][0])

    def test_remote_contact_search_uses_saved_contacts_without_message_context(self):
        def fake_api(_method, path, **_kwargs):
            self.assertIn("search=", path)
            self.assertIn("source=contacts", path)
            self.assertIn("include_counts=false", path)
            self.assertIn("include_thread_stats=false", path)
            return {
                "contacts": [
                    {
                        "contact_key": "synthetic-contact",
                        "display_name": "Synthetic Person",
                        "phone": "+15555550123",
                        "is_saved": True,
                    }
                ]
            }

        with mock.patch.object(penguin_connect_mcp, "_api_json", side_effect=fake_api):
            result = penguin_connect_mcp.search_remote_contacts_data("Synthetic")

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["contacts"][0]["contact_key"], "synthetic-contact")

    def test_generic_remote_send_supports_exact_imessage_conversation(self):
        policy = penguin_connect_mcp.policy_for_profile("slashy")
        confirmations = penguin_connect_mcp.RemoteConfirmationStore()
        conversation = {
            "conversation_id": "synthetic-imessage-chat",
            "source_provider": "imessage",
        }

        def fake_api(_method, path, **_kwargs):
            if path.startswith("/penguin-connect/conversations?"):
                return {"conversations": [conversation]}
            return {"success": True}

        with mock.patch.object(
            penguin_connect_mcp,
            "_api_json",
            side_effect=fake_api,
        ), mock.patch.object(
            penguin_connect_mcp,
            "request_local_mcp_approval",
            return_value=True,
        ):
            preview = penguin_connect_mcp.remote_send_message_data(
                confirmations,
                "",
                "Synthetic message",
                provider="imessage",
                conversation_id="synthetic-imessage-chat",
                providers=policy.providers,
            )
            sent = penguin_connect_mcp.remote_send_message_data(
                confirmations,
                "",
                "Synthetic message",
                provider="imessage",
                conversation_id="synthetic-imessage-chat",
                confirmation_token=preview["confirmation_token"],
                providers=policy.providers,
            )

        self.assertTrue(preview["confirmation_required"])
        self.assertTrue(sent["success"])

    def test_remote_contact_upsert_requires_exact_confirmation_and_local_approval(self):
        confirmations = penguin_connect_mcp.RemoteConfirmationStore()
        with mock.patch.object(
            penguin_connect_mcp,
            "request_local_mcp_approval",
            return_value=True,
        ) as approve, mock.patch.object(
            penguin_connect_mcp,
            "_api_json",
            return_value={"success": True, "updated": False},
        ) as api:
            preview = penguin_connect_mcp.remote_upsert_contact_data(
                confirmations,
                first_name="Synthetic",
                phone="+15555550123",
            )
            rejected = penguin_connect_mcp.remote_upsert_contact_data(
                confirmations,
                first_name="Changed",
                phone="+15555550123",
                confirmation_token=preview["confirmation_token"],
            )

        self.assertTrue(preview["confirmation_required"])
        self.assertEqual(rejected["error"], "invalid_or_expired_confirmation")
        approve.assert_not_called()
        api.assert_not_called()

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

    def test_remote_protocol_rejects_hidden_local_tool(self):
        from starlette.testclient import TestClient

        token = "synthetic-secret-with-at-least-thirty-two-characters"
        server = penguin_connect_mcp.create_mcp_server(
            host="127.0.0.1",
            port=8765,
            bearer_token=token,
            remote_policy=penguin_connect_mcp.policy_for_profile("whatsapp"),
        )
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        with TestClient(
            server.streamable_http_app(),
            base_url="http://127.0.0.1:8765",
        ) as client:
            listed = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers=headers,
            )
            hidden = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "search_files", "arguments": {"query": "private"}},
                },
                headers=headers,
            )

        self.assertEqual(
            [tool["name"] for tool in listed.json()["result"]["tools"]],
            ["search_whatsapp", "send_whatsapp"],
        )
        self.assertTrue(hidden.json()["result"]["isError"])
        self.assertIn("Unknown tool", hidden.json()["result"]["content"][0]["text"])

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
