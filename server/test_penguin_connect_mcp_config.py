"""Tests for installable remote MCP access profiles."""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "penguin_connect_mcp_config.py"
SPEC = importlib.util.spec_from_file_location("penguin_connect_mcp_config", SCRIPT_PATH)
assert SPEC and SPEC.loader
penguin_connect_mcp_config = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = penguin_connect_mcp_config
SPEC.loader.exec_module(penguin_connect_mcp_config)


class PenguinConnectMcpConfigTests(unittest.TestCase):
    def test_slashy_profile_has_explicit_personal_messaging_scopes(self):
        policy = penguin_connect_mcp_config.policy_for_profile("slashy")

        self.assertEqual(policy.providers, ("imessage", "whatsapp"))
        self.assertEqual(
            policy.scopes,
            (
                "messages.read",
                "contacts.read",
                "messages.send",
                "contacts.write",
                "groups.create",
            ),
        )
        self.assertTrue(policy.daily_code_required)

    def test_legacy_local_approval_policy_upgrades_to_daily_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote-mcp.json"
            path.write_text(
                '{"profile":"slashy","scopes":["messages.read","contacts.read",'
                '"messages.send","contacts.write","groups.create"],"providers":["imessage","whatsapp"],'
                '"local_approval_required":true}',
                encoding="utf-8",
            )

            policy = penguin_connect_mcp_config.load_remote_policy(path)

        self.assertEqual(policy.profile, "slashy")
        self.assertTrue(policy.daily_code_required)

    def test_missing_config_preserves_legacy_whatsapp_only_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = penguin_connect_mcp_config.load_remote_policy(
                Path(tmp) / "missing.json"
            )

        self.assertEqual(policy.profile, "whatsapp")
        self.assertEqual(policy.providers, ("whatsapp",))
        self.assertEqual(policy.scopes, ("messages.read", "messages.send"))

    def test_policy_round_trip_uses_owner_only_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote-mcp.json"
            expected = penguin_connect_mcp_config.policy_for_profile("read-only")

            penguin_connect_mcp_config.save_remote_policy(expected, path)
            actual = penguin_connect_mcp_config.load_remote_policy(path)

            self.assertEqual(actual, expected)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_invalid_config_fails_closed_to_legacy_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote-mcp.json"
            path.write_text('{"profile":"slashy","scopes":["files.read"]}', encoding="utf-8")

            policy = penguin_connect_mcp_config.load_remote_policy(path)

        self.assertEqual(policy.profile, "whatsapp")


if __name__ == "__main__":
    unittest.main()
