"""Tests for the remote MCP bearer-token Keychain helper."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from datetime import date
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

    def test_daily_code_is_stable_for_day_and_changes_next_day(self):
        secret = "synthetic-daily-code-secret"

        first = penguin_connect_mcp_auth.daily_access_code(
            secret,
            day=date(2026, 8, 9),
        )
        repeated = penguin_connect_mcp_auth.daily_access_code(
            secret,
            day=date(2026, 8, 9),
        )
        next_day = penguin_connect_mcp_auth.daily_access_code(
            secret,
            day=date(2026, 8, 10),
        )

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, next_day)
        self.assertEqual(len(first), 6)
        self.assertTrue(set(first).issubset(set(penguin_connect_mcp_auth.DAILY_CODE_ALPHABET)))

    def test_connection_token_requires_valid_six_character_code(self):
        token = penguin_connect_mcp_auth.connection_token(
            "synthetic-long-install-token",
            "AB7K9Z",
        )

        self.assertEqual(token, "synthetic-long-install-token.AB7K9Z")
        with self.assertRaises(ValueError):
            penguin_connect_mcp_auth.connection_token(
                "synthetic-long-install-token",
                "123456",
            )

    def test_ensure_daily_secret_preserves_existing_secret(self):
        with mock.patch.object(
            penguin_connect_mcp_auth,
            "read_keychain_daily_code_secret",
            return_value="synthetic-existing-daily-secret",
        ), mock.patch.object(
            penguin_connect_mcp_auth,
            "store_keychain_daily_code_secret",
        ) as store:
            secret, created = penguin_connect_mcp_auth.ensure_daily_code_secret()

        self.assertEqual(secret, "synthetic-existing-daily-secret")
        self.assertFalse(created)
        store.assert_not_called()

    def test_copy_uses_composed_daily_access_token_not_bare_bearer(self):
        completed = penguin_connect_mcp_auth.subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            penguin_connect_mcp_auth,
            "load_token",
            return_value="synthetic-long-bearer",
        ), mock.patch.object(
            penguin_connect_mcp_auth,
            "connection_token",
            return_value="synthetic-long-bearer.AB7K9Z",
        ), mock.patch.object(
            penguin_connect_mcp_auth.subprocess,
            "run",
            return_value=completed,
        ) as run, redirect_stdout(io.StringIO()):
            result = penguin_connect_mcp_auth._copy_token()

        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.kwargs["input"], "synthetic-long-bearer.AB7K9Z")

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
