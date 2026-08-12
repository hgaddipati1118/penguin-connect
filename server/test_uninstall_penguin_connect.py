"""Safety tests for the consumer Penguin uninstaller."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "uninstall_penguin_connect.py"
SPEC = importlib.util.spec_from_file_location("uninstall_penguin_connect", SCRIPT_PATH)
assert SPEC and SPEC.loader
uninstaller = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = uninstaller
SPEC.loader.exec_module(uninstaller)


class UninstallPenguinConnectTests(unittest.TestCase):
    def test_endpoint_tunnel_detection_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "endpoint.json"
            with patch.object(uninstaller, "ENDPOINT_STATE", state):
                self.assertFalse(uninstaller.endpoint_uses_tailscale())
                state.write_text("not-json", encoding="utf-8")
                self.assertFalse(uninstaller.endpoint_uses_tailscale())
                state.write_text(json.dumps({"tunnel": "tailscale"}), encoding="utf-8")
                self.assertTrue(uninstaller.endpoint_uses_tailscale())

    def test_delete_local_data_only_removes_explicit_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_support = root / "PenguinConnect"
            local_data = root / "penguinconnect-local-bridge-data"
            unrelated = root / "keep-me"
            for path in (app_support, local_data, unrelated):
                path.mkdir()
                (path / "marker").write_text("synthetic", encoding="utf-8")

            with (
                patch.object(uninstaller, "APP_SUPPORT", app_support),
                patch.object(uninstaller, "LOCAL_DATA", local_data),
            ):
                uninstaller.delete_local_data()

            self.assertFalse(app_support.exists())
            self.assertFalse(local_data.exists())
            self.assertTrue(unrelated.exists())

    def test_delete_data_requires_explicit_yes(self):
        with patch.object(sys, "argv", ["uninstall_penguin_connect.py", "--delete-data"]):
            with self.assertRaises(SystemExit) as raised:
                uninstaller.main()
        self.assertEqual(raised.exception.code, 2)

    def test_default_uninstall_revokes_optional_bluebubbles_backend(self):
        self.assertIn(
            ("penguin-connect-bluebubbles", "com.penguinconnect.bluebubbles.server-password"),
            uninstaller.KEYCHAIN_ITEMS,
        )
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "imessage-backend.json"
            config.write_text('{"backend":"bluebubbles"}', encoding="utf-8")
            with patch.object(uninstaller, "IMESSAGE_BACKEND_CONFIG", config):
                uninstaller.remove_imessage_backend_config()
            self.assertFalse(config.exists())


if __name__ == "__main__":
    unittest.main()
