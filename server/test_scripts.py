import argparse
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import penguin_connect_local_api
import penguin_connect_bridge_watchdog
import import_contacts
import penguin_connect_backfill
import penguin_connect_excluded_chats
import penguin_connect_setup
import penguin_connect_tool
import penguin_connect_verify_contact_resolution


class ScriptTests(unittest.TestCase):
    def test_packaged_python_entrypoints_disable_bytecode_writes(self):
        protected_entrypoints = (
            ROOT_DIR / "scripts" / "bootstrap_packaged_runtime.sh",
            ROOT_DIR / "scripts" / "run_penguin_connect_bridge.sh",
            ROOT_DIR / "scripts" / "run_penguin_connect_persistent_bridge.sh",
            ROOT_DIR / "scripts" / "install_launchd_penguin_connect_bridge.sh",
            ROOT_DIR / "scripts" / "run_penguin_connect_remote_mcp.sh",
            ROOT_DIR / "scripts" / "run_penguin_connect_mcp_cloudflare.sh",
            ROOT_DIR / "scripts" / "install_launchd_remote_mcp.sh",
            ROOT_DIR / "scripts" / "install_launchd_remote_tunnel.sh",
            ROOT_DIR / "scripts" / "install_launchd_whatsapp_bridge.sh",
        )
        for entrypoint in protected_entrypoints:
            with self.subTest(entrypoint=entrypoint.name):
                self.assertIn(
                    "PYTHONDONTWRITEBYTECODE",
                    entrypoint.read_text(encoding="utf-8"),
                )

        app_source = (ROOT_DIR / "desktop" / "PenguinApp.swift").read_text(encoding="utf-8")
        self.assertIn('environment["PYTHONDONTWRITEBYTECODE"] = "1"', app_source)

    def test_persistent_bridge_launch_agent_uses_penguin_without_terminal(self):
        installer = (
            ROOT_DIR / "scripts" / "install_launchd_penguin_connect_bridge.sh"
        ).read_text(encoding="utf-8")
        runner = (
            ROOT_DIR / "scripts" / "run_penguin_connect_persistent_bridge.sh"
        ).read_text(encoding="utf-8")
        app_source = (ROOT_DIR / "desktop" / "PenguinApp.swift").read_text(encoding="utf-8")

        self.assertIn('"KeepAlive": True', installer)
        self.assertIn('"AssociatedBundleIdentifiers"', installer)
        self.assertIn('"--bridge-agent"', installer)
        self.assertNotIn("/usr/bin/python3", installer)
        self.assertNotIn("osascript", installer)
        self.assertNotIn('application "Terminal"', installer)
        self.assertIn("127.0.0.1", runner)
        self.assertIn("--bridge-agent", app_source)

    def test_packaged_app_reopens_permissions_when_full_disk_access_is_missing(self):
        app_source = (ROOT_DIR / "desktop" / "PenguinApp.swift").read_text(encoding="utf-8")

        self.assertIn("isPackagedRuntime && !hasFullDiskAccess()", app_source)
        self.assertIn("showOnboarding(step: 1)", app_source)

    def test_packaged_app_requests_contacts_from_its_responsible_identity(self):
        app_source = (ROOT_DIR / "desktop" / "PenguinApp.swift").read_text(encoding="utf-8")

        permission_section = app_source.split("private func reportPermissions()", 1)[1].split(
            "private func whatsAppSourceURL()", 1
        )[0]
        self.assertIn("CNContactStore().requestAccess(for: .contacts)", permission_section)
        self.assertNotIn('arguments: ["--authorize"]', permission_section)
        self.assertNotIn('arguments: ["--status"]', permission_section)

        for entitlement_file in (
            ROOT_DIR / "desktop" / "Penguin.entitlements",
            ROOT_DIR / "desktop" / "PenguinContactsHelper.entitlements",
        ):
            with self.subTest(entitlement_file=entitlement_file.name):
                self.assertIn(
                    "com.apple.security.personal-information.addressbook",
                    entitlement_file.read_text(encoding="utf-8"),
                )

        build_script = (ROOT_DIR / "scripts" / "build_desktop_app.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("PenguinContactsHelper.entitlements", build_script)

    def test_remote_mcp_launch_agent_forwards_local_api_configuration(self):
        installer = (ROOT_DIR / "scripts" / "install_launchd_remote_mcp.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('"PENGUIN_CONNECT_PORT"', installer)
        self.assertIn('"PENGUIN_CONNECT_LOCAL_API_BASE"', installer)

    def test_resolve_local_api_base_prefers_explicit_base(self):
        base = penguin_connect_local_api.resolve_local_api_base(
            {
                "PENGUIN_CONNECT_LOCAL_API_BASE": "http://127.0.0.1:8899/",
                "PENGUIN_CONNECT_PORT": "9001",
            }
        )

        self.assertEqual(base, "http://127.0.0.1:8899")

    def test_resolve_local_api_base_falls_back_to_port(self):
        base = penguin_connect_local_api.resolve_local_api_base({"PENGUIN_CONNECT_PORT": "9001"})
        self.assertEqual(base, "http://127.0.0.1:9001")

    def test_resolve_local_api_base_handles_invalid_port(self):
        base = penguin_connect_local_api.resolve_local_api_base({"PENGUIN_CONNECT_PORT": "99999"})
        self.assertEqual(base, "http://127.0.0.1:9000")

    def test_bridge_watchdog_starts_only_when_listener_and_health_are_missing(self):
        status = penguin_connect_bridge_watchdog.inspect_bridge_status(
            {"PENGUIN_CONNECT_PORT": "9000"},
            listener_probe=lambda host, port, timeout: False,
            health_fetcher=lambda api_base, timeout: (False, None, None),
        )

        self.assertTrue(status.should_start)
        self.assertEqual(status.detail, "bridge_missing")

    def test_bridge_watchdog_does_not_start_when_listener_is_alive(self):
        status = penguin_connect_bridge_watchdog.inspect_bridge_status(
            {"PENGUIN_CONNECT_PORT": "9000"},
            listener_probe=lambda host, port, timeout: True,
            health_fetcher=lambda api_base, timeout: (False, None, None),
        )

        self.assertFalse(status.should_start)
        self.assertEqual(status.detail, "listener_present")

    def test_bridge_watchdog_does_not_start_when_health_is_reachable_without_listener_probe(self):
        status = penguin_connect_bridge_watchdog.inspect_bridge_status(
            {"PENGUIN_CONNECT_PORT": "9000"},
            listener_probe=lambda host, port, timeout: False,
            health_fetcher=lambda api_base, timeout: (True, 200, False),
        )

        self.assertFalse(status.should_start)
        self.assertEqual(status.detail, "health_reachable_without_listener_probe")

    def test_bridge_watchdog_launches_terminal_starter_only_when_missing(self):
        launcher = mock.Mock()

        status = penguin_connect_bridge_watchdog.run_watchdog_once(
            {"PENGUIN_CONNECT_PORT": "9000"},
            launcher=launcher,
            listener_probe=lambda host, port, timeout: False,
            health_fetcher=lambda api_base, timeout: (False, None, None),
        )

        self.assertTrue(status.should_start)
        launcher.assert_called_once_with(
            penguin_connect_bridge_watchdog.REPO_ROOT,
            allow_missing_gmail_startup=False,
        )

    def test_import_contacts_counts_only_inserted_rows(self):
        contacts = [
            {
                "first_name": "Ava",
                "last_name": "Stone",
                "organization": "",
                "phone": "+1 (415) 555-0101",
                "phone_normalized": "+14155550101",
                "email": None,
                "source_db": "addressbook-1",
            },
            {
                "first_name": "Ava",
                "last_name": "Stone",
                "organization": "",
                "phone": "+1 415-555-0101",
                "phone_normalized": "+14155550101",
                "email": None,
                "source_db": "addressbook-1",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cache.db"
            db_path.touch()
            original_cache_db = import_contacts.CACHE_DB
            import_contacts.CACHE_DB = db_path
            try:
                inserted = import_contacts.import_to_cache(contacts)
                conn = sqlite3.connect(str(db_path))
                try:
                    row_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
                finally:
                    conn.close()
            finally:
                import_contacts.CACHE_DB = original_cache_db

        self.assertEqual(inserted, 1)
        self.assertEqual(row_count, 1)

    def test_verify_contact_resolution_normalizes_us_phone(self):
        normalized = penguin_connect_verify_contact_resolution.normalize_phone_for_lookup("+1 (404) 729-4874")
        self.assertEqual(normalized, "4047294874")

    def test_verify_contact_resolution_picks_contact_name_for_phone(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE contacts (
                   first_name TEXT,
                   last_name TEXT,
                   organization TEXT,
                   phone TEXT,
                   phone_normalized TEXT,
                   email TEXT,
                   imported_at TEXT
               )"""
        )
        conn.execute(
            """INSERT INTO contacts(first_name, last_name, organization, phone, phone_normalized, email, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("Julian", "Joseph", "", "+15127436385", "+15127436385", None),
        )

        resolved = penguin_connect_verify_contact_resolution.resolve_contact_name(conn, "+1 (512) 743-6385")
        conn.close()
        self.assertEqual(resolved, "Julian Joseph")

    def test_backfill_script_wait_seconds_clamped(self):
        wait = penguin_connect_backfill._rate_limit_wait_seconds({"retry_after_seconds": "2400"}, 900)
        self.assertEqual(wait, 900)

    def test_backfill_script_retries_after_rate_limit(self):
        with mock.patch(
            "penguin_connect_backfill._call_sync",
            side_effect=[
                {
                    "success": True,
                    "skipped": True,
                    "reason": "gmail_rate_limited",
                    "retry_after_seconds": 3,
                },
                {"success": True, "mode": "backfill"},
            ],
        ) as mock_call, mock.patch("penguin_connect_backfill.time.sleep") as mock_sleep:
            code, result = penguin_connect_backfill.run_backfill(
                api_base="http://127.0.0.1:9000",
                payload={"mode": "backfill", "verify_all": True, "days": 30},
                max_attempts=3,
                wait_on_rate_limit=True,
                max_wait_seconds=120,
                timeout_seconds=30,
            )

        self.assertEqual(code, 0)
        self.assertTrue(result["success"])
        self.assertEqual(mock_call.call_count, 2)
        mock_sleep.assert_called_once_with(3)

    def test_setup_persists_signature_markers_to_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("PENGUIN_CONNECT_PORT=9000\n", encoding="utf-8")
            env_values = penguin_connect_setup._read_env_file(env_path)

            penguin_connect_setup._configure_signature_markers(
                Path(tmp),
                env_path,
                cli_markers=["External email:", "Company Confidential"],
                assume_yes=True,
                env_file=env_values,
            )

            prefs_path = Path(tmp) / penguin_connect_setup.DEFAULT_SIGNATURE_MARKERS_FILE
            contents = prefs_path.read_text(encoding="utf-8")

        self.assertIn('"signature_markers"', contents)
        self.assertIn('"External email:"', contents)
        self.assertIn('"Company Confidential"', contents)

    def test_setup_runs_excluded_chat_manager_with_gmail(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            venv_python = repo_root / "server" / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")

            with mock.patch("penguin_connect_setup._confirm", return_value=True), mock.patch(
                "penguin_connect_setup._run"
            ) as mock_run:
                penguin_connect_setup._configure_excluded_chats(
                    repo_root,
                    venv_python,
                    "owner@gmail.com",
                    assume_yes=False,
                    env_file={},
                )

        mock_run.assert_called_once_with(
            [
                str(venv_python),
                "scripts/penguin_connect_excluded_chats.py",
                "--gmail",
                "owner@gmail.com",
            ],
            cwd=repo_root,
        )

    def test_setup_runs_excluded_chat_manager_with_configured_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            venv_python = repo_root / "server" / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")
            excluded_path = repo_root / "config" / "excluded.json"

            with mock.patch("penguin_connect_setup._confirm", return_value=True), mock.patch(
                "penguin_connect_setup._run"
            ) as mock_run:
                penguin_connect_setup._configure_excluded_chats(
                    repo_root,
                    venv_python,
                    "owner@gmail.com",
                    assume_yes=False,
                    env_file={"PENGUIN_CONNECT_EXCLUDED_CHATS_FILE": str(excluded_path)},
                )

        mock_run.assert_called_once_with(
            [
                str(venv_python),
                "scripts/penguin_connect_excluded_chats.py",
                "--gmail",
                "owner@gmail.com",
                "--file",
                str(excluded_path),
            ],
            cwd=repo_root,
        )

    def test_setup_yes_mode_skips_excluded_chat_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            venv_python = repo_root / "server" / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")

            with mock.patch("penguin_connect_setup._run") as mock_run:
                penguin_connect_setup._configure_excluded_chats(
                    repo_root,
                    venv_python,
                    "owner@gmail.com",
                    assume_yes=True,
                    env_file={},
                )

        mock_run.assert_not_called()

    def test_excluded_chats_script_parses_toggle_ranges(self):
        selected = penguin_connect_excluded_chats._parse_selection("1,3-5", 6)
        self.assertEqual(selected, {1, 3, 4, 5})

    def test_excluded_chats_script_toggle_preserves_existing_hidden_entries(self):
        rows = [
            {
                "index": 1,
                "entry": {
                    "conversation_id": "amc_visible",
                    "thread_key": "chat:visible",
                    "display_name": "Visible",
                },
                "existing_entry": None,
            }
        ]
        existing_entries = [
            {
                "conversation_id": "amc_hidden",
                "thread_key": "chat:hidden",
                "display_name": "Hidden",
            }
        ]

        updated = penguin_connect_excluded_chats._toggle_entries(
            rows,
            existing_entries,
            selected_indexes={1},
            reason="quiet thread",
        )

        by_id = {entry["conversation_id"]: entry for entry in updated}
        self.assertIn("amc_hidden", by_id)
        self.assertIn("amc_visible", by_id)
        self.assertEqual(by_id["amc_visible"]["reason"], "quiet thread")

    def test_tool_search_matches_conversation_participants(self):
        conversation = {
            "conversation_id": "amc_123",
            "display_name": "Weekend Plans",
            "participants": ["+14155550101", "ava@example.com"],
        }

        self.assertTrue(penguin_connect_tool._conversation_matches(conversation, "ava@example.com"))
        self.assertFalse(penguin_connect_tool._conversation_matches(conversation, "not-here"))

    def test_tool_contact_create_script_escapes_fields(self):
        script = penguin_connect_tool._build_contact_create_script(
            first_name='Ava "AJ"',
            last_name="Stone",
            organization="Example Co",
            phones=["+14155550101"],
            emails=["ava@example.com"],
        )

        self.assertIn('first name:"Ava \\"AJ\\""', script)
        self.assertIn('make new phone at end of phones of newPerson', script)
        self.assertIn('value:"+14155550101"', script)
        self.assertIn('make new email at end of emails of newPerson', script)
        self.assertIn('value:"ava@example.com"', script)

    def test_tool_contact_applescript_uses_stdin_to_keep_contact_data_out_of_argv(self):
        completed = mock.Mock(returncode=0, stdout="synthetic-person\n", stderr="")
        script = 'tell application "Contacts" to return "synthetic-person"'
        with mock.patch.object(
            penguin_connect_tool.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = penguin_connect_tool._run_osascript(script)

        self.assertEqual(result, "synthetic-person")
        self.assertEqual(run.call_args.args[0], ["osascript", "-"])
        self.assertEqual(run.call_args.kwargs["input"], script)

    def test_tool_group_draft_lists_participants_and_message(self):
        draft = penguin_connect_tool._build_group_draft(
            ["+14155550101", "ava@example.com"],
            "Dinner at 7?",
        )

        self.assertEqual(draft, "To: +14155550101, ava@example.com\n\nDinner at 7?\n")

    def test_tool_group_draft_builds_addressed_messages_url(self):
        url = penguin_connect_tool._messages_address_url(["+14155550101", "ava@example.com"])

        self.assertEqual(url, "sms://open?addresses=%2B14155550101%2C%20ava%40example.com")

    def test_tool_resolve_attachment_paths_requires_existing_file(self):
        with tempfile.NamedTemporaryFile(suffix=".m4a") as audio_file:
            resolved = penguin_connect_tool._resolve_attachment_paths([audio_file.name])

        self.assertEqual(len(resolved), 1)
        self.assertTrue(resolved[0].endswith(".m4a"))
        with self.assertRaises(penguin_connect_tool.ToolError):
            penguin_connect_tool._resolve_attachment_paths(["/tmp/missing-voice-memo.m4a"])

    def test_tool_schedule_posts_scheduled_message_payload(self):
        calls = []

        def fake_api(method, path, *, api_base, payload=None, timeout=20.0):
            calls.append(
                {
                    "method": method,
                    "path": path,
                    "api_base": api_base,
                    "payload": payload,
                    "timeout": timeout,
                }
            )
            return {
                "success": True,
                "scheduled_message": {
                    "scheduled_id": "scheduled_123",
                    "conversation_id": "amc_test",
                    "scheduled_at": "2026-07-01T16:30:00-07:00",
                    "status": "scheduled",
                },
            }

        args = argparse.Namespace(
            conversation_id="amc_test",
            sender_email="ops@example.test",
            message="Later",
            message_file=None,
            attachment_paths=[],
            scheduled_at="2026-07-01T16:30:00-07:00",
            api_base="http://127.0.0.1:9000",
            timeout=3.0,
            json=False,
        )

        with mock.patch.object(penguin_connect_tool, "_api_json", side_effect=fake_api), mock.patch("builtins.print"):
            result = penguin_connect_tool.command_schedule(args)

        self.assertEqual(result, 0)
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["path"], "/penguin-connect/conversations/amc_test/scheduled-messages")
        self.assertEqual(
            calls[0]["payload"],
            {
                "sender_email": "ops@example.test",
                "message": "Later",
                "attachment_paths": None,
                "scheduled_at": "2026-07-01T16:30:00-07:00",
            },
        )

    def test_tool_scheduled_commands_call_expected_api_paths(self):
        calls = []

        def fake_api(method, path, *, api_base, payload=None, timeout=20.0):
            calls.append((method, path, payload))
            if path.endswith("/scheduled-messages"):
                return {"success": True, "scheduled_messages": []}
            if path.endswith("/cancel"):
                return {
                    "success": True,
                    "scheduled_message": {"scheduled_id": "scheduled_123", "status": "cancelled"},
                }
            return {"success": True, "processed": 0, "results": []}

        with mock.patch.object(penguin_connect_tool, "_api_json", side_effect=fake_api), mock.patch("builtins.print"):
            penguin_connect_tool.command_scheduled_list(
                argparse.Namespace(conversation_id="amc_test", api_base="http://127.0.0.1:9000", timeout=3.0, json=False)
            )
            penguin_connect_tool.command_scheduled_cancel(
                argparse.Namespace(scheduled_id="scheduled_123", api_base="http://127.0.0.1:9000", timeout=3.0, json=False)
            )
            penguin_connect_tool.command_scheduled_run_due(
                argparse.Namespace(limit=5, api_base="http://127.0.0.1:9000", timeout=3.0, json=False)
            )

        self.assertEqual(calls[0], ("GET", "/penguin-connect/conversations/amc_test/scheduled-messages", None))
        self.assertEqual(calls[1], ("POST", "/penguin-connect/scheduled-messages/scheduled_123/cancel", {}))
        self.assertEqual(calls[2], ("POST", "/penguin-connect/scheduled-messages/run-due?limit=5", {}))

    def test_tool_formats_scheduled_message(self):
        formatted = penguin_connect_tool._format_scheduled_message(
            {
                "scheduled_id": "scheduled_123",
                "status": "scheduled",
                "scheduled_at": "2026-07-01T16:30:00-07:00",
                "source_provider": "whatsapp",
                "message": "Later",
                "attachment_count": 1,
            }
        )

        self.assertIn("scheduled_123", formatted)
        self.assertIn("whatsapp", formatted)
        self.assertIn("Later", formatted)
        self.assertIn("attachments: 1", formatted)

    def test_tool_formats_audio_attachment_summary(self):
        summary = penguin_connect_tool._format_message_attachment_summary(
            {
                "metadata": {
                    "attachments": [
                        {
                            "filename": "/tmp/Audio Message.caf",
                            "mime_type": "audio/x-caf",
                            "transfer_name": "Audio Message.caf",
                        }
                    ]
                }
            }
        )

        self.assertIn("audio:Audio Message.caf", summary)

    def test_tool_search_bridge_messages_matches_body_and_attachment_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cache.db"
            conn = sqlite3.connect(str(db_path))
            conn.executescript(
                """
                CREATE TABLE penguin_connect_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    source_provider TEXT,
                    source_service_name TEXT,
                    source_chat_identifier TEXT,
                    participants TEXT
                );
                CREATE TABLE penguin_connect_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT,
                    provider TEXT,
                    provider_message_id TEXT,
                    direction TEXT,
                    sender_email TEXT,
                    sender_name TEXT,
                    subject TEXT,
                    body_text TEXT,
                    message_timestamp TEXT,
                    metadata TEXT,
                    gmail_message_id TEXT,
                    gmail_thread_id TEXT
                );
                """
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (conversation_id, display_name, source_provider, source_service_name, source_chat_identifier, participants)
                   VALUES ('amc_test', 'Weekend Plans', 'imessage', 'iMessage', 'chat-123', '["ava@example.com"]')"""
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_name, subject,
                    body_text, message_timestamp, metadata)
                   VALUES (?, 'imessage', 'imsg-1', 'imessage_to_gmail', ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "Ava",
                    "Weekend Plans",
                    "Voice memo from dinner",
                    "2026-03-10T10:00:00+00:00",
                    '{"attachments":[{"transfer_name":"voice-note.m4a","mime_type":"audio/mp4"}]}',
                ),
            )
            conn.commit()
            conn.close()

            original_cache_db = penguin_connect_tool.CACHE_DB
            penguin_connect_tool.CACHE_DB = db_path
            try:
                body_rows = penguin_connect_tool._search_bridge_messages("dinner", limit=5)
                attachment_rows = penguin_connect_tool._search_bridge_messages("voice-note", limit=5)
            finally:
                penguin_connect_tool.CACHE_DB = original_cache_db

        self.assertEqual(len(body_rows), 1)
        self.assertEqual(body_rows[0]["conversation_id"], "amc_test")
        self.assertEqual(len(attachment_rows), 1)
        self.assertEqual(attachment_rows[0]["attachments"][0]["transfer_name"], "voice-note.m4a")

    def test_tool_formats_message_search_row(self):
        row = {
            "conversation_id": "amc_test",
            "display_name": "Weekend Plans",
            "sender_name": "Ava",
            "body_text": "Voice memo from dinner",
            "message_timestamp": "2026-03-10T10:00:00+00:00",
        }

        formatted = penguin_connect_tool._format_search_message_row(row)

        self.assertIn("amc_test", formatted)
        self.assertIn("Weekend Plans", formatted)
        self.assertIn("Voice memo", formatted)


if __name__ == "__main__":
    unittest.main()
