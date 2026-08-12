"""Tests for Penguin's loopback-only BlueBubbles integration."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "penguin_connect_bluebubbles.py"
SPEC = importlib.util.spec_from_file_location("penguin_connect_bluebubbles", SCRIPT_PATH)
assert SPEC and SPEC.loader
penguin_connect_bluebubbles = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = penguin_connect_bluebubbles
SPEC.loader.exec_module(penguin_connect_bluebubbles)


class _JsonResponse:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class BlueBubblesIntegrationTests(unittest.TestCase):
    def test_api_base_accepts_only_loopback_bluebubbles_origin(self):
        self.assertEqual(
            penguin_connect_bluebubbles.validate_api_base("http://localhost:1234"),
            "http://localhost:1234/api/v1",
        )
        self.assertEqual(
            penguin_connect_bluebubbles.validate_api_base("http://127.0.0.1:1234/api/v1"),
            "http://127.0.0.1:1234/api/v1",
        )
        for unsafe in (
            "https://messages.example.test",
            "http://127.0.0.1:1234/other",
            "http://user:secret@127.0.0.1:1234",
            "http://127.0.0.1:1234?guid=secret",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                penguin_connect_bluebubbles.validate_api_base(unsafe)

    def test_config_file_never_contains_bluebubbles_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "imessage-backend.json"
            penguin_connect_bluebubbles.save_config(
                penguin_connect_bluebubbles.BlueBubblesConfig(
                    api_base="http://127.0.0.1:1234/api/v1"
                ),
                config_path,
            )

            payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["backend"], "bluebubbles")
        self.assertEqual(payload["api_base"], "http://127.0.0.1:1234/api/v1")
        self.assertNotIn("password", payload)

    def test_native_setup_reads_password_from_stdin_without_using_clipboard(self):
        password = "synthetic-native-setup-password"
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    str(SCRIPT_PATH),
                    "--configure-stdin",
                    "http://127.0.0.1:1234",
                ],
            ),
            mock.patch.object(sys, "stdin", io.StringIO(password + "\n")),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(
                penguin_connect_bluebubbles.BlueBubblesClient,
                "ping",
            ) as ping,
            mock.patch.object(
                penguin_connect_bluebubbles,
                "store_keychain_password",
            ) as store_password,
            mock.patch.object(penguin_connect_bluebubbles, "save_config") as save_config,
            mock.patch.object(
                penguin_connect_bluebubbles,
                "_clipboard_text",
                side_effect=AssertionError("native setup must not read the clipboard"),
            ),
            mock.patch.object(
                penguin_connect_bluebubbles,
                "_clear_clipboard",
                side_effect=AssertionError("native setup must not touch the clipboard"),
            ),
        ):
            result = penguin_connect_bluebubbles.main()

        self.assertEqual(result, 0)
        ping.assert_called_once_with()
        store_password.assert_called_once_with(password)
        save_config.assert_called_once()

    def test_keychain_write_prompts_over_stdin_instead_of_exposing_password_in_argv(self):
        password = "synthetic-keychain-password"
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            penguin_connect_bluebubbles.subprocess,
            "run",
            return_value=completed,
        ) as run:
            penguin_connect_bluebubbles.store_keychain_password(password)

        command = run.call_args.args[0]
        self.assertEqual(command[-1], "-w")
        self.assertNotIn(password, command)
        self.assertEqual(run.call_args.kwargs["input"], password + "\n")

    def test_group_creation_uses_private_api_and_applies_name(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            if request.full_url.startswith("http://127.0.0.1:1234/api/v1/chat/new?"):
                return _JsonResponse(
                    {
                        "status": 200,
                        "data": {"guid": "iMessage;+;synthetic-group"},
                    }
                )
            return _JsonResponse(
                {
                    "status": 200,
                    "data": {"guid": "iMessage;+;synthetic-group", "displayName": "Synthetic group"},
                }
            )

        client = penguin_connect_bluebubbles.BlueBubblesClient(
            penguin_connect_bluebubbles.BlueBubblesConfig(
                api_base="http://127.0.0.1:1234/api/v1"
            ),
            "synthetic-bluebubbles-password",
            opener=opener,
        )

        result = client.create_group(
            ["15555550123", "synthetic@example.test"],
            first_message="Synthetic first message",
            name="Synthetic group",
        )

        self.assertEqual(result["group_id"], "iMessage;+;synthetic-group")
        self.assertTrue(result["name_applied"])
        self.assertEqual(len(requests), 2)
        create_request = requests[0][0]
        self.assertEqual(create_request.method, "POST")
        self.assertIn("guid=synthetic-bluebubbles-password", create_request.full_url)
        self.assertEqual(
            json.loads(create_request.data),
            {
                "addresses": ["15555550123", "synthetic@example.test"],
                "message": "Synthetic first message",
                "method": "private-api",
                "service": "iMessage",
            },
        )
        rename_request = requests[1][0]
        self.assertEqual(rename_request.method, "PUT")
        self.assertIn("iMessage%3B%2B%3Bsynthetic-group", rename_request.full_url)
        self.assertEqual(json.loads(rename_request.data), {"displayName": "Synthetic group"})

    def test_network_errors_do_not_expose_password(self):
        def opener(_request, _timeout):
            raise urllib.error.URLError("offline")

        client = penguin_connect_bluebubbles.BlueBubblesClient(
            penguin_connect_bluebubbles.BlueBubblesConfig(
                api_base="http://127.0.0.1:1234/api/v1"
            ),
            "synthetic-bluebubbles-password",
            opener=opener,
        )

        with self.assertRaises(penguin_connect_bluebubbles.BlueBubblesError) as raised:
            client.create_group(
                ["15555550123", "synthetic@example.test"],
                first_message="Synthetic first message",
            )

        self.assertNotIn("synthetic-bluebubbles-password", str(raised.exception))

    def test_load_client_requires_both_config_and_keychain_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "imessage-backend.json"
            penguin_connect_bluebubbles.save_config(
                penguin_connect_bluebubbles.BlueBubblesConfig(
                    api_base="http://127.0.0.1:1234/api/v1"
                ),
                config_path,
            )
            with mock.patch.object(
                penguin_connect_bluebubbles,
                "read_keychain_password",
                return_value="",
            ):
                self.assertIsNone(
                    penguin_connect_bluebubbles.load_client(config_path=config_path)
                )
            with mock.patch.object(
                penguin_connect_bluebubbles,
                "read_keychain_password",
                return_value="synthetic-bluebubbles-password",
            ):
                self.assertIsNotNone(
                    penguin_connect_bluebubbles.load_client(config_path=config_path)
                )


if __name__ == "__main__":
    unittest.main()
