import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

import app as app_module

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import penguin_agent_tools_mcp as agent_tools  # noqa: E402


class AgentEngineTest(unittest.TestCase):
    def test_engine_defaults_to_codex_and_rejects_unknown(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertEqual(app_module._agent_engine(""), "codex")
        self.assertEqual(app_module._agent_engine("Claude"), "claude")
        with self.assertRaises(HTTPException):
            app_module._agent_engine("gpt")

    def test_codex_gets_read_only_message_tools(self):
        overrides = app_module._codex_agent_tools_overrides()
        joined = " ".join(overrides)
        self.assertIn("mcp_servers.penguin_messages.command=", joined)
        self.assertIn("penguin_agent_tools_mcp.py", joined)
        self.assertIn("PENGUIN_AGENT_API_BASE", joined)

    def test_claude_read_mode_only_allows_read_tools(self):
        command = app_module._claude_agent_command("/bin/claude", "read")
        self.assertIn("--allowedTools", command)
        self.assertNotIn("--dangerously-skip-permissions", command)
        allowed = command[command.index("--allowedTools") + 1 :]
        self.assertEqual(allowed, ["mcp__penguin_messages", "Read", "Grep", "Glob"])
        config = json.loads(command[command.index("--mcp-config") + 1])
        self.assertIn("penguin_messages", config["mcpServers"])

    def test_claude_confirmed_modes_skip_permissions(self):
        self.assertIn("--dangerously-skip-permissions", app_module._claude_agent_command("/bin/claude", "ask"))

    def test_translates_claude_stream_events(self):
        translate = app_module._claude_stream_to_agent_events
        self.assertEqual(translate({"type": "system", "subtype": "init"}), [{"type": "turn.started"}])
        started = translate({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "mcp__penguin_messages__search_messages", "input": {}},
            ]},
        })
        self.assertEqual(started[0]["item"]["type"], "mcp_tool_call")
        self.assertEqual(started[0]["item"]["tool"], "search_messages")
        answer = translate({"type": "assistant", "message": {"content": [{"type": "text", "text": "Found it"}]}})
        self.assertEqual(answer, [{"type": "item.completed", "item": {"type": "agent_message", "text": "Found it"}}])
        done = translate({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}})
        self.assertEqual(done[0]["item"]["status"], "completed")
        self.assertEqual(translate({"type": "result", "usage": {"x": 1}}), [{"type": "turn.completed", "usage": {"x": 1}}])
        failed = translate({"type": "result", "is_error": True, "result": "boom"})
        self.assertEqual(failed[0], {"type": "error", "message": "boom"})


class AgentToolsTest(unittest.TestCase):
    def test_tool_set_is_read_only(self):
        names = {tool.__name__ for tool in agent_tools.TOOLS}
        self.assertEqual(names, {"search_messages", "read_conversation", "search_contacts", "list_conversations"})

    def test_search_messages_shapes_hybrid_results(self):
        payload = {"results": [
            {"kind": "message", "title": "Lease crew", "timestamp": "2024-01-01", "provider": "imessage",
             "snippet": "Me | lease is due", "metadata": {"conversation_id": "amc_1"}},
            {"kind": "file", "title": "x.pdf"},
        ]}
        with mock.patch.object(agent_tools, "_get", return_value=payload) as get:
            result = agent_tools.search_messages("lease", limit=5)
        self.assertEqual(get.call_args.args[1]["include_spotlight"], "false")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["conversation_id"], "amc_1")

    def test_read_conversation_does_not_trigger_source_refresh(self):
        with mock.patch.object(agent_tools, "_get", return_value={"messages": []}) as get:
            agent_tools.read_conversation("amc_1")
        self.assertEqual(get.call_args.args[1]["refresh"], "false")


if __name__ == "__main__":
    unittest.main()
