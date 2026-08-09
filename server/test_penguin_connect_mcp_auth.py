"""Tests for the remote MCP bearer-token Keychain helper."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "penguin_connect_mcp_auth.py"
SPEC = importlib.util.spec_from_file_location("penguin_connect_mcp_auth", SCRIPT_PATH)
assert SPEC and SPEC.loader
penguin_connect_mcp_auth = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = penguin_connect_mcp_auth
SPEC.loader.exec_module(penguin_connect_mcp_auth)


class PenguinConnectMcpAuthTests(unittest.TestCase):
    def test_generated_token_has_high_entropy_shape(self):
        token = penguin_connect_mcp_auth.generate_token()

        self.assertGreaterEqual(len(token), 64)
        self.assertNotIn(" ", token)

    def test_environment_override_precedes_keychain(self):
        with mock.patch.dict(
            os.environ,
            {"PENGUIN_CONNECT_MCP_BEARER_TOKEN": "synthetic-env-token"},
        ), mock.patch.object(
            penguin_connect_mcp_auth,
            "read_keychain_token",
            return_value="synthetic-keychain-token",
        ):
            token = penguin_connect_mcp_auth.load_token()

        self.assertEqual(token, "synthetic-env-token")

    def test_ensure_token_preserves_existing_secret(self):
        with mock.patch.object(
            penguin_connect_mcp_auth,
            "read_keychain_token",
            return_value="synthetic-existing-token",
        ), mock.patch.object(penguin_connect_mcp_auth, "store_keychain_token") as store:
            token, created = penguin_connect_mcp_auth.ensure_token()

        self.assertEqual(token, "synthetic-existing-token")
        self.assertFalse(created)
        store.assert_not_called()

    def test_rotate_restarts_the_loaded_remote_service(self):
        with mock.patch.object(
            penguin_connect_mcp_auth,
            "generate_token",
            return_value="synthetic-rotated-token",
        ), mock.patch.object(
            penguin_connect_mcp_auth,
            "store_keychain_token",
        ) as store, mock.patch.object(
            penguin_connect_mcp_auth,
            "restart_remote_mcp_service",
            return_value=True,
        ) as restart, mock.patch.object(
            sys,
            "argv",
            ["penguin_connect_mcp_auth.py", "--rotate"],
        ), redirect_stdout(io.StringIO()):
            result = penguin_connect_mcp_auth.main()

        self.assertEqual(result, 0)
        store.assert_called_once_with("synthetic-rotated-token")
        restart.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
