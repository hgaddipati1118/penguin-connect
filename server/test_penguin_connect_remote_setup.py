"""Tests for the one-command remote PenguinConnect MCP setup flow."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "penguin_connect_remote_setup.py"
SPEC = importlib.util.spec_from_file_location("penguin_connect_remote_setup", SCRIPT_PATH)
assert SPEC and SPEC.loader
remote_setup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = remote_setup
SPEC.loader.exec_module(remote_setup)


class PenguinConnectRemoteSetupTests(unittest.TestCase):
    def test_extract_quick_tunnel_url_uses_last_cloudflare_origin(self):
        log = """
        old https://old-name.trycloudflare.com
        INF +---------------------------------------------+
        INF | https://new-name.trycloudflare.com          |
        """
        self.assertEqual(
            remote_setup.extract_quick_tunnel_url(log),
            "https://new-name.trycloudflare.com",
        )

    def test_extract_quick_tunnel_url_rejects_lookalike_host(self):
        self.assertEqual(
            remote_setup.extract_quick_tunnel_url(
                "https://safe.trycloudflare.com.evil.example"
            ),
            "",
        )

    def test_connection_bundle_matches_slashy_generic_mcp_contract(self):
        policy = remote_setup.policy_for_profile("slashy")
        bundle = remote_setup.build_connection_bundle(
            "https://synthetic.trycloudflare.com",
            "synthetic-secret-token",
            policy,
        )

        self.assertEqual(bundle["server_url"], "https://synthetic.trycloudflare.com/mcp")
        self.assertEqual(bundle["token"], "synthetic-secret-token")
        self.assertEqual(bundle["transport"], "streamable_http")
        self.assertEqual(bundle["profile"], "slashy")
        self.assertNotIn("local_path", json.dumps(bundle))

    def test_endpoint_state_is_owner_only_and_never_contains_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "endpoint.json"
            remote_setup.save_endpoint_state(
                "https://synthetic.trycloudflare.com",
                remote_setup.policy_for_profile("read-only"),
                path=path,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(payload["origin"], "https://synthetic.trycloudflare.com")
            self.assertNotIn("token", payload)

    def test_normalize_public_origin_rejects_paths_and_non_https(self):
        for value in (
            "http://mcp.example.com",
            "https://mcp.example.com/not-mcp",
            "https://user:pass@mcp.example.com",
            "https://127.0.0.1",
            "https://192.168.1.20",
            "https://penguin.local",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    remote_setup.normalize_public_origin(value)


if __name__ == "__main__":
    unittest.main()
