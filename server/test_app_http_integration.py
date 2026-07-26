import json
import base64
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
import penguin_connect
from db import SCHEMA

class AppHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        penguin_connect._local_conversation_discovery_last_run = 0.0
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "cache.db"

        conn = self._get_connection()
        try:
            conn.executescript(SCHEMA)
            conn.execute(
                """INSERT INTO penguin_connect_accounts
                   (gmail_email, keychain_service, send_as_aliases, status)
                   VALUES (?, ?, ?, 'connected')""",
                (
                    "owner@gmail.com",
                    "penguinconnect-local-bridge.gmail.owner@gmail.com",
                    '["owner@gmail.com", "ops@company.com"]',
                ),
            )
            conn.executemany(
                """INSERT INTO contacts
                   (first_name, last_name, organization, phone, phone_normalized, email, source_db)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        "Taylor",
                        "Example",
                        "",
                        "+1 (512) 743-6385",
                        "15127436385",
                        "",
                        "synthetic",
                    ),
                    (
                        "",
                        "",
                        "Example Ops",
                        "",
                        "",
                        "ops@example.test",
                        "synthetic",
                    ),
                ],
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, chat_type, participants,
                    alias_email, status)
                   VALUES (?, ?, ?, ?, 'dm', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_test",
                    "chat-123",
                    "Taylor",
                    '["+15127436385"]',
                    "owner+am-test@gmail.com",
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_aliases
                   (conversation_id, alias_email, alias_local_part, status)
                   VALUES (?, ?, ?, 'active')""",
                (
                    "amc_test",
                    "owner+am-test@gmail.com",
                    "owner+am-test",
                ),
            )
            conn.executemany(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        "amc_test",
                        "imessage",
                        "imsg-older",
                        "imessage_to_gmail",
                        None,
                        "[Apple Messages] Taylor",
                        "Older message",
                        "2026-03-09T10:00:00+00:00",
                        1,
                        "{}",
                    ),
                    (
                        "amc_test",
                        "imessage",
                        "imsg-latest",
                        "imessage_to_gmail",
                        None,
                        "[Apple Messages] Taylor",
                        "Latest message",
                        "2026-03-10T10:00:00+00:00",
                        1,
                        "{}",
                    ),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        self.patchers = [
            mock.patch("app.get_connection", side_effect=self._get_connection),
            mock.patch("app.init_db"),
            mock.patch("app.assert_startup_ready", return_value={"ok": True}),
            mock.patch("app.start_watchers"),
            mock.patch("app.stop_watchers"),
            mock.patch(
                "app.penguinconnect_run_startup_catchup",
                return_value={"success": True, "skipped": True, "reason": "queue_idle"},
            ),
            mock.patch("app.log_action"),
            mock.patch("builtins.print"),
            mock.patch("app.action_log_path", return_value=Path(self.tmpdir.name) / "actions.jsonl"),
            mock.patch.object(app_module, "DB_PATH", self.db_path),
            mock.patch("penguin_connect.log_action"),
            mock.patch(
                "penguin_connect.send_imessage",
                side_effect=AssertionError("Tests must mock send_imessage explicitly"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmpdir.cleanup()

    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def test_status_endpoint_reports_counts_and_runtime_sync_status(self):
        with mock.patch(
            "app.penguinconnect_get_gmail_connection_status",
            return_value={"connected": True, "gmail_email": "owner@gmail.com"},
        ), mock.patch(
            "app.penguinconnect_get_cached_sync_metrics",
            return_value={"totals": {"retry_queue_count": 1}},
        ), mock.patch(
            "app.penguinconnect_get_runtime_sync_status",
            return_value={"running": True, "mode": "incremental"},
        ), mock.patch(
            "app.get_sync_status",
            return_value={"penguin_connect": {"polling": True, "last_sync": "2026-03-10T10:30:00+00:00"}},
        ), TestClient(app_module.app) as client:
            response = client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_conversations"], 1)
        self.assertEqual(body["active_conversations"], 1)
        self.assertTrue(body["sync_status"]["penguin_connect"]["polling"])
        self.assertTrue(body["sync_status"]["penguin_connect"]["running"])
        self.assertEqual(body["sync_metrics"]["totals"]["retry_queue_count"], 1)

    def test_app_startup_refreshes_contacts_once(self):
        with mock.patch("app.refresh_contacts_now", return_value={"success": True}) as mock_refresh, TestClient(
            app_module.app
        ):
            pass

        mock_refresh.assert_called_once_with()

    def test_app_startup_retries_transient_startup_catchup_skips(self):
        results = [
            {"success": True, "skipped": True, "reason": "gmail_rate_limited", "retry_after_seconds": 3},
            {"success": True, "skipped": True, "reason": "queue_busy"},
            {"success": True, "skipped": False, "pending_bootstrap_conversations": 0, "pending_full_verify_conversations": 0},
        ]
        finished = threading.Event()

        def fake_run_startup_catchup():
            result = results.pop(0)
            if not results:
                finished.set()
            return result

        with mock.patch("app.penguinconnect_run_startup_catchup", side_effect=fake_run_startup_catchup) as mock_run, mock.patch(
            "app._startup_catchup_batch_pause_seconds",
            return_value=7.0,
        ), mock.patch("app.time.sleep") as mock_sleep, TestClient(app_module.app):
            self.assertTrue(finished.wait(1))

        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [mock.call(3.0), mock.call(7.0)])

    def test_messages_endpoint_respects_limit_and_returns_latest_first(self):
        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})
            older_response = client.get(
                "/penguin-connect/conversations/amc_test/messages",
                params={"limit": 1, "offset": 1, "refresh": "false"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["found"])
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-latest")
        self.assertEqual(body["messages"][0]["body_text"], "Latest message")
        self.assertGreaterEqual(body["total"], 2)
        self.assertTrue(body["has_more"])
        self.assertEqual(older_response.status_code, 200)
        older = older_response.json()
        self.assertEqual(older["offset"], 1)
        self.assertNotEqual(older["messages"][0]["provider_message_id"], "imsg-latest")

    def test_attachment_library_returns_paginated_files_with_intelligence(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """UPDATE penguin_connect_messages
                   SET metadata = ?
                   WHERE conversation_id = 'amc_test'
                     AND provider_message_id = 'imsg-latest'""",
                (
                    json.dumps({
                        "attachments": [
                            {
                                "filename": "/tmp/synthetic-deck.pdf",
                                "transfer_name": "synthetic-deck.pdf",
                                "mime_type": "application/pdf",
                            },
                            {
                                "filename": "/tmp/synthetic-image.jpg",
                                "transfer_name": "synthetic-image.jpg",
                                "mime_type": "image/jpeg",
                            },
                        ],
                    }),
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_attachment_intelligence
                   (conversation_id, provider_message_id, attachment_index, filename,
                    mime_type, summary, status)
                   VALUES ('amc_test', 'imsg-latest', 0, 'synthetic-deck.pdf',
                           'application/pdf', 'Synthetic launch plan and timeline.', 'summarized')"""
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            first = client.get(
                "/penguin-connect/attachment-library",
                params={"limit": 1, "offset": 0},
            )
            second = client.get(
                "/penguin-connect/attachment-library",
                params={"limit": 1, "offset": 1},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["total"], 2)
        self.assertEqual(first.json()["count"], 1)
        self.assertTrue(first.json()["has_more"])
        self.assertEqual(first.json()["items"][0]["attachment_index"], 0)
        self.assertEqual(
            first.json()["items"][0]["intelligence_summary"],
            "Synthetic launch plan and timeline.",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["items"][0]["attachment_index"], 1)
        self.assertFalse(second.json()["has_more"])
        self.assertEqual(first.json()["intelligence"]["complete"], 1)

    def test_attachment_intelligence_worker_drains_batches_before_refresh(self):
        batches = [
            {"attempted": 4, "processed": 4, "remaining": 3},
            {"attempted": 3, "processed": 3, "remaining": 0},
        ]
        with mock.patch(
            "app._run_attachment_intelligence_batch",
            side_effect=batches,
        ) as run_batch, mock.patch(
            "app.refresh_message_search_index",
        ) as refresh_index, mock.patch(
            "app.time.sleep",
        ) as sleep:
            app_module._run_attachment_intelligence_worker()

        self.assertEqual(run_batch.call_count, 2)
        sleep.assert_called_once()
        refresh_index.assert_called_once_with()

    def test_attachment_intelligence_queue_reaches_older_missing_rows(self):
        conn = self._get_connection()
        attachment_metadata = json.dumps({
            "attachments": [{
                "filename": "/tmp/synthetic.txt",
                "transfer_name": "synthetic.txt",
                "mime_type": "text/plain",
            }],
        })
        try:
            conn.executemany(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, body_text,
                    message_timestamp, is_read, metadata)
                   VALUES ('amc_test', 'imessage', ?, 'imessage_local', '', ?, 1, ?)""",
                [
                    ("queue-newer-covered", "2026-03-12T10:00:00+00:00", attachment_metadata),
                    ("queue-older-missing", "2026-03-11T10:00:00+00:00", attachment_metadata),
                ],
            )
            conn.execute(
                """INSERT INTO penguin_connect_attachment_intelligence
                   (conversation_id, provider_message_id, attachment_index, filename,
                    mime_type, status, updated_at)
                   VALUES ('amc_test', 'queue-newer-covered', 0, 'synthetic.txt',
                           'text/plain', 'metadata_only', datetime('now'))"""
            )
            conn.commit()

            queued = app_module._queue_attachment_intelligence(conn, limit=1)
            queued_row = conn.execute(
                """SELECT provider_message_id, status
                   FROM penguin_connect_attachment_intelligence
                   WHERE provider_message_id = 'queue-older-missing'"""
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(queued, 1)
        self.assertIsNotNone(queued_row)
        self.assertEqual(queued_row["provider_message_id"], "queue-older-missing")
        self.assertEqual(queued_row["status"], "queued")

    def test_missing_attachment_becomes_searchable_metadata_without_retrying(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """UPDATE penguin_connect_messages
                   SET body_text = ?, sender_name = ?, metadata = ?
                   WHERE conversation_id = 'amc_test'
                     AND provider_message_id = 'imsg-latest'""",
                (
                    "The launch plan is attached.",
                    "Taylor Example",
                    json.dumps({
                        "attachments": [{
                            "filename": "/missing/launch-plan.pdf",
                            "transfer_name": "launch-plan.pdf",
                            "mime_type": "application/pdf",
                        }],
                    }),
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_attachment_intelligence
                   (conversation_id, provider_message_id, attachment_index, file_path,
                    filename, mime_type, status)
                   VALUES ('amc_test', 'imsg-latest', 0, '/missing/launch-plan.pdf',
                           'launch-plan.pdf', 'application/pdf', 'queued')"""
            )
            conn.commit()
        finally:
            conn.close()

        result = app_module._run_attachment_intelligence_batch()

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT status, summary, extracted_text, last_error
                   FROM penguin_connect_attachment_intelligence
                   WHERE conversation_id = 'amc_test'
                     AND provider_message_id = 'imsg-latest'
                     AND attachment_index = 0"""
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(row["status"], "metadata_only")
        self.assertIn("launch-plan.pdf", row["summary"])
        self.assertIn("Taylor Example", row["summary"])
        self.assertIn("The launch plan is attached.", row["extracted_text"])
        self.assertEqual(row["last_error"], "")

    def test_queue_reconciles_legacy_missing_file_failures(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_attachment_intelligence
                   (conversation_id, provider_message_id, attachment_index, file_path,
                    filename, mime_type, status, last_error)
                   VALUES ('amc_test', 'imsg-latest', 0, '/missing/photo.jpg',
                           'photo.jpg', 'image/jpeg', 'failed',
                           'attachment_file_not_found')"""
            )
            conn.commit()
            app_module._queue_attachment_intelligence(conn, limit=1)
            row = conn.execute(
                """SELECT status, summary, last_error
                   FROM penguin_connect_attachment_intelligence
                   WHERE conversation_id = 'amc_test'
                     AND provider_message_id = 'imsg-latest'
                     AND attachment_index = 0"""
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["status"], "metadata_only")
        self.assertIn("photo.jpg", row["summary"])
        self.assertEqual(row["last_error"], "")

    def test_workspace_revision_is_small_private_and_changes_with_cached_messages(self):
        source_stamp = ((101, 202), (303, 404))
        with mock.patch("app._workspace_source_file_token", return_value=source_stamp), TestClient(
            app_module.app
        ) as client:
            first = client.get("/penguin-connect/workspace-revision")
            conn = self._get_connection()
            try:
                conn.execute(
                    """INSERT INTO penguin_connect_messages
                       (conversation_id, provider, provider_message_id, direction, body_text,
                        message_timestamp, is_read, metadata)
                       VALUES (?, 'imessage', ?, 'imessage_local', ?, ?, 1, '{}')""",
                    (
                        "amc_test",
                        "synthetic-revision-message",
                        "Synthetic revision probe",
                        "2026-03-10T10:01:00+00:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            second = client.get("/penguin-connect/workspace-revision")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(first.json()["revision"]), 64)
        self.assertEqual(len(first.json()["local_revision"]), 64)
        self.assertEqual(len(first.json()["imessage_revision"]), 64)
        self.assertEqual(len(first.json()["whatsapp_revision"]), 64)
        self.assertEqual(len(first.json()["slack_revision"]), 64)
        self.assertNotEqual(first.json()["revision"], second.json()["revision"])
        self.assertEqual(first.json()["poll_after_ms"], 5000)
        self.assertNotIn(str(self.db_path), first.text)
        self.assertLess(len(first.content), 500)

    def test_conversations_compact_mode_keeps_inbox_fields_and_drops_bridge_fields(self):
        with TestClient(app_module.app) as client:
            full_response = client.get("/penguin-connect/conversations")
            compact_response = client.get(
                "/penguin-connect/conversations",
                params={"compact": True},
            )

        self.assertEqual(full_response.status_code, 200)
        self.assertEqual(compact_response.status_code, 200)
        full_row = full_response.json()["conversations"][0]
        compact_row = compact_response.json()["conversations"][0]
        self.assertEqual(compact_row["conversation_id"], full_row["conversation_id"])
        self.assertEqual(compact_row["last_message_preview"], full_row["last_message_preview"])
        self.assertIn("participants", compact_row)
        self.assertIn("contact_context", compact_row)
        self.assertIn("is_archived", compact_row)
        self.assertNotIn("alias_email", compact_row)
        self.assertNotIn("gmail_thread_id", compact_row)
        self.assertNotIn("source_chat_id", compact_row)
        for contact in compact_row["contact_context"]:
            self.assertLessEqual(
                set(contact),
                set(app_module._COMPACT_CONTACT_CONTEXT_FIELDS),
            )
        self.assertLess(len(compact_response.content), len(full_response.content))

    def test_conversations_fast_mode_skips_blocking_discovery_and_preview_hydration(self):
        cached = {
            "connected": False,
            "gmail_email": "",
            "conversations": [],
        }
        with mock.patch.object(
            app_module,
            "penguinconnect_list_conversations",
            return_value=cached,
        ) as list_conversations, TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations",
                params={"compact": True, "fast": True},
            )

        self.assertEqual(response.status_code, 200)
        list_conversations.assert_called_once_with(
            mock.ANY,
            discover_sources=False,
            hydrate_previews=False,
        )

    def test_messages_endpoint_forwards_incremental_refresh_mode(self):
        with mock.patch(
            "app.penguinconnect_get_conversation_messages",
            return_value={"found": True, "messages": [], "total": 0, "has_more": False},
        ) as get_messages, TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations/amc_test/messages",
                params={"limit": 25, "refresh": True, "incremental": True},
            )

        self.assertEqual(response.status_code, 200)
        get_messages.assert_called_once_with(
            mock.ANY,
            "amc_test",
            limit=25,
            offset=0,
            refresh_source=True,
            incremental_refresh=True,
        )

    def test_cache_backfill_endpoint_repairs_selected_conversation_history(self):
        with mock.patch(
            "app.penguinconnect_backfill_local_conversation_cache",
            return_value={"found": True, "imported": 3381, "completed": True},
        ) as backfill, TestClient(app_module.app) as client:
            response = client.post("/penguin-connect/conversations/amc_test/cache-backfill", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 3381)
        backfill.assert_called_once()
        self.assertEqual(backfill.call_args.args[1], "amc_test")

    def test_codex_workspace_modes_require_explicit_write_confirmation(self):
        self.assertEqual(app_module._codex_stream_mode("read", False), ("read", "read-only"))
        self.assertEqual(app_module._codex_stream_mode("ask", True), ("ask", "danger-full-access"))
        self.assertEqual(app_module._codex_stream_mode("yolo", True), ("yolo", "danger-full-access"))
        with self.assertRaises(Exception) as ask_ctx:
            app_module._codex_stream_mode("ask", False)
        with self.assertRaises(Exception) as yolo_ctx:
            app_module._codex_stream_mode("yolo", False)
        self.assertEqual(ask_ctx.exception.status_code, 403)
        self.assertEqual(yolo_ctx.exception.status_code, 403)

    def test_translation_uses_codex_for_non_english_text(self):
        with mock.patch("app._detect_message_language", return_value=("el", 0.99)), mock.patch(
            "app._run_codex_prompt",
            return_value={"success": True, "answer": "Hello, I am running late 👋"},
        ) as mock_codex, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/translate",
                json={"text": "Γεια σου, αργώ 👋"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["translated"])
        self.assertEqual(body["language"], "el")
        self.assertEqual(body["text"], "Hello, I am running late 👋")
        self.assertIn("Preserve names, URLs, emojis", mock_codex.call_args.args[0])

    def test_translation_skips_codex_for_confident_english(self):
        with mock.patch("app._detect_message_language", return_value=("en", 0.99)), mock.patch(
            "app._run_codex_prompt"
        ) as mock_codex, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/translate",
                json={"text": "Meet me at the office at noon."},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["translated"])
        self.assertEqual(response.json()["text"], "Meet me at the office at noon.")
        mock_codex.assert_not_called()

    def test_conversations_endpoint_includes_unread_count(self):
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE penguin_connect_messages SET is_read = 0 WHERE provider_message_id = ?",
                ("imsg-latest",),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations")

        self.assertEqual(response.status_code, 200)
        conversation = response.json()["conversations"][0]
        self.assertEqual(conversation["conversation_id"], "amc_test")
        self.assertEqual(conversation["unread_count"], 1)
        self.assertTrue(conversation["has_unread"])
        self.assertFalse(conversation["is_pinned"])
        self.assertFalse(conversation["is_archived"])
        self.assertEqual(conversation["title"], "")
        self.assertEqual(conversation["note"], "")
        self.assertEqual(conversation["labels"], [])
        self.assertEqual(conversation["draft_text"], "")
        self.assertEqual(conversation["follow_up_at"], "")
        self.assertEqual(conversation["last_message_preview"], "Latest message")
        self.assertEqual(conversation["last_message_sender"], "")
        self.assertEqual(conversation["last_message_direction"], "imessage_to_gmail")
        self.assertEqual(conversation["last_message_provider_id"], "imsg-latest")
        self.assertFalse(conversation["last_message_has_attachments"])
        self.assertEqual(conversation["participant_count"], 1)
        self.assertEqual(conversation["saved_participant_count"], 1)
        self.assertEqual(conversation["unknown_participant_count"], 0)
        self.assertEqual(conversation["contact_context"][0]["display_name"], "Taylor Example")
        self.assertEqual(conversation["contact_context"][0]["primary_handle"], "+1 (512) 743-6385")
        self.assertTrue(conversation["contact_context"][0]["is_saved"])
        self.assertIn("Taylor Example", conversation["contact_context_text"])

    def test_historical_unread_rows_before_latest_read_do_not_create_99_badge(self):
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE penguin_connect_messages SET is_read = 0 WHERE provider_message_id = ?",
                ("imsg-older",),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations")

        self.assertEqual(response.status_code, 200)
        conversation = response.json()["conversations"][0]
        self.assertEqual(conversation["unread_count"], 0)
        self.assertFalse(conversation["has_unread"])

    def test_conversations_endpoint_includes_participant_contact_notes_for_search(self):
        with TestClient(app_module.app) as client:
            favorite_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "favorite": True},
            )
            note_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "note": "Ask about launch seating."},
            )
            response = client.get("/penguin-connect/conversations")

        self.assertEqual(favorite_response.status_code, 200)
        self.assertEqual(note_response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        conversation = response.json()["conversations"][0]
        self.assertTrue(conversation["contact_context"][0]["is_favorite"])
        self.assertEqual(conversation["contact_context"][0]["contact_note"], "Ask about launch seating.")
        self.assertIn("Ask about launch seating.", conversation["contact_context_text"])

    def test_conversations_endpoint_counts_unknown_participants(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, chat_type, participants,
                    alias_email, status)
                   VALUES (?, ?, ?, ?, 'dm', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_unknown_contact",
                    "chat-unknown-contact",
                    "Unknown Contact",
                    '["+1 (415) 555-0198"]',
                    "owner+unknown-contact@gmail.com",
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_unknown_contact",
                    "imessage",
                    "imsg-unknown-contact",
                    "imessage_to_gmail",
                    None,
                    "[Apple Messages] Unknown Contact",
                    "Synthetic unknown contact message",
                    "2026-03-11T09:00:00+00:00",
                    1,
                    "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations")

        self.assertEqual(response.status_code, 200)
        conversations = {
            conversation["conversation_id"]: conversation
            for conversation in response.json()["conversations"]
        }
        unknown = conversations["amc_unknown_contact"]
        self.assertEqual(unknown["participant_count"], 1)
        self.assertEqual(unknown["saved_participant_count"], 0)
        self.assertEqual(unknown["unknown_participant_count"], 1)
        self.assertEqual(unknown["contact_context"][0]["display_name"], "+1 (415) 555-0198")
        self.assertFalse(unknown["contact_context"][0]["is_saved"])

        saved = conversations["amc_test"]
        self.assertEqual(saved["participant_count"], 1)
        self.assertEqual(saved["saved_participant_count"], 1)
        self.assertEqual(saved["unknown_participant_count"], 0)

    def test_conversations_endpoint_returns_cached_threads_without_gmail_account(self):
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM penguin_connect_accounts")
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "penguin_connect.browse_imessage_chats",
            return_value={"available": True, "chats": []},
        ), TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["connected"])
        self.assertEqual(body["gmail_email"], "")
        self.assertEqual(len(body["conversations"]), 1)
        self.assertEqual(body["conversations"][0]["conversation_id"], "amc_test")
        self.assertEqual(body["conversations"][0]["last_message_preview"], "Latest message")

    def test_conversations_endpoint_discovers_whatsapp_only_when_requested(self):
        with mock.patch.object(
            app_module,
            "penguinconnect_ensure_whatsapp_conversations_discovered",
            return_value=0,
        ) as discover_whatsapp, TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations?include_whatsapp=true"
            )

        self.assertEqual(response.status_code, 200)
        discover_whatsapp.assert_called_once()
        self.assertFalse(discover_whatsapp.call_args.kwargs["provision_aliases"])

    def test_conversations_endpoint_discovers_slack_only_when_requested(self):
        with mock.patch.object(
            app_module,
            "penguinconnect_ensure_slack_conversations_discovered",
            return_value=0,
        ) as discover_slack, TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations?include_slack=true"
            )

        self.assertEqual(response.status_code, 200)
        discover_slack.assert_called_once_with(mock.ANY, "owner@gmail.com")

    def test_conversations_endpoint_refreshes_imessage_only_when_requested(self):
        with mock.patch.object(
            app_module,
            "penguinconnect_ensure_conversations_discovered",
            return_value=0,
        ) as discover_imessage, TestClient(app_module.app) as client:
            ordinary = client.get("/penguin-connect/conversations")
            refreshed = client.get(
                "/penguin-connect/conversations?include_imessage=true"
            )

        self.assertEqual(ordinary.status_code, 200)
        self.assertEqual(refreshed.status_code, 200)
        discover_imessage.assert_called_once_with(
            mock.ANY,
            "owner@gmail.com",
            provision_aliases=False,
        )

    def test_conversations_endpoint_discovers_local_threads_without_gmail_account(self):
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM penguin_connect_messages")
            conn.execute("DELETE FROM penguin_connect_aliases")
            conn.execute("DELETE FROM penguin_connect_conversations")
            conn.execute("DELETE FROM penguin_connect_accounts")
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "penguin_connect.browse_imessage_chats",
            return_value={
                "available": True,
                "chats": [
                    {
                        "chat_id": "chat-local-http",
                        "chat_identifier": "+15551234567",
                        "service": "iMessage",
                        "name": "+15551234567",
                        "chat_type": "dm",
                        "participants": ["+15551234567"],
                        "last_message_at": "2026-03-11T12:00:00+00:00",
                    }
                ],
            },
        ), mock.patch(
            "penguin_connect.fetch_imessage_messages",
            return_value=[
                {
                    "native_message_id": "http-preview-1",
                    "timestamp": "2026-03-11T12:00:00+00:00",
                    "text": "HTTP local preview",
                    "is_from_me": False,
                    "handle": "+15551234567",
                    "attachments": [],
                    "chat_id": "chat-local-http",
                }
            ],
        ), mock.patch(
            "penguin_connect._get_imessage_unread_count",
            return_value=0,
        ), TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["connected"])
        self.assertEqual(body["gmail_email"], "")
        self.assertEqual(len(body["conversations"]), 1)
        self.assertEqual(body["conversations"][0]["source_chat_id"], "chat-local-http")
        self.assertIsNone(body["conversations"][0]["alias_email"])
        self.assertEqual(body["conversations"][0]["last_message_preview"], "HTTP local preview")
        self.assertEqual(body["conversations"][0]["last_message_provider_id"], "imessage:http-preview-1")
        self.assertEqual(body["conversations"][0]["last_message_ts"], "2026-03-11T12:00:00+00:00")

        verify_conn = self._get_connection()
        try:
            row = verify_conn.execute(
                "SELECT gmail_email, alias_email FROM penguin_connect_conversations WHERE source_chat_id = ?",
                ("chat-local-http",),
            ).fetchone()
            alias_count = verify_conn.execute("SELECT COUNT(*) FROM penguin_connect_aliases").fetchone()[0]
        finally:
            verify_conn.close()
        self.assertEqual(row["gmail_email"], penguin_connect.LOCAL_MESSAGES_ACCOUNT_EMAIL)
        self.assertIsNone(row["alias_email"])
        self.assertEqual(alias_count, 0)

    def test_conversation_management_endpoint_pins_and_archives(self):
        with TestClient(app_module.app) as client:
            pin_response = client.post(
                "/penguin-connect/conversations/amc_test/management",
                json={
                    "pinned": True,
                    "muted": True,
                    "title": "Local Taylor Thread",
                    "note": "Follow up after intro",
                    "labels": ["VIP", "#Hiring", "vip", " ".join(["long"] * 20)],
                    "avatar_data_url": "data:image/png;base64,aGVsbG8=",
                    "draft_text": "Draft reply from local UI",
                    "follow_up_at": "2026-03-12T09:30",
                },
            )
            pinned_list_response = client.get("/penguin-connect/conversations")
            archive_response = client.post("/penguin-connect/conversations/amc_test/management", json={"archived": True})
            archived_list_response = client.get("/penguin-connect/conversations")
            unarchive_response = client.post("/penguin-connect/conversations/amc_test/management", json={"archived": False})
            unmute_response = client.post("/penguin-connect/conversations/amc_test/management", json={"muted": False})

        self.assertEqual(pin_response.status_code, 200)
        pin_body = pin_response.json()
        self.assertTrue(pin_body["success"])
        self.assertTrue(pin_body["is_pinned"])
        self.assertFalse(pin_body["is_archived"])
        self.assertTrue(pin_body["is_muted"])
        self.assertEqual(pin_body["title"], "Local Taylor Thread")
        self.assertEqual(pin_body["note"], "Follow up after intro")
        self.assertEqual(pin_body["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(pin_body["avatar_data_url"], "data:image/png;base64,aGVsbG8=")
        self.assertEqual(pin_body["draft_text"], "Draft reply from local UI")
        self.assertEqual(pin_body["follow_up_at"], "2026-03-12T09:30")

        pinned_conversation = pinned_list_response.json()["conversations"][0]
        self.assertTrue(pinned_conversation["is_pinned"])
        self.assertFalse(pinned_conversation["is_archived"])
        self.assertTrue(pinned_conversation["is_muted"])
        self.assertEqual(pinned_conversation["title"], "Local Taylor Thread")
        self.assertEqual(pinned_conversation["note"], "Follow up after intro")
        self.assertEqual(pinned_conversation["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(pinned_conversation["avatar_data_url"], "data:image/png;base64,aGVsbG8=")
        self.assertEqual(pinned_conversation["draft_text"], "Draft reply from local UI")
        self.assertEqual(pinned_conversation["follow_up_at"], "2026-03-12T09:30")

        self.assertEqual(archive_response.status_code, 200)
        archive_body = archive_response.json()
        self.assertFalse(archive_body["is_pinned"])
        self.assertTrue(archive_body["is_archived"])
        self.assertTrue(archive_body["is_muted"])
        self.assertEqual(archive_body["title"], "Local Taylor Thread")
        self.assertEqual(archive_body["note"], "Follow up after intro")
        self.assertEqual(archive_body["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(archive_body["avatar_data_url"], "data:image/png;base64,aGVsbG8=")
        self.assertEqual(archive_body["draft_text"], "Draft reply from local UI")
        self.assertEqual(archive_body["follow_up_at"], "2026-03-12T09:30")

        archived_conversation = archived_list_response.json()["conversations"][0]
        self.assertFalse(archived_conversation["is_pinned"])
        self.assertTrue(archived_conversation["is_archived"])
        self.assertTrue(archived_conversation["is_muted"])
        self.assertEqual(archived_conversation["title"], "Local Taylor Thread")
        self.assertEqual(archived_conversation["note"], "Follow up after intro")
        self.assertEqual(archived_conversation["labels"], ["VIP", "Hiring", "long long long long long long lo"])
        self.assertEqual(archived_conversation["draft_text"], "Draft reply from local UI")
        self.assertEqual(archived_conversation["follow_up_at"], "2026-03-12T09:30")

        self.assertEqual(unarchive_response.status_code, 200)
        unarchive_body = unarchive_response.json()
        self.assertFalse(unarchive_body["is_pinned"])
        self.assertFalse(unarchive_body["is_archived"])
        self.assertTrue(unarchive_body["is_muted"])

        self.assertEqual(unmute_response.status_code, 200)
        self.assertFalse(unmute_response.json()["is_muted"])

    def test_conversation_management_endpoint_rejects_unknown_conversation(self):
        with TestClient(app_module.app) as client:
            response = client.post("/penguin-connect/conversations/missing/management", json={"pinned": True})

        self.assertEqual(response.status_code, 404)

    def test_scheduled_message_can_be_created_listed_and_cancelled(self):
        with TestClient(app_module.app) as client:
            create_response = client.post(
                "/penguin-connect/conversations/amc_test/scheduled-messages",
                json={
                    "message": "Scheduled hello",
                    "scheduled_at": "2099-03-12T09:30:00Z",
                },
            )
            list_response = client.get(
                "/penguin-connect/conversations/amc_test/scheduled-messages",
            )
            queue_response = client.get("/penguin-connect/scheduled-messages")
            scheduled_id = create_response.json()["scheduled_message"]["scheduled_id"]
            cancel_response = client.post(
                f"/penguin-connect/scheduled-messages/{scheduled_id}/cancel",
            )

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(create_response.json()["scheduled_message"]["status"], "scheduled")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["scheduled_messages"][0]["message"], "Scheduled hello")
        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(queue_response.json()["scheduled_messages"][0]["message"], "Scheduled hello")
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["scheduled_message"]["status"], "cancelled")

    def test_due_scheduled_message_requeues_when_provider_is_offline(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_scheduled_messages
                   (scheduled_id, conversation_id, body_text, attachment_paths, scheduled_at, status)
                   VALUES (?, ?, ?, '[]', ?, 'scheduled')""",
                ("scheduled_offline", "amc_test", "Retry me", "2001-01-01T00:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "app.penguinconnect_send_manual_message",
            return_value={"success": False, "error": "provider_offline"},
        ):
            result = app_module.run_due_scheduled_messages()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["results"][0]["status"], "scheduled")
        verify_conn = self._get_connection()
        try:
            row = verify_conn.execute(
                """SELECT status, attempt_count, last_error, scheduled_at
                   FROM penguin_connect_scheduled_messages
                   WHERE scheduled_id = ?""",
                ("scheduled_offline",),
            ).fetchone()
        finally:
            verify_conn.close()
        self.assertEqual(row["status"], "scheduled")
        self.assertEqual(row["attempt_count"], 1)
        self.assertEqual(row["last_error"], "provider_offline")
        self.assertGreater(row["scheduled_at"], "2001-01-01T00:00:00+00:00")

    def test_open_messages_endpoint_opens_addressed_conversation(self):
        with mock.patch("app._open_messages_addressed", return_value="sms://open?addresses=%2B15127436385") as mock_open, TestClient(
            app_module.app
        ) as client:
            response = client.post("/penguin-connect/conversations/amc_test/open-messages")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["opened_addressed"])
        self.assertFalse(body["opened_messages"])
        self.assertEqual(body["participants"], ["+15127436385"])
        self.assertEqual(body["participants_count"], 1)
        self.assertEqual(body["messages_url"], "sms://open?addresses=%2B15127436385")
        mock_open.assert_called_once_with(["+15127436385"])

    def test_open_messages_endpoint_falls_back_to_messages_app_without_handles(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """UPDATE penguin_connect_conversations
                   SET participants = '[]', source_chat_identifier = 'chat-project-thread'
                   WHERE conversation_id = ?""",
                ("amc_test",),
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch("app._open_messages_app") as mock_open_app, mock.patch("app._open_messages_addressed") as mock_open_addressed, TestClient(
            app_module.app
        ) as client:
            response = client.post("/penguin-connect/conversations/amc_test/open-messages")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["opened_addressed"])
        self.assertTrue(body["opened_messages"])
        self.assertEqual(body["participants"], [])
        self.assertEqual(body["participants_count"], 0)
        mock_open_app.assert_called_once_with()
        mock_open_addressed.assert_not_called()

    def test_read_state_endpoint_marks_conversation_read_and_unread(self):
        with TestClient(app_module.app) as client:
            unread_response = client.post("/penguin-connect/conversations/amc_test/read-state", json={"unread": True})
            read_response = client.post("/penguin-connect/conversations/amc_test/read-state", json={"unread": False})

        self.assertEqual(unread_response.status_code, 200)
        unread_body = unread_response.json()
        self.assertEqual(unread_body["updated_messages"], 2)
        self.assertEqual(unread_body["unread_count"], 2)
        self.assertTrue(unread_body["has_unread"])

        self.assertEqual(read_response.status_code, 200)
        read_body = read_response.json()
        self.assertEqual(read_body["updated_messages"], 2)
        self.assertEqual(read_body["unread_count"], 0)
        self.assertFalse(read_body["has_unread"])

        conn = self._get_connection()
        try:
            unread_count = conn.execute(
                "SELECT COUNT(*) FROM penguin_connect_messages WHERE conversation_id = ? AND COALESCE(is_read, 0) = 0",
                ("amc_test",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(unread_count, 0)

    def test_message_management_endpoint_stars_cached_message(self):
        with TestClient(app_module.app) as client:
            star_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "starred": True},
            )
            note_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "note": "Ask about timing"},
            )
            mark_unread_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "unread": True},
            )
            messages_response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})
            mark_read_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "unread": False},
            )
            unstar_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "starred": False},
            )
            clear_note_response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "imsg-latest", "note": ""},
            )

        self.assertEqual(star_response.status_code, 200)
        self.assertTrue(star_response.json()["is_starred"])

        self.assertEqual(note_response.status_code, 200)
        note_body = note_response.json()
        self.assertTrue(note_body["is_starred"])
        self.assertEqual(note_body["message_note"], "Ask about timing")
        self.assertTrue(note_body["has_note"])

        self.assertEqual(mark_unread_response.status_code, 200)
        unread_body = mark_unread_response.json()
        self.assertFalse(unread_body["is_read"])
        self.assertEqual(unread_body["unread_count"], 1)
        self.assertTrue(unread_body["has_unread"])
        self.assertTrue(unread_body["is_starred"])
        self.assertEqual(unread_body["message_note"], "Ask about timing")

        self.assertEqual(messages_response.status_code, 200)
        message = messages_response.json()["messages"][0]
        self.assertEqual(message["provider_message_id"], "imsg-latest")
        self.assertTrue(message["is_starred"])
        self.assertEqual(message["message_note"], "Ask about timing")
        self.assertFalse(message["is_read"])

        self.assertEqual(mark_read_response.status_code, 200)
        read_body = mark_read_response.json()
        self.assertTrue(read_body["is_read"])
        self.assertEqual(read_body["unread_count"], 0)
        self.assertFalse(read_body["has_unread"])

        self.assertEqual(unstar_response.status_code, 200)
        unstar_body = unstar_response.json()
        self.assertFalse(unstar_body["is_starred"])
        self.assertEqual(unstar_body["message_note"], "Ask about timing")

        self.assertEqual(clear_note_response.status_code, 200)
        clear_note_body = clear_note_response.json()
        self.assertFalse(clear_note_body["is_starred"])
        self.assertEqual(clear_note_body["message_note"], "")
        self.assertFalse(clear_note_body["has_note"])

    def test_message_management_endpoint_rejects_unknown_message(self):
        with TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/messages/management",
                json={"provider_message_id": "missing-message", "starred": True},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "message_not_found")

    def test_message_search_endpoint_searches_cached_messages(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_message_management
                   (conversation_id, provider_message_id, is_starred, note)
                   VALUES (?, ?, 1, ?)""",
                ("amc_test", "imsg-latest", "Search-level note"),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/messages/search", params={"query": "latest", "limit": 10})
            or_response = client.get("/penguin-connect/messages/search", params={"query": "older | latest", "limit": 10})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["messages"][0]["conversation_id"], "amc_test")
        self.assertEqual(body["messages"][0]["display_name"], "Taylor")
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-latest")
        self.assertTrue(body["messages"][0]["is_starred"])
        self.assertEqual(body["messages"][0]["message_note"], "Search-level note")
        self.assertEqual(or_response.status_code, 200)
        or_body = or_response.json()
        self.assertEqual(or_body["count"], 2)
        self.assertEqual(
            [message["provider_message_id"] for message in or_body["messages"]],
            ["imsg-latest", "imsg-older"],
        )

    def test_message_search_endpoint_can_skip_native_refresh(self):
        with mock.patch(
            "app.penguinconnect_import_local_imessage_search_results",
            side_effect=AssertionError("cached search must not touch the native source"),
        ) as mock_import, TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/messages/search",
                params={
                    "query": "latest",
                    "limit": 10,
                    "refresh_source": "false",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        mock_import.assert_not_called()

    def test_message_search_endpoint_imports_raw_local_imessage_hits_without_gmail_account(self):
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM penguin_connect_messages")
            conn.execute("DELETE FROM penguin_connect_aliases")
            conn.execute("DELETE FROM penguin_connect_conversations")
            conn.execute("DELETE FROM penguin_connect_accounts")
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "penguin_connect.search_imessage_messages",
            return_value={
                "available": True,
                "messages": [
                    {
                        "chat_id": "iMessage;-;+15551234567",
                        "chat_identifier": "+15551234567",
                        "chat_name": "Raw Taylor",
                        "source_provider": "imessage",
                        "service": "iMessage",
                        "native_message_id": "raw-1",
                        "timestamp": "2026-03-11T13:00:00+00:00",
                        "is_from_me": False,
                        "handle": "+15551234567",
                        "text": "raw local needle",
                        "attachments": [{"transfer_name": "raw-note.m4a", "mime_type": "audio/mp4"}],
                    }
                ],
            },
        ), TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/messages/search", params={"query": "needle", "limit": 10})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        message = body["messages"][0]
        self.assertEqual(message["display_name"], "Raw Taylor")
        self.assertEqual(message["provider_message_id"], "imessage:raw-1")
        self.assertEqual(message["direction"], "imessage_local")
        self.assertEqual(message["attachments"][0]["transfer_name"], "raw-note.m4a")

        verify_conn = self._get_connection()
        try:
            row = verify_conn.execute(
                """SELECT c.gmail_email, m.direction, m.body_text
                   FROM penguin_connect_messages m
                   JOIN penguin_connect_conversations c ON c.conversation_id = m.conversation_id
                   WHERE m.provider_message_id = ?""",
                ("imessage:raw-1",),
            ).fetchone()
        finally:
            verify_conn.close()
        self.assertEqual(row["gmail_email"], penguin_connect.LOCAL_MESSAGES_ACCOUNT_EMAIL)
        self.assertEqual(row["direction"], "imessage_local")
        self.assertEqual(row["body_text"], "raw local needle")

    def test_message_search_endpoint_includes_self_authored_local_hits_in_mine_view(self):
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM penguin_connect_messages")
            conn.execute("DELETE FROM penguin_connect_aliases")
            conn.execute("DELETE FROM penguin_connect_conversations")
            conn.execute("DELETE FROM penguin_connect_accounts")
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "penguin_connect.search_imessage_messages",
            return_value={
                "available": True,
                "messages": [
                    {
                        "chat_id": "iMessage;-;+15557654321",
                        "chat_identifier": "+15557654321",
                        "chat_name": "Raw Morgan",
                        "source_provider": "imessage",
                        "service": "iMessage",
                        "native_message_id": "raw-me-1",
                        "timestamp": "2026-03-11T13:05:00+00:00",
                        "is_from_me": True,
                        "handle": "",
                        "text": "mine raw local",
                        "attachments": [],
                    }
                ],
            },
        ), TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/messages/search",
                params={"query": "mine raw", "view": "mine", "limit": 10},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["messages"][0]["provider_message_id"], "imessage:raw-me-1")
        self.assertEqual(body["messages"][0]["direction"], "imessage_local")
        self.assertEqual(body["messages"][0]["sender_name"], "Me")

    def test_message_search_endpoint_searches_attachment_metadata(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "imessage",
                    "imsg-voice",
                    "imessage_to_gmail",
                    None,
                    "[Apple Messages] Taylor",
                    "",
                    "2026-03-08T10:00:00+00:00",
                    1,
                    json.dumps({"attachments": [{"transfer_name": "voice-note.m4a", "mime_type": "audio/mp4"}]}),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/messages/search", params={"query": "voice-note", "limit": 10})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-voice")
        self.assertEqual(body["messages"][0]["attachments"][0]["transfer_name"], "voice-note.m4a")

    def test_message_search_endpoint_filters_scoped_views(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversation_management
                   (conversation_id, title, labels)
                   VALUES (?, ?, ?)""",
                ("amc_test", "Launch crew", json.dumps(["planning"])),
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, alias_email, status)
                   VALUES (?, ?, ?, ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_other",
                    "iMessage;-;chat-other",
                    "Morgan",
                    "owner+other@gmail.com",
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_other",
                    "imessage",
                    "imsg-audio",
                    "imessage_to_gmail",
                    None,
                    "Morgan",
                    "[Apple Messages] Morgan",
                    "",
                    "2026-03-11T10:00:00+00:00",
                    1,
                    json.dumps({"attachments": [{"transfer_name": "voice-memo.m4a", "mime_type": "audio/mp4"}]}),
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "imessage",
                    "imsg-file",
                    "imessage_to_gmail",
                    None,
                    "Taylor",
                    "[Apple Messages] Taylor",
                    "",
                    "2026-03-11T09:00:00+00:00",
                    1,
                    json.dumps({"attachments": [{"transfer_name": "deck.pdf", "mime_type": "application/pdf"}]}),
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "manual",
                    "manual-sent",
                    "manual_to_imessage",
                    "owner@gmail.com",
                    "Me",
                    "[Apple Messages] Taylor",
                    "I sent the plan. https://example.com/launch",
                    "2026-03-11T09:30:00+00:00",
                    1,
                    "{}",
                ),
            )
            conn.execute(
                "UPDATE penguin_connect_messages SET is_read = 0 WHERE provider_message_id = ?",
                ("imsg-latest",),
            )
            conn.executemany(
                """INSERT INTO penguin_connect_message_management
                   (conversation_id, provider_message_id, is_starred, note)
                   VALUES (?, ?, ?, ?)""",
                [
                    ("amc_test", "imsg-file", 1, ""),
                    ("amc_test", "manual-sent", 0, "Follow up on sent plan"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            recent_response = client.get("/penguin-connect/messages/search", params={"view": "recent", "limit": 2})
            title_response = client.get("/penguin-connect/messages/search", params={"query": "launch", "limit": 10})
            audio_response = client.get("/penguin-connect/messages/search", params={"view": "audio", "limit": 10})
            files_response = client.get("/penguin-connect/messages/search", params={"view": "files", "limit": 10})
            links_response = client.get("/penguin-connect/messages/search", params={"view": "links", "limit": 10})
            unread_response = client.get("/penguin-connect/messages/search", params={"view": "unread", "limit": 10})
            starred_response = client.get("/penguin-connect/messages/search", params={"view": "starred", "limit": 10})
            noted_response = client.get("/penguin-connect/messages/search", params={"view": "noted", "limit": 10})
            note_query_response = client.get("/penguin-connect/messages/search", params={"query": "sent plan", "limit": 10})
            mine_response = client.get("/penguin-connect/messages/search", params={"view": "mine", "limit": 10})
            date_response = client.get(
                "/penguin-connect/messages/search",
                params={"date_from": "2026-03-11", "date_to": "2026-03-11", "limit": 10},
            )
            invalid_date_response = client.get(
                "/penguin-connect/messages/search",
                params={"date_from": "not-a-date", "limit": 10},
            )
            current_response = client.get(
                "/penguin-connect/messages/search",
                params={"view": "current", "conversation_id": "amc_test", "limit": 10},
            )

        self.assertEqual(recent_response.status_code, 200)
        recent_body = recent_response.json()
        self.assertEqual(recent_body["view"], "recent")
        self.assertEqual(
            [message["provider_message_id"] for message in recent_body["messages"]],
            ["imsg-audio", "manual-sent"],
        )

        self.assertEqual(title_response.status_code, 200)
        title_body = title_response.json()
        self.assertGreaterEqual(title_body["count"], 1)
        self.assertEqual(title_body["messages"][0]["title"], "Launch crew")
        self.assertEqual(title_body["messages"][0]["labels"], ["planning"])

        self.assertEqual(audio_response.status_code, 200)
        audio_body = audio_response.json()
        self.assertEqual(audio_body["view"], "audio")
        self.assertEqual([message["provider_message_id"] for message in audio_body["messages"]], ["imsg-audio"])

        self.assertEqual(files_response.status_code, 200)
        file_ids = {message["provider_message_id"] for message in files_response.json()["messages"]}
        self.assertIn("imsg-audio", file_ids)
        self.assertIn("imsg-file", file_ids)
        self.assertNotIn("manual-sent", file_ids)

        self.assertEqual(links_response.status_code, 200)
        self.assertEqual(
            [message["provider_message_id"] for message in links_response.json()["messages"]],
            ["manual-sent"],
        )

        self.assertEqual(unread_response.status_code, 200)
        unread_ids = {message["provider_message_id"] for message in unread_response.json()["messages"]}
        self.assertIn("imsg-latest", unread_ids)

        self.assertEqual(starred_response.status_code, 200)
        starred_body = starred_response.json()
        self.assertEqual(starred_body["view"], "starred")
        self.assertEqual([message["provider_message_id"] for message in starred_body["messages"]], ["imsg-file"])
        self.assertTrue(starred_body["messages"][0]["is_starred"])

        self.assertEqual(noted_response.status_code, 200)
        noted_body = noted_response.json()
        self.assertEqual(noted_body["view"], "noted")
        self.assertEqual([message["provider_message_id"] for message in noted_body["messages"]], ["manual-sent"])
        self.assertEqual(noted_body["messages"][0]["message_note"], "Follow up on sent plan")

        self.assertEqual(note_query_response.status_code, 200)
        note_query_body = note_query_response.json()
        self.assertEqual(note_query_body["query"], "sent plan")
        self.assertEqual([message["provider_message_id"] for message in note_query_body["messages"]], ["manual-sent"])
        self.assertEqual(note_query_body["messages"][0]["message_note"], "Follow up on sent plan")

        self.assertEqual(mine_response.status_code, 200)
        self.assertEqual([message["provider_message_id"] for message in mine_response.json()["messages"]], ["manual-sent"])

        self.assertEqual(date_response.status_code, 200)
        date_body = date_response.json()
        self.assertEqual(date_body["date_from"], "2026-03-11")
        self.assertEqual(date_body["date_to"], "2026-03-11")
        date_ids = {message["provider_message_id"] for message in date_body["messages"]}
        self.assertEqual(date_ids, {"imsg-audio", "imsg-file", "manual-sent"})
        self.assertNotIn("imsg-latest", date_ids)

        self.assertEqual(invalid_date_response.status_code, 400)
        self.assertEqual(invalid_date_response.json()["detail"], "invalid_message_search_date")

        self.assertEqual(current_response.status_code, 200)
        current_body = current_response.json()
        self.assertEqual(current_body["view"], "current")
        self.assertEqual(current_body["conversation_id"], "amc_test")
        self.assertTrue(current_body["messages"])
        self.assertTrue(all(message["conversation_id"] == "amc_test" for message in current_body["messages"]))
        self.assertNotIn("imsg-audio", {message["provider_message_id"] for message in current_body["messages"]})

    def test_current_conversation_search_uses_repaired_cache_without_native_rescan(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_name,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, 'imessage', ?, 'imessage_local', ?, ?, ?, 1, '{}')""",
                (
                    "amc_test",
                    "imessage:cached-example",
                    "Taylor",
                    "An exact cached example",
                    "2026-03-11T12:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "app.penguinconnect_import_local_imessage_search_results"
        ) as native_rescan, TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/messages/search",
                params={
                    "query": "exact cached example",
                    "view": "current",
                    "conversation_id": "amc_test",
                    "limit": 10,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        native_rescan.assert_not_called()

    def test_attachment_endpoint_serves_stored_message_file(self):
        attachment_path = Path(self.tmpdir.name) / "voice-note.m4a"
        attachment_path.write_bytes(b"fake-audio")
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_test",
                    "imessage",
                    "imsg-voice-file",
                    "imessage_to_gmail",
                    None,
                    "[Apple Messages] Taylor",
                    "",
                    "2026-03-08T10:05:00+00:00",
                    1,
                    json.dumps(
                        {
                            "attachments": [
                                {
                                    "filename": str(attachment_path),
                                    "transfer_name": "voice-note.m4a",
                                    "mime_type": "audio",
                                    "size": len(b"fake-audio"),
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations/amc_test/attachments/0",
                params={"provider_message_id": "imsg-voice-file"},
            )
            inline_response = client.get(
                "/penguin-connect/conversations/amc_test/attachments/0",
                params={"provider_message_id": "imsg-voice-file", "inline": "true"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"fake-audio")
        self.assertTrue(response.headers["content-type"].startswith("audio/mp4"))
        self.assertIn("voice-note.m4a", response.headers["content-disposition"])
        self.assertEqual(inline_response.status_code, 200)
        self.assertEqual(inline_response.headers["content-disposition"], "inline")

    def test_attachment_endpoint_rejects_unknown_attachment(self):
        with TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/conversations/amc_test/attachments/0",
                params={"provider_message_id": "imsg-latest"},
            )

        self.assertEqual(response.status_code, 404)

    def test_contacts_endpoint_searches_cached_contacts(self):
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE penguin_connect_messages SET is_read = 0 WHERE provider_message_id = ?",
                ("imsg-latest",),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            favorite_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "favorite": True},
            )
            note_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "note": "Met at demo day. Ask about pilots."},
            )
            management_response = client.post(
                "/penguin-connect/conversations/amc_test/management",
                json={
                    "title": "Launch Desk",
                    "note": "Green room owners",
                    "labels": ["Sponsor"],
                    "follow_up_at": "2026-03-12T09:30",
                },
            )
            browse_response = client.get("/penguin-connect/contacts", params={"limit": 10})
            response = client.get("/penguin-connect/contacts", params={"search": "taylor", "limit": 10})
            phone_response = client.get("/penguin-connect/contacts", params={"search": "+15127436385", "limit": 10})
            note_search_response = client.get("/penguin-connect/contacts", params={"search": "pilots", "limit": 10})
            managed_note_response = client.get("/penguin-connect/contacts", params={"search": "green room", "limit": 10})
            message_context_response = client.get("/penguin-connect/contacts", params={"search": "latest message", "limit": 10})
            managed_label_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "contacts", "search": "sponsor", "limit": 10},
            )
            noted_response = client.get("/penguin-connect/contacts", params={"source": "noted", "limit": 10})
            noted_search_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "noted", "search": "demo day", "limit": 10},
            )
            saved_response = client.get("/penguin-connect/contacts", params={"source": "contacts", "limit": 10})
            threaded_response = client.get("/penguin-connect/contacts", params={"source": "threaded", "limit": 10})
            direct_response = client.get("/penguin-connect/contacts", params={"source": "direct", "limit": 10})
            groups_response = client.get("/penguin-connect/contacts", params={"source": "groups", "limit": 10})
            threaded_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "threaded", "search": "launch desk", "limit": 10},
            )
            unread_response = client.get("/penguin-connect/contacts", params={"source": "unread", "limit": 10})
            needs_reply_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "needs_reply", "limit": 10},
            )
            followup_response = client.get("/penguin-connect/contacts", params={"source": "followup", "limit": 10})
            phones_source_response = client.get("/penguin-connect/contacts", params={"source": "phones", "limit": 10})
            emails_source_response = client.get("/penguin-connect/contacts", params={"source": "emails", "limit": 10})
            favorites_response = client.get("/penguin-connect/contacts", params={"source": "favorites", "limit": 1})
            favorite_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "favorites", "search": "launch desk", "limit": 10},
            )
            unfavorite_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:15127436385", "favorite": False},
            )
            empty_favorites_response = client.get("/penguin-connect/contacts", params={"source": "favorites", "limit": 10})

        self.assertEqual(favorite_response.status_code, 200)
        self.assertTrue(favorite_response.json()["is_favorite"])
        self.assertEqual(note_response.status_code, 200)
        self.assertTrue(note_response.json()["is_favorite"])
        self.assertTrue(note_response.json()["has_note"])
        self.assertEqual(management_response.status_code, 200)
        self.assertEqual(browse_response.status_code, 200)
        browse_body = browse_response.json()
        self.assertEqual(browse_body["source"], "all")
        self.assertEqual(browse_body["count"], 2)
        self.assertEqual(
            browse_body["source_counts"],
            {
                "all": 2,
                "contacts": 2,
                "participants": 0,
                "threaded": 1,
                "direct": 1,
                "groups": 0,
                "unread": 1,
                "needs_reply": 1,
                "followup": 1,
                "favorites": 1,
                "noted": 1,
                "phones": 1,
                "emails": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total_contacts"], 2)
        self.assertEqual(body["source_counts"]["contacts"], 2)
        self.assertEqual(body["source_counts"]["threaded"], 1)
        self.assertEqual(body["source_counts"]["direct"], 1)
        self.assertEqual(body["source_counts"]["groups"], 0)
        self.assertEqual(body["source_counts"]["unread"], 1)
        self.assertEqual(body["source_counts"]["needs_reply"], 1)
        self.assertEqual(body["source_counts"]["followup"], 1)
        self.assertEqual(body["source_counts"]["favorites"], 1)
        self.assertEqual(body["source_counts"]["noted"], 1)
        self.assertEqual(body["source_counts"]["phones"], 1)
        self.assertEqual(body["source_counts"]["emails"], 1)
        self.assertEqual(body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(body["contacts"][0]["contact_key"], "phone:15127436385")
        self.assertTrue(body["contacts"][0]["is_favorite"])
        self.assertEqual(body["contacts"][0]["contact_note"], "Met at demo day. Ask about pilots.")
        self.assertEqual(body["contacts"][0]["primary_handle"], "+1 (512) 743-6385")
        self.assertEqual(body["contacts"][0]["handle_type"], "phone")
        self.assertEqual(body["contacts"][0]["thread_count"], 1)
        self.assertEqual(body["contacts"][0]["direct_thread_count"], 1)
        self.assertEqual(body["contacts"][0]["group_thread_count"], 0)
        self.assertEqual(body["contacts"][0]["needs_reply_thread_count"], 1)
        self.assertEqual(body["contacts"][0]["follow_up_thread_count"], 1)
        self.assertEqual(body["contacts"][0]["next_follow_up_at"], "2026-03-12T09:30")
        self.assertEqual(body["contacts"][0]["unread_thread_count"], 1)
        self.assertEqual(body["contacts"][0]["unread_message_count"], 1)
        self.assertEqual(body["contacts"][0]["last_thread_at"], "2026-03-10T10:00:00+00:00")
        self.assertEqual(body["contacts"][0]["thread_names"], ["Taylor"])
        self.assertEqual(phone_response.status_code, 200)
        phone_body = phone_response.json()
        self.assertEqual(phone_body["count"], 1)
        self.assertEqual(phone_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertTrue(phone_body["contacts"][0]["is_favorite"])
        self.assertEqual(note_search_response.status_code, 200)
        note_body = note_search_response.json()
        self.assertEqual(note_body["count"], 1)
        self.assertEqual(note_body["contacts"][0]["contact_key"], "phone:15127436385")
        self.assertEqual(note_body["contacts"][0]["contact_note"], "Met at demo day. Ask about pilots.")

        self.assertEqual(managed_note_response.status_code, 200)
        managed_note_body = managed_note_response.json()
        self.assertEqual(managed_note_body["count"], 1)
        self.assertEqual(managed_note_body["contacts"][0]["contact_key"], "phone:15127436385")

        self.assertEqual(message_context_response.status_code, 200)
        message_context_body = message_context_response.json()
        self.assertEqual(message_context_body["count"], 1)
        self.assertEqual(message_context_body["contacts"][0]["contact_key"], "phone:15127436385")
        self.assertEqual(message_context_body["contacts"][0]["message_context"][0]["conversation_id"], "amc_test")
        self.assertEqual(message_context_body["contacts"][0]["message_context"][0]["message_text"], "Latest message")

        self.assertEqual(managed_label_response.status_code, 200)
        managed_label_body = managed_label_response.json()
        self.assertEqual(managed_label_body["source"], "contacts")
        self.assertEqual(managed_label_body["count"], 1)
        self.assertEqual(managed_label_body["contacts"][0]["display_name"], "Taylor Example")

        self.assertEqual(noted_response.status_code, 200)
        noted_body = noted_response.json()
        self.assertEqual(noted_body["source"], "noted")
        self.assertEqual(noted_body["count"], 1)
        self.assertEqual(noted_body["contacts"][0]["contact_key"], "phone:15127436385")
        self.assertEqual(noted_body["contacts"][0]["contact_note"], "Met at demo day. Ask about pilots.")

        self.assertEqual(noted_search_response.status_code, 200)
        noted_search_body = noted_search_response.json()
        self.assertEqual(noted_search_body["source"], "noted")
        self.assertEqual(noted_search_body["count"], 1)
        self.assertEqual(noted_search_body["contacts"][0]["display_name"], "Taylor Example")

        self.assertEqual(saved_response.status_code, 200)
        saved_body = saved_response.json()
        self.assertEqual(saved_body["source"], "contacts")
        self.assertEqual(saved_body["count"], 2)
        self.assertTrue(all(contact["source"] == "contacts" for contact in saved_body["contacts"]))

        self.assertEqual(threaded_response.status_code, 200)
        threaded_body = threaded_response.json()
        self.assertEqual(threaded_body["source"], "threaded")
        self.assertEqual(threaded_body["count"], 1)
        self.assertEqual(threaded_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(threaded_body["contacts"][0]["thread_count"], 1)
        self.assertEqual(threaded_body["contacts"][0]["unread_message_count"], 1)
        self.assertEqual(threaded_body["contacts"][0]["last_thread_at"], "2026-03-10T10:00:00+00:00")

        self.assertEqual(direct_response.status_code, 200)
        direct_body = direct_response.json()
        self.assertEqual(direct_body["source"], "direct")
        self.assertEqual(direct_body["count"], 1)
        self.assertEqual(direct_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(direct_body["contacts"][0]["direct_thread_count"], 1)
        self.assertEqual(direct_body["contacts"][0]["group_thread_count"], 0)

        self.assertEqual(groups_response.status_code, 200)
        groups_body = groups_response.json()
        self.assertEqual(groups_body["source"], "groups")
        self.assertEqual(groups_body["count"], 0)

        self.assertEqual(threaded_context_response.status_code, 200)
        threaded_context_body = threaded_context_response.json()
        self.assertEqual(threaded_context_body["source"], "threaded")
        self.assertEqual(threaded_context_body["count"], 1)
        self.assertEqual(threaded_context_body["contacts"][0]["display_name"], "Taylor Example")

        self.assertEqual(unread_response.status_code, 200)
        unread_body = unread_response.json()
        self.assertEqual(unread_body["source"], "unread")
        self.assertEqual(unread_body["count"], 1)
        self.assertEqual(unread_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(unread_body["contacts"][0]["thread_count"], 1)
        self.assertEqual(unread_body["contacts"][0]["unread_thread_count"], 1)
        self.assertEqual(unread_body["contacts"][0]["unread_message_count"], 1)

        self.assertEqual(needs_reply_response.status_code, 200)
        needs_reply_body = needs_reply_response.json()
        self.assertEqual(needs_reply_body["source"], "needs_reply")
        self.assertEqual(needs_reply_body["count"], 1)
        self.assertEqual(needs_reply_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(needs_reply_body["contacts"][0]["thread_count"], 1)
        self.assertEqual(needs_reply_body["contacts"][0]["needs_reply_thread_count"], 1)
        self.assertEqual(needs_reply_body["contacts"][0]["unread_message_count"], 1)

        self.assertEqual(followup_response.status_code, 200)
        followup_body = followup_response.json()
        self.assertEqual(followup_body["source"], "followup")
        self.assertEqual(followup_body["count"], 1)
        self.assertEqual(followup_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(followup_body["contacts"][0]["thread_count"], 1)
        self.assertEqual(followup_body["contacts"][0]["follow_up_thread_count"], 1)
        self.assertEqual(followup_body["contacts"][0]["next_follow_up_at"], "2026-03-12T09:30")

        self.assertEqual(phones_source_response.status_code, 200)
        phones_source_body = phones_source_response.json()
        self.assertEqual(phones_source_body["source"], "phones")
        self.assertEqual(phones_source_body["count"], 1)
        self.assertEqual(phones_source_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(phones_source_body["contacts"][0]["handle_type"], "phone")

        self.assertEqual(emails_source_response.status_code, 200)
        emails_source_body = emails_source_response.json()
        self.assertEqual(emails_source_body["source"], "emails")
        self.assertEqual(emails_source_body["count"], 1)
        self.assertEqual(emails_source_body["contacts"][0]["display_name"], "Example Ops")
        self.assertEqual(emails_source_body["contacts"][0]["handle_type"], "email")

        self.assertEqual(favorites_response.status_code, 200)
        favorites_body = favorites_response.json()
        self.assertEqual(favorites_body["source"], "favorites")
        self.assertEqual(favorites_body["count"], 1)
        self.assertEqual(favorites_body["contacts"][0]["contact_key"], "phone:15127436385")

        self.assertEqual(favorite_context_response.status_code, 200)
        favorite_context_body = favorite_context_response.json()
        self.assertEqual(favorite_context_body["source"], "favorites")
        self.assertEqual(favorite_context_body["count"], 1)
        self.assertEqual(favorite_context_body["contacts"][0]["display_name"], "Taylor Example")

        self.assertEqual(unfavorite_response.status_code, 200)
        self.assertFalse(unfavorite_response.json()["is_favorite"])
        self.assertEqual(empty_favorites_response.status_code, 200)
        self.assertEqual(empty_favorites_response.json()["count"], 0)
        self.assertEqual(empty_favorites_response.json()["source_counts"]["favorites"], 0)
        self.assertEqual(empty_favorites_response.json()["source_counts"]["noted"], 1)

    def test_contacts_endpoint_can_skip_source_count_aggregation(self):
        with mock.patch(
            "app._contact_source_counts",
            side_effect=AssertionError("quick contact search must not aggregate source counts"),
        ) as mock_counts, mock.patch(
            "app._conversation_contact_thread_stats",
            side_effect=AssertionError("quick contact search must not aggregate thread stats"),
        ) as mock_thread_stats, TestClient(app_module.app) as client:
            response = client.get(
                "/penguin-connect/contacts",
                params={
                    "search": "taylor",
                    "limit": 10,
                    "include_counts": "false",
                    "include_thread_stats": "false",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["source_counts"], {})
        mock_counts.assert_not_called()
        mock_thread_stats.assert_not_called()

    def test_contacts_endpoint_accepts_full_directory_limit(self):
        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/contacts", params={"limit": 5000})

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(response.json()["count"], 5000)

    def test_contacts_endpoint_uses_phone_key_note_for_email_primary_contact(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO contacts
                   (first_name, last_name, organization, phone, phone_normalized, email, source_db)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "Alex",
                    "Dual",
                    "",
                    "+1 (415) 555-0103",
                    "14155550103",
                    "alex.dual@example.test",
                    "synthetic",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            favorite_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "email:alex.dual@example.test", "favorite": True},
            )
            note_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:14155550103", "note": "Bridge note on phone key."},
            )
            noted_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "noted", "search": "bridge note", "limit": 10},
            )
            favorites_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "favorites", "search": "alex", "limit": 10},
            )
            phones_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "phones", "search": "alex", "limit": 10},
            )
            emails_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "emails", "search": "alex", "limit": 10},
            )

        self.assertEqual(favorite_response.status_code, 200)
        self.assertEqual(note_response.status_code, 200)
        self.assertEqual(noted_response.status_code, 200)
        body = noted_response.json()
        self.assertEqual(body["source"], "noted")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["contacts"][0]["display_name"], "Alex Dual")
        self.assertEqual(body["contacts"][0]["contact_key"], "phone:14155550103")
        self.assertEqual(body["contacts"][0]["favorite_contact_key"], "email:alex.dual@example.test")
        self.assertEqual(body["contacts"][0]["note_contact_key"], "phone:14155550103")
        self.assertTrue(body["contacts"][0]["is_favorite"])
        self.assertEqual(body["contacts"][0]["primary_handle"], "alex.dual@example.test")
        self.assertEqual(body["contacts"][0]["handle_type"], "email")
        self.assertEqual(body["contacts"][0]["contact_note"], "Bridge note on phone key.")

        self.assertEqual(favorites_response.status_code, 200)
        favorite_body = favorites_response.json()
        self.assertEqual(favorite_body["source"], "favorites")
        self.assertEqual(favorite_body["count"], 1)
        self.assertEqual(favorite_body["contacts"][0]["display_name"], "Alex Dual")
        self.assertEqual(favorite_body["contacts"][0]["contact_note"], "Bridge note on phone key.")

        self.assertEqual(phones_response.status_code, 200)
        phones_body = phones_response.json()
        self.assertEqual(phones_body["source"], "phones")
        self.assertEqual(phones_body["source_counts"]["phones"], 2)
        self.assertEqual(phones_body["source_counts"]["emails"], 2)
        self.assertEqual(phones_body["count"], 1)
        self.assertEqual(phones_body["contacts"][0]["display_name"], "Alex Dual")
        self.assertEqual(phones_body["contacts"][0]["handle_type"], "email")
        self.assertEqual(phones_body["contacts"][0]["phone_normalized"], "14155550103")

        self.assertEqual(emails_response.status_code, 200)
        emails_body = emails_response.json()
        self.assertEqual(emails_body["source"], "emails")
        self.assertEqual(emails_body["count"], 1)
        self.assertEqual(emails_body["contacts"][0]["display_name"], "Alex Dual")
        self.assertEqual(emails_body["contacts"][0]["primary_handle"], "alex.dual@example.test")

    def test_opaque_group_identifier_is_not_treated_as_phone_contact(self):
        opaque_identifier = "deadbeefdeadbeefdeadbeefdeadbeef"
        self.assertEqual(app_module._contact_handle_type(opaque_identifier), "handle")

        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_provider, source_chat_id, source_chat_identifier,
                    source_service_name, display_name, chat_type, participants, alias_email, status)
                   VALUES (?, ?, 'imessage', ?, ?, 'iMessage', ?, 'group', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_opaque_group",
                    f"iMessage;-;{opaque_identifier}",
                    opaque_identifier,
                    opaque_identifier,
                    "[]",
                    "owner+opaque-group@gmail.com",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            conversations_response = client.get("/penguin-connect/conversations")
            contacts_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "participants", "search": opaque_identifier[:8], "limit": 10},
            )

        self.assertEqual(conversations_response.status_code, 200)
        conversation = next(
            item for item in conversations_response.json()["conversations"]
            if item["conversation_id"] == "amc_opaque_group"
        )
        self.assertEqual(conversation["participant_count"], 0)
        self.assertEqual(conversation["contact_context"], [])

        self.assertEqual(contacts_response.status_code, 200)
        self.assertEqual(contacts_response.json()["participant_count"], 0)
        self.assertFalse(
            any(
                contact["primary_handle"] == opaque_identifier
                for contact in contacts_response.json()["contacts"]
            )
        )

    def test_contacts_endpoint_searches_unsaved_conversation_participants(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, chat_type, participants,
                    alias_email, status)
                   VALUES (?, ?, ?, ?, 'dm', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_unsaved",
                    "chat-unsaved",
                    "Unsaved Thread",
                    '["+1 (415) 555-0199"]',
                    "owner+unsaved@gmail.com",
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_conversation_management
                   (conversation_id, title, note, labels, follow_up_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    "amc_unsaved",
                    "Venue Ops",
                    "Ask about soundcheck",
                    json.dumps(["Green room"]),
                    "2026-03-13T14:00",
                ),
            )
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "amc_unsaved",
                    "imessage",
                    "imsg-unsaved-rider",
                    "imessage_to_gmail",
                    None,
                    "[Apple Messages] Unsaved Thread",
                    "Please send rider setup notes.",
                    "2026-03-11T10:00:00+00:00",
                    0,
                    "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            all_browse_response = client.get("/penguin-connect/contacts", params={"limit": 10})
            response = client.get("/penguin-connect/contacts", params={"search": "5550199", "limit": 10})
            thread_name_response = client.get("/penguin-connect/contacts", params={"search": "unsaved thread", "limit": 10})
            management_note_response = client.get("/penguin-connect/contacts", params={"search": "soundcheck", "limit": 10})
            message_context_response = client.get("/penguin-connect/contacts", params={"search": "rider setup", "limit": 10})
            management_label_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "participants", "search": "green room", "limit": 10},
            )
            saved_response = client.get("/penguin-connect/contacts", params={"search": "+15127436385", "limit": 10})
            saved_only_response = client.get(
                "/penguin-connect/contacts",
                params={"search": "5550199", "source": "contacts", "limit": 10},
            )
            unsaved_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "participants", "limit": 10},
            )
            threaded_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "threaded", "limit": 10},
            )
            threaded_unsaved_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "threaded", "search": "soundcheck", "limit": 10},
            )
            unread_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "unread", "limit": 10},
            )
            unread_unsaved_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "unread", "search": "soundcheck", "limit": 10},
            )
            needs_reply_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "needs_reply", "limit": 10},
            )
            needs_reply_unsaved_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "needs_reply", "search": "soundcheck", "limit": 10},
            )
            followup_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "followup", "limit": 10},
            )
            followup_unsaved_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "followup", "search": "soundcheck", "limit": 10},
            )
            phones_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "phones", "limit": 10},
            )
            emails_browse_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "emails", "limit": 10},
            )
            favorite_unsaved_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:14155550199", "favorite": True},
            )
            note_unsaved_response = client.post(
                "/penguin-connect/contacts/management",
                json={"contact_key": "phone:14155550199", "note": "Ask about venue seating."},
            )
            favorite_unsaved_search_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "favorites", "search": "5550199", "limit": 10},
            )
            favorite_unsaved_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "favorites", "search": "venue ops", "limit": 10},
            )
            noted_unsaved_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "noted", "search": "venue", "limit": 10},
            )
            noted_unsaved_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "noted", "search": "soundcheck", "limit": 10},
            )

        self.assertEqual(all_browse_response.status_code, 200)
        all_browse_body = all_browse_response.json()
        self.assertEqual(all_browse_body["source"], "all")
        self.assertEqual(all_browse_body["count"], 3)
        self.assertEqual(
            all_browse_body["source_counts"],
            {
                "all": 3,
                "contacts": 2,
                "participants": 1,
                "threaded": 2,
                "direct": 2,
                "groups": 0,
                "unread": 1,
                "needs_reply": 2,
                "followup": 1,
                "favorites": 0,
                "noted": 0,
                "phones": 2,
                "emails": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["total_contacts"], 2)
        self.assertEqual(body["participant_count"], 1)
        self.assertEqual(body["source_counts"]["participants"], 1)
        self.assertEqual(body["source_counts"]["direct"], 2)
        self.assertEqual(body["source_counts"]["groups"], 0)
        self.assertEqual(body["source_counts"]["unread"], 1)
        self.assertEqual(body["source_counts"]["needs_reply"], 2)
        self.assertEqual(body["source_counts"]["followup"], 1)
        self.assertEqual(body["source_counts"]["phones"], 2)
        self.assertEqual(body["source_counts"]["emails"], 1)
        result = body["contacts"][0]
        self.assertEqual(result["source"], "conversation")
        self.assertFalse(result["is_saved"])
        self.assertEqual(result["display_name"], "+1 (415) 555-0199")
        self.assertEqual(result["primary_handle"], "+1 (415) 555-0199")
        self.assertEqual(result["phone_normalized"], "14155550199")
        self.assertEqual(result["handle_type"], "phone")
        self.assertEqual(result["conversation_id"], "amc_unsaved")
        self.assertIn("Unsaved Thread", result["organization"])
        self.assertEqual(result["thread_count"], 1)
        self.assertEqual(result["direct_thread_count"], 1)
        self.assertEqual(result["group_thread_count"], 0)
        self.assertEqual(result["needs_reply_thread_count"], 1)
        self.assertEqual(result["follow_up_thread_count"], 1)
        self.assertEqual(result["next_follow_up_at"], "2026-03-13T14:00")
        self.assertEqual(result["unread_thread_count"], 1)
        self.assertEqual(result["unread_message_count"], 1)
        self.assertEqual(result["last_thread_at"], "2026-03-11T10:00:00+00:00")
        self.assertEqual(result["thread_names"], ["Unsaved Thread"])

        self.assertEqual(thread_name_response.status_code, 200)
        thread_name_body = thread_name_response.json()
        self.assertEqual(thread_name_body["count"], 1)
        self.assertEqual(thread_name_body["participant_count"], 1)
        self.assertEqual(thread_name_body["contacts"][0]["source"], "conversation")
        self.assertEqual(thread_name_body["contacts"][0]["conversation_name"], "Unsaved Thread")

        self.assertEqual(management_note_response.status_code, 200)
        management_note_body = management_note_response.json()
        self.assertEqual(management_note_body["count"], 1)
        self.assertEqual(management_note_body["participant_count"], 1)
        self.assertEqual(management_note_body["contacts"][0]["conversation_note"], "Ask about soundcheck")

        self.assertEqual(message_context_response.status_code, 200)
        message_context_body = message_context_response.json()
        self.assertEqual(message_context_body["count"], 1)
        self.assertEqual(message_context_body["participant_count"], 1)
        self.assertEqual(message_context_body["contacts"][0]["source"], "conversation")
        self.assertEqual(message_context_body["contacts"][0]["contact_key"], "phone:14155550199")
        self.assertEqual(message_context_body["contacts"][0]["message_context"][0]["conversation_id"], "amc_unsaved")
        self.assertEqual(
            message_context_body["contacts"][0]["message_context"][0]["message_text"],
            "Please send rider setup notes.",
        )

        self.assertEqual(management_label_response.status_code, 200)
        management_label_body = management_label_response.json()
        self.assertEqual(management_label_body["source"], "participants")
        self.assertEqual(management_label_body["count"], 1)
        self.assertEqual(management_label_body["contacts"][0]["conversation_labels"], ["Green room"])

        self.assertEqual(saved_response.status_code, 200)
        saved_body = saved_response.json()
        self.assertEqual(saved_body["count"], 1)
        self.assertEqual(saved_body["participant_count"], 0)
        self.assertEqual(saved_body["contacts"][0]["source"], "contacts")
        self.assertTrue(saved_body["contacts"][0]["is_saved"])

        self.assertEqual(saved_only_response.status_code, 200)
        self.assertEqual(saved_only_response.json()["count"], 0)
        self.assertEqual(saved_only_response.json()["source"], "contacts")

        self.assertEqual(unsaved_browse_response.status_code, 200)
        unsaved_body = unsaved_browse_response.json()
        self.assertEqual(unsaved_body["source"], "participants")
        self.assertEqual(unsaved_body["count"], 1)
        self.assertEqual(unsaved_body["participant_count"], 1)
        self.assertEqual(unsaved_body["contacts"][0]["primary_handle"], "+1 (415) 555-0199")

        self.assertEqual(threaded_browse_response.status_code, 200)
        threaded_body = threaded_browse_response.json()
        self.assertEqual(threaded_body["source"], "threaded")
        self.assertEqual(threaded_body["count"], 2)
        self.assertEqual(threaded_body["participant_count"], 1)
        self.assertEqual({contact["source"] for contact in threaded_body["contacts"]}, {"contacts", "conversation"})
        self.assertTrue(all(contact["thread_count"] == 1 for contact in threaded_body["contacts"]))

        self.assertEqual(threaded_unsaved_context_response.status_code, 200)
        threaded_unsaved_context_body = threaded_unsaved_context_response.json()
        self.assertEqual(threaded_unsaved_context_body["source"], "threaded")
        self.assertEqual(threaded_unsaved_context_body["count"], 1)
        self.assertEqual(threaded_unsaved_context_body["participant_count"], 1)
        self.assertEqual(threaded_unsaved_context_body["contacts"][0]["source"], "conversation")
        self.assertEqual(threaded_unsaved_context_body["contacts"][0]["conversation_note"], "Ask about soundcheck")
        self.assertEqual(threaded_unsaved_context_body["contacts"][0]["thread_names"], ["Unsaved Thread"])

        self.assertEqual(unread_browse_response.status_code, 200)
        unread_body = unread_browse_response.json()
        self.assertEqual(unread_body["source"], "unread")
        self.assertEqual(unread_body["count"], 1)
        self.assertEqual(unread_body["participant_count"], 1)
        self.assertEqual(unread_body["contacts"][0]["source"], "conversation")
        self.assertEqual(unread_body["contacts"][0]["contact_key"], "phone:14155550199")
        self.assertEqual(unread_body["contacts"][0]["unread_thread_count"], 1)
        self.assertEqual(unread_body["contacts"][0]["unread_message_count"], 1)

        self.assertEqual(unread_unsaved_context_response.status_code, 200)
        unread_unsaved_context_body = unread_unsaved_context_response.json()
        self.assertEqual(unread_unsaved_context_body["source"], "unread")
        self.assertEqual(unread_unsaved_context_body["count"], 1)
        self.assertEqual(unread_unsaved_context_body["participant_count"], 1)
        self.assertEqual(unread_unsaved_context_body["contacts"][0]["source"], "conversation")
        self.assertEqual(unread_unsaved_context_body["contacts"][0]["conversation_note"], "Ask about soundcheck")
        self.assertEqual(unread_unsaved_context_body["contacts"][0]["thread_names"], ["Unsaved Thread"])

        self.assertEqual(needs_reply_browse_response.status_code, 200)
        needs_reply_body = needs_reply_browse_response.json()
        self.assertEqual(needs_reply_body["source"], "needs_reply")
        self.assertEqual(needs_reply_body["count"], 2)
        self.assertEqual(needs_reply_body["participant_count"], 1)
        self.assertEqual({contact["source"] for contact in needs_reply_body["contacts"]}, {"contacts", "conversation"})
        self.assertIn("phone:14155550199", {contact["contact_key"] for contact in needs_reply_body["contacts"]})
        self.assertTrue(all(contact["needs_reply_thread_count"] == 1 for contact in needs_reply_body["contacts"]))

        self.assertEqual(needs_reply_unsaved_context_response.status_code, 200)
        needs_reply_unsaved_context_body = needs_reply_unsaved_context_response.json()
        self.assertEqual(needs_reply_unsaved_context_body["source"], "needs_reply")
        self.assertEqual(needs_reply_unsaved_context_body["count"], 1)
        self.assertEqual(needs_reply_unsaved_context_body["participant_count"], 1)
        self.assertEqual(needs_reply_unsaved_context_body["contacts"][0]["source"], "conversation")
        self.assertEqual(needs_reply_unsaved_context_body["contacts"][0]["conversation_note"], "Ask about soundcheck")
        self.assertEqual(needs_reply_unsaved_context_body["contacts"][0]["thread_names"], ["Unsaved Thread"])

        self.assertEqual(followup_browse_response.status_code, 200)
        followup_body = followup_browse_response.json()
        self.assertEqual(followup_body["source"], "followup")
        self.assertEqual(followup_body["count"], 1)
        self.assertEqual(followup_body["participant_count"], 1)
        self.assertEqual(followup_body["contacts"][0]["source"], "conversation")
        self.assertEqual(followup_body["contacts"][0]["contact_key"], "phone:14155550199")
        self.assertEqual(followup_body["contacts"][0]["follow_up_thread_count"], 1)
        self.assertEqual(followup_body["contacts"][0]["next_follow_up_at"], "2026-03-13T14:00")

        self.assertEqual(followup_unsaved_context_response.status_code, 200)
        followup_unsaved_context_body = followup_unsaved_context_response.json()
        self.assertEqual(followup_unsaved_context_body["source"], "followup")
        self.assertEqual(followup_unsaved_context_body["count"], 1)
        self.assertEqual(followup_unsaved_context_body["participant_count"], 1)
        self.assertEqual(followup_unsaved_context_body["contacts"][0]["source"], "conversation")
        self.assertEqual(followup_unsaved_context_body["contacts"][0]["conversation_note"], "Ask about soundcheck")
        self.assertEqual(followup_unsaved_context_body["contacts"][0]["thread_names"], ["Unsaved Thread"])

        self.assertEqual(phones_browse_response.status_code, 200)
        phones_browse_body = phones_browse_response.json()
        self.assertEqual(phones_browse_body["source"], "phones")
        self.assertEqual(phones_browse_body["count"], 2)
        self.assertEqual(phones_browse_body["participant_count"], 1)
        self.assertEqual({contact["handle_type"] for contact in phones_browse_body["contacts"]}, {"phone"})
        self.assertIn("phone:14155550199", {contact["contact_key"] for contact in phones_browse_body["contacts"]})

        self.assertEqual(emails_browse_response.status_code, 200)
        emails_browse_body = emails_browse_response.json()
        self.assertEqual(emails_browse_body["source"], "emails")
        self.assertEqual(emails_browse_body["count"], 1)
        self.assertEqual(emails_browse_body["participant_count"], 0)
        self.assertEqual(emails_browse_body["contacts"][0]["display_name"], "Example Ops")
        self.assertEqual(emails_browse_body["contacts"][0]["handle_type"], "email")

        self.assertEqual(favorite_unsaved_response.status_code, 200)
        self.assertTrue(favorite_unsaved_response.json()["is_favorite"])
        self.assertEqual(note_unsaved_response.status_code, 200)
        self.assertTrue(note_unsaved_response.json()["has_note"])
        self.assertEqual(favorite_unsaved_search_response.status_code, 200)
        favorite_unsaved_body = favorite_unsaved_search_response.json()
        self.assertEqual(favorite_unsaved_body["source"], "favorites")
        self.assertEqual(favorite_unsaved_body["count"], 1)
        self.assertEqual(favorite_unsaved_body["participant_count"], 1)
        self.assertEqual(favorite_unsaved_body["source_counts"]["favorites"], 1)
        self.assertEqual(favorite_unsaved_body["source_counts"]["noted"], 1)
        self.assertEqual(favorite_unsaved_body["contacts"][0]["source"], "conversation")
        self.assertEqual(favorite_unsaved_body["contacts"][0]["contact_key"], "phone:14155550199")
        self.assertTrue(favorite_unsaved_body["contacts"][0]["is_favorite"])

        self.assertEqual(favorite_unsaved_context_response.status_code, 200)
        favorite_unsaved_context_body = favorite_unsaved_context_response.json()
        self.assertEqual(favorite_unsaved_context_body["source"], "favorites")
        self.assertEqual(favorite_unsaved_context_body["count"], 1)
        self.assertEqual(favorite_unsaved_context_body["participant_count"], 1)
        self.assertEqual(favorite_unsaved_context_body["contacts"][0]["conversation_title"], "Venue Ops")

        self.assertEqual(noted_unsaved_response.status_code, 200)
        noted_unsaved_body = noted_unsaved_response.json()
        self.assertEqual(noted_unsaved_body["source"], "noted")
        self.assertEqual(noted_unsaved_body["count"], 1)
        self.assertEqual(noted_unsaved_body["participant_count"], 1)
        self.assertEqual(noted_unsaved_body["contacts"][0]["source"], "conversation")
        self.assertEqual(noted_unsaved_body["contacts"][0]["contact_key"], "phone:14155550199")
        self.assertEqual(noted_unsaved_body["contacts"][0]["contact_note"], "Ask about venue seating.")

        self.assertEqual(noted_unsaved_context_response.status_code, 200)
        noted_unsaved_context_body = noted_unsaved_context_response.json()
        self.assertEqual(noted_unsaved_context_body["source"], "noted")
        self.assertEqual(noted_unsaved_context_body["count"], 1)
        self.assertEqual(noted_unsaved_context_body["participant_count"], 1)
        self.assertEqual(noted_unsaved_context_body["contacts"][0]["conversation_note"], "Ask about soundcheck")

    def test_contacts_endpoint_filters_direct_and_group_thread_contacts(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_conversations
                   (gmail_email, conversation_id, source_chat_id, display_name, chat_type, participants,
                    alias_email, status)
                   VALUES (?, ?, ?, ?, 'group', ?, ?, 'active')""",
                (
                    "owner@gmail.com",
                    "amc_group_contact",
                    "chat-group-contacts",
                    "Project Room",
                    '["+15127436385", "friend@example.test"]',
                    "owner+group-contacts@gmail.com",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            direct_response = client.get("/penguin-connect/contacts", params={"source": "direct", "limit": 10})
            group_response = client.get("/penguin-connect/contacts", params={"source": "groups", "limit": 10})
            group_context_response = client.get(
                "/penguin-connect/contacts",
                params={"source": "groups", "search": "project room", "limit": 10},
            )

        self.assertEqual(direct_response.status_code, 200)
        direct_body = direct_response.json()
        self.assertEqual(direct_body["source"], "direct")
        self.assertEqual(direct_body["count"], 1)
        self.assertEqual(direct_body["source_counts"]["direct"], 1)
        self.assertEqual(direct_body["source_counts"]["groups"], 2)
        self.assertEqual(direct_body["contacts"][0]["display_name"], "Taylor Example")
        self.assertEqual(direct_body["contacts"][0]["direct_thread_count"], 1)
        self.assertEqual(direct_body["contacts"][0]["group_thread_count"], 1)

        self.assertEqual(group_response.status_code, 200)
        group_body = group_response.json()
        self.assertEqual(group_body["source"], "groups")
        self.assertEqual(group_body["count"], 2)
        self.assertEqual(group_body["participant_count"], 1)
        by_key = {contact["contact_key"]: contact for contact in group_body["contacts"]}
        self.assertEqual(set(by_key), {"phone:15127436385", "email:friend@example.test"})
        self.assertEqual(by_key["phone:15127436385"]["thread_count"], 2)
        self.assertEqual(by_key["phone:15127436385"]["direct_thread_count"], 1)
        self.assertEqual(by_key["phone:15127436385"]["group_thread_count"], 1)
        self.assertEqual(by_key["email:friend@example.test"]["source"], "conversation")
        self.assertEqual(by_key["email:friend@example.test"]["thread_count"], 1)
        self.assertEqual(by_key["email:friend@example.test"]["direct_thread_count"], 0)
        self.assertEqual(by_key["email:friend@example.test"]["group_thread_count"], 1)

        self.assertEqual(group_context_response.status_code, 200)
        group_context_body = group_context_response.json()
        self.assertEqual(group_context_body["source"], "groups")
        self.assertEqual(group_context_body["count"], 2)
        self.assertEqual(group_context_body["participant_count"], 1)

    def test_contacts_refresh_endpoint_runs_refresh_once(self):
        with mock.patch(
            "app.refresh_contacts_now",
            return_value={"success": True, "contacts_count": 2, "display_names_updated": 1},
        ) as mock_refresh, TestClient(app_module.app) as client:
            mock_refresh.reset_mock()
            response = client.post("/penguin-connect/contacts/refresh")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["contacts_count"], 2)
        mock_refresh.assert_called_once_with()

    def test_contacts_create_endpoint_runs_osascript_and_refreshes_cache(self):
        completed = subprocess.CompletedProcess(["osascript"], 0, stdout="person-123\n", stderr="")
        with mock.patch("app.subprocess.run", return_value=completed) as mock_run, mock.patch(
            "app.refresh_contacts_now",
            return_value={"success": True, "contacts_count": 3, "display_names_updated": 0},
        ) as mock_refresh, TestClient(app_module.app) as client:
            mock_refresh.reset_mock()
            response = client.post(
                "/penguin-connect/contacts",
                json={
                    "first_name": "Jordan",
                    "last_name": "Example",
                    "phones": ["+1 (415) 555-0100"],
                    "emails": ["jordan@example.test"],
                    "refresh_after": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["contact_id"], "person-123")
        self.assertEqual(body["refresh"]["contacts_count"], 3)
        mock_refresh.assert_called_once_with()
        script = mock_run.call_args.args[0][2]
        self.assertIn('first name:"Jordan"', script)
        self.assertIn('value:"+1 (415) 555-0100"', script)
        self.assertIn('value:"jordan@example.test"', script)

    def test_messages_draft_endpoint_copies_and_opens_messages(self):
        with mock.patch("app._copy_to_clipboard") as mock_copy, mock.patch("app._open_messages_app") as mock_open, TestClient(
            app_module.app
        ) as client:
            response = client.post(
                "/penguin-connect/messages/draft",
                json={
                    "participants": ["+14155550100", "friend@example.test", "+1 (415) 555-0100", ""],
                    "message": "Starting this thread",
                    "copy_to_clipboard": True,
                    "open_messages": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["participants_count"], 2)
        self.assertEqual(body["participants"], ["+14155550100", "friend@example.test"])
        self.assertEqual(body["recipient_line"], "+14155550100, friend@example.test")
        self.assertEqual(body["body"], "Starting this thread")
        self.assertEqual(body["draft"], "To: +14155550100, friend@example.test\n\nStarting this thread\n")
        self.assertEqual(body["messages_url"], "sms://open?addresses=%2B14155550100%2C%20friend%40example.test")
        self.assertTrue(body["copied"])
        self.assertTrue(body["opened_messages"])
        self.assertFalse(body["opened_addressed"])
        mock_copy.assert_called_once_with(body["draft"])
        mock_open.assert_called_once_with()

    def test_messages_draft_endpoint_opens_addressed_compose(self):
        with mock.patch("app._copy_to_clipboard") as mock_copy, mock.patch("app._open_messages_app") as mock_open, mock.patch(
            "app._open_messages_addressed",
            return_value="sms://open?addresses=%2B14155550100%2C%20friend%40example.test",
        ) as mock_open_addressed, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/messages/draft",
                json={
                    "participants": ["+14155550100", "friend@example.test"],
                    "message": "Starting this thread",
                    "copy_to_clipboard": False,
                    "open_messages": True,
                    "open_addressed": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["copied"])
        self.assertFalse(body["opened_messages"])
        self.assertTrue(body["opened_addressed"])
        self.assertEqual(body["messages_url"], "sms://open?addresses=%2B14155550100%2C%20friend%40example.test")
        mock_copy.assert_not_called()
        mock_open.assert_not_called()
        mock_open_addressed.assert_called_once_with(["+14155550100", "friend@example.test"])

    def test_messages_resolve_draft_endpoint_matches_existing_thread(self):
        with TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/messages/resolve-draft",
                json={"participants": ["+1 (512) 743-6385", "+15127436385"]},
            )
            no_match_response = client.post(
                "/penguin-connect/messages/resolve-draft",
                json={"participants": ["+14155550100", "friend@example.test"]},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["participants"], ["+1 (512) 743-6385"])
        self.assertEqual(body["participants_count"], 1)
        self.assertEqual(body["match_state"], "exact")
        self.assertEqual(body["match_error"], "")
        self.assertEqual(body["matched_conversation"]["conversation_id"], "amc_test")
        self.assertEqual(body["matches"][0]["conversation_id"], "amc_test")

        self.assertEqual(no_match_response.status_code, 200)
        no_match_body = no_match_response.json()
        self.assertEqual(no_match_body["match_state"], "none")
        self.assertEqual(no_match_body["match_error"], "no_matching_conversation")
        self.assertIsNone(no_match_body["matched_conversation"])
        self.assertEqual(no_match_body["matches"], [])

    def test_messages_draft_endpoint_stages_browser_attachments(self):
        data = base64.b64encode(b"fake image bytes").decode("ascii")
        with mock.patch("app._copy_to_clipboard") as mock_copy, mock.patch("app._open_messages_app") as mock_open, mock.patch(
            "app._open_attachment_folder"
        ) as mock_open_attachments, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/messages/draft",
                json={
                    "participants": ["+14155550100"],
                    "message": "Photo for the new thread",
                    "attachments": [
                        {
                            "filename": "photo.png",
                            "mime_type": "image/png",
                            "size": len(b"fake image bytes"),
                            "data_base64": data,
                        }
                    ],
                    "copy_to_clipboard": False,
                    "open_messages": False,
                    "open_attachments": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["attachment_count"], 1)
        attachment_path = Path(body["attachment_paths"][0])
        self.assertEqual(attachment_path.parent, Path(body["attachment_folder"]))
        self.assertEqual(attachment_path.read_bytes(), b"fake image bytes")
        self.assertTrue(str(attachment_path).startswith(str(self.db_path.parent)))
        self.assertFalse(body["opened_messages"])
        self.assertFalse(body["opened_addressed"])
        self.assertTrue(body["opened_attachments"])
        mock_copy.assert_not_called()
        mock_open.assert_not_called()
        mock_open_attachments.assert_called_once_with(attachment_path.parent)

    def test_messages_draft_endpoint_stages_existing_attachment_paths(self):
        existing_path = self.db_path.parent / "existing-photo.png"
        existing_path.write_bytes(b"existing image bytes")
        with mock.patch("app._copy_to_clipboard") as mock_copy, mock.patch("app._open_messages_app") as mock_open, mock.patch(
            "app._open_attachment_folder"
        ) as mock_open_attachments, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/messages/draft",
                json={
                    "participants": ["+14155550100"],
                    "message": "Forwarding existing media",
                    "attachment_paths": [str(existing_path)],
                    "copy_to_clipboard": False,
                    "open_messages": False,
                    "open_attachments": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["attachment_count"], 1)
        attachment_path = Path(body["attachment_paths"][0])
        self.assertEqual(attachment_path.parent, Path(body["attachment_folder"]))
        self.assertEqual(attachment_path.name, "existing-photo.png")
        self.assertEqual(attachment_path.read_bytes(), b"existing image bytes")
        self.assertNotEqual(attachment_path, existing_path)
        self.assertFalse(body["opened_messages"])
        self.assertFalse(body["opened_addressed"])
        self.assertTrue(body["opened_attachments"])
        mock_copy.assert_not_called()
        mock_open.assert_not_called()
        mock_open_attachments.assert_called_once_with(attachment_path.parent)

    def test_messages_send_draft_endpoint_sends_when_recipients_match_existing_thread(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send, mock.patch(
            "app._copy_to_clipboard"
        ) as mock_copy, mock.patch("app._open_messages_addressed") as mock_open_addressed, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/messages/send-draft",
                json={
                    "participants": ["+1 (512) 743-6385", "+15127436385"],
                    "message": "Known thread send",
                    "copy_to_clipboard": True,
                    "open_addressed": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["send_mode"], "sent")
        self.assertEqual(body["conversation_id"], "amc_test")
        self.assertEqual(body["participants"], ["+1 (512) 743-6385"])
        self.assertEqual(body["matched_conversation"]["conversation_id"], "amc_test")
        mock_send.assert_called_once_with("chat-123", "Known thread send", attachment_paths=None)
        mock_copy.assert_not_called()
        mock_open_addressed.assert_not_called()

    def test_messages_send_draft_endpoint_falls_back_to_open_draft_without_match(self):
        with mock.patch("app._copy_to_clipboard") as mock_copy, mock.patch(
            "app._open_messages_addressed",
            return_value="sms://open?addresses=%2B14155550100%2C%20friend%40example.test",
        ) as mock_open_addressed, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/messages/send-draft",
                json={
                    "participants": ["+14155550100", "friend@example.test"],
                    "message": "New group draft",
                    "copy_to_clipboard": True,
                    "open_messages": False,
                    "open_addressed": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["send_mode"], "draft")
        self.assertEqual(body["send_error"], "no_matching_conversation")
        self.assertEqual(body["participants"], ["+14155550100", "friend@example.test"])
        self.assertEqual(body["draft"], "To: +14155550100, friend@example.test\n\nNew group draft\n")
        self.assertTrue(body["copied"])
        self.assertTrue(body["opened_addressed"])
        mock_copy.assert_called_once_with(body["draft"])
        mock_open_addressed.assert_called_once_with(["+14155550100", "friend@example.test"])

    def test_recipient_lists_can_be_saved_updated_listed_and_deleted(self):
        with TestClient(app_module.app) as client:
            create_response = client.post(
                "/penguin-connect/recipient-lists",
                json={
                    "name": "Demo crew",
                    "participants": ["+14155550100", "friend@example.test", "+1 (415) 555-0100"],
                    "note": "launch thread",
                },
            )
            list_response = client.get("/penguin-connect/recipient-lists")
            created = create_response.json()["recipient_list"]
            update_response = client.post(
                "/penguin-connect/recipient-lists",
                json={
                    "list_id": created["list_id"],
                    "name": "Demo crew updated",
                    "participants": ["friend@example.test", "+14155550100"],
                },
            )
            delete_response = client.delete(f"/penguin-connect/recipient-lists/{created['list_id']}")
            empty_response = client.get("/penguin-connect/recipient-lists")

        self.assertEqual(create_response.status_code, 200)
        self.assertTrue(create_response.json()["success"])
        self.assertEqual(created["name"], "Demo crew")
        self.assertEqual(created["participants"], ["+14155550100", "friend@example.test"])
        self.assertEqual(created["participants_count"], 2)
        self.assertEqual(created["note"], "launch thread")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["count"], 1)
        self.assertEqual(list_response.json()["recipient_lists"][0]["list_id"], created["list_id"])

        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()["recipient_list"]
        self.assertEqual(updated["list_id"], created["list_id"])
        self.assertEqual(updated["name"], "Demo crew updated")
        self.assertEqual(updated["participants"], ["friend@example.test", "+14155550100"])
        self.assertEqual(updated["note"], "")

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["list_id"], created["list_id"])
        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(empty_response.json()["count"], 0)

    def test_codex_ask_endpoint_runs_local_runner(self):
        with mock.patch(
            "app._run_codex_prompt",
            return_value={"success": True, "answer": "Draft reply", "prompt_chars": 42},
        ) as mock_run, TestClient(app_module.app) as client:
            response = client.post("/penguin-connect/codex/ask", json={"prompt": "Synthetic prompt"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["answer"], "Draft reply")
        mock_run.assert_called_once_with("Synthetic prompt")

    def test_codex_runner_uses_ephemeral_readonly_exec(self):
        def fake_run(command, *, input, capture_output, text, timeout):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("Synthetic answer", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with mock.patch("app.shutil.which", return_value="/usr/local/bin/codex"), mock.patch(
            "app.subprocess.run", side_effect=fake_run
        ) as mock_run:
            result = app_module._run_codex_prompt("Synthetic prompt")

        command = mock_run.call_args.args[0]
        self.assertEqual(result["answer"], "Synthetic answer")
        self.assertEqual(command[:2], ["/usr/local/bin/codex", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn("--output-last-message", command)
        self.assertIn("-", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--ask-for-approval", command)

    def test_codex_status_reports_cli_auth_without_exposing_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text('{"secret":"redacted"}', encoding="utf-8")
            with mock.patch.dict(
                app_module.os.environ,
                {
                    "CODEX_HOME": tmp,
                    "CODEX_ACCESS_TOKEN": "",
                    "CODEX_API_KEY": "",
                    "PENGUIN_CONNECT_CODEX_BIN": "codex",
                },
                clear=False,
            ), mock.patch("app.shutil.which", return_value="/usr/local/bin/codex"), TestClient(app_module.app) as client:
                response = client.get("/penguin-connect/codex/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["available"])
        self.assertTrue(body["ask_enabled"])
        self.assertEqual(body["auth_state"], "detected")
        self.assertEqual(body["auth_method"], "codex_cli_cache")
        self.assertEqual(body["credential_owner"], "codex_cli")
        self.assertIn("codex login", body["chatgpt_login_command"])
        self.assertIn("login --with-access-token", body["access_token_login_command"])
        self.assertNotIn("redacted", response.text)

    def test_codex_status_prefers_access_token_env_without_exposing_value(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            app_module.os.environ,
            {
                "CODEX_HOME": tmp,
                "CODEX_ACCESS_TOKEN": "secret-token-value",
                "CODEX_API_KEY": "",
                "PENGUIN_CONNECT_CODEX_BIN": "codex",
            },
            clear=False,
        ), mock.patch("app.shutil.which", return_value="/usr/local/bin/codex"), TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/codex/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["auth_state"], "detected")
        self.assertEqual(body["auth_method"], "codex_access_token_env")
        self.assertTrue(body["access_token_env_detected"])
        self.assertNotIn("secret-token-value", response.text)

    def test_codex_runner_maps_login_failure_to_auth_required(self):
        def fake_run(command, *, input, capture_output, text, timeout):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not logged in; run codex login")

        with mock.patch("app.shutil.which", return_value="/usr/local/bin/codex"), mock.patch(
            "app.subprocess.run", side_effect=fake_run
        ):
            with self.assertRaises(Exception) as ctx:
                app_module._run_codex_prompt("Synthetic prompt")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "codex_auth_required")

    def test_ui_endpoint_serves_console_assets(self):
        with TestClient(app_module.app) as client:
            inbox_response = client.get("/penguin-connect/ui")
            inbox_css_response = client.get("/penguin-connect/ui/inbox.css")
            logo_response = client.get("/penguin-connect/ui/penguin-logo.png")
            inbox_js_response = client.get("/penguin-connect/ui/inbox.js")
            html_response = client.get("/penguin-connect/console")
            css_response = client.get("/penguin-connect/ui/app.css")
            js_response = client.get("/penguin-connect/ui/app.js")

        self.assertEqual(inbox_response.status_code, 200)
        self.assertIn("<title>Penguin</title>", inbox_response.text)
        self.assertIn("Penguin Agent", inbox_response.text)
        self.assertIn("Search messages and people", inbox_response.text)
        self.assertEqual(inbox_css_response.status_code, 200)
        self.assertIn(".app-shell", inbox_css_response.text)
        self.assertEqual(logo_response.status_code, 200)
        self.assertEqual(logo_response.headers["content-type"], "image/png")
        self.assertGreater(len(logo_response.content), 10_000)
        self.assertIn('src="/penguin-connect/ui/penguin-logo.png"', inbox_response.text)
        self.assertEqual(inbox_js_response.status_code, 200)
        self.assertIn("loadConversations", inbox_js_response.text)
        self.assertIn("hasCachedMessage", inbox_js_response.text)
        self.assertIn("conversation?.chat_type === \"group\" && displayName", inbox_js_response.text)
        self.assertIn("peopleVisible", inbox_js_response.text)
        self.assertIn("resolvedLimit", inbox_js_response.text)
        self.assertIn('params.set("include_counts", "false")', inbox_js_response.text)
        self.assertIn('params.set("include_thread_stats", "false")', inbox_js_response.text)
        self.assertIn("moveConversationSelection", inbox_js_response.text)
        self.assertIn("openConversationMeta", inbox_js_response.text)
        self.assertIn("Primary context — currently selected conversation", inbox_js_response.text)
        self.assertIn("renderInlineAttachment", inbox_js_response.text)
        self.assertIn("missingMediaPreview", inbox_js_response.text)
        self.assertIn("message-attachment-preview", inbox_js_response.text)
        self.assertIn("&inline=true", inbox_js_response.text)
        self.assertIn("renderFilesList", inbox_js_response.text)
        self.assertIn("loadFiles", inbox_js_response.text)
        self.assertIn("loadFilePage", inbox_js_response.text)
        self.assertIn("syncAttachmentHistory", inbox_js_response.text)
        self.assertIn("/penguin-connect/attachment-library?limit=200", inbox_js_response.text)
        self.assertIn("/penguin-connect/attachment-library/status", inbox_js_response.text)
        self.assertIn("renderMentionSuggestions", inbox_js_response.text)
        self.assertIn("event.stopPropagation()", inbox_js_response.text)
        self.assertIn('query.set("fast", "true")', inbox_js_response.text)
        self.assertIn("CONVERSATION_RENDER_BATCH = 120", inbox_js_response.text)
        self.assertIn("MESSAGE_RENDER_WINDOW = 60", inbox_js_response.text)
        self.assertIn("document.createDocumentFragment()", inbox_js_response.text)
        self.assertIn("/cache-backfill", inbox_js_response.text)
        self.assertIn("activeDividerHasMatch", inbox_js_response.text)
        self.assertIn('id="mentionSuggestions"', inbox_response.text)
        self.assertIn('id="mentionButton"', inbox_response.text)
        self.assertIn(".label-filter-select", inbox_css_response.text)
        self.assertIn("renderLinksList", inbox_js_response.text)
        self.assertIn("loadLinks", inbox_js_response.text)
        self.assertIn("view=links", inbox_js_response.text)
        self.assertIn("renderLabelBar", inbox_js_response.text)
        self.assertIn("conversationLabels", inbox_js_response.text)
        self.assertIn("refreshSelectedMessages", inbox_js_response.text)
        self.assertIn("renderMessages({ preserveScroll: !shouldFollowLatest })", inbox_js_response.text)
        self.assertIn("refreshWorkspaceIfChanged", inbox_js_response.text)
        self.assertIn("rememberWorkspaceRevision", inbox_js_response.text)
        self.assertIn("/penguin-connect/workspace-revision", inbox_js_response.text)
        self.assertIn("conversationsFingerprint", inbox_js_response.text)
        self.assertIn('incremental=${incremental ? "true" : "false"}', inbox_js_response.text)
        self.assertIn("}, 5000);", inbox_js_response.text)
        self.assertIn("undoPendingSend", inbox_js_response.text)
        self.assertIn("Message queued for 15 seconds", inbox_js_response.text)
        self.assertIn("loadScheduledMessages", inbox_js_response.text)
        self.assertIn("renderQueueList", inbox_js_response.text)
        self.assertIn("loadQueue", inbox_js_response.text)
        self.assertIn("scheduleCurrentMessage", inbox_js_response.text)
        self.assertIn("offline retry enabled", inbox_js_response.text)
        self.assertIn("conversationAvatarDraft", inbox_js_response.text)
        self.assertIn("openContactDialog", inbox_js_response.text)
        self.assertIn("saveContact", inbox_js_response.text)
        self.assertIn("renderAgentHistory", inbox_js_response.text)
        self.assertIn("scrollThreadToBottom", inbox_js_response.text)
        self.assertIn("latestAnchorResizeObserver", inbox_js_response.text)
        self.assertIn("&& !event.shiftKey", inbox_js_response.text)
        self.assertIn("&& !event.isComposing", inbox_js_response.text)
        self.assertIn("preloadRecentMessages", inbox_js_response.text)
        self.assertIn("hydrateWorkspaceCache", inbox_js_response.text)
        self.assertIn("persistConversationSnapshot", inbox_js_response.text)
        self.assertIn("persistThreadSnapshot", inbox_js_response.text)
        self.assertIn("penguin-local-workspace", inbox_js_response.text)
        self.assertIn('query.set("compact", "true")', inbox_js_response.text)
        self.assertIn("openContactCard", inbox_js_response.text)
        self.assertIn("toggleConversationPane", inbox_js_response.text)
        self.assertIn("toggleAgentPane", inbox_js_response.text)
        self.assertIn("setInboxSmartView", inbox_js_response.text)
        self.assertIn("runGoShortcut", inbox_js_response.text)
        self.assertIn("setConversationPinned", inbox_js_response.text)
        self.assertIn("setConversationUnread", inbox_js_response.text)
        self.assertIn("openShortcutGuide", inbox_js_response.text)
        self.assertIn("streamAgentPrompt", inbox_js_response.text)
        self.assertIn("loadOlderMessages", inbox_js_response.text)
        self.assertIn("appendInfiniteSentinel", inbox_js_response.text)
        self.assertIn("preloadAdjacentConversations", inbox_js_response.text)
        self.assertIn("conversationProjectionCache", inbox_js_response.text)
        self.assertIn("visibleConversationIndex", inbox_js_response.text)
        self.assertIn("reconcileConversationSelection", inbox_js_response.text)
        self.assertIn("selectConversation(rows[0], { markRead: false })", inbox_js_response.text)
        self.assertIn("cancelSearchRequest", inbox_js_response.text)
        self.assertIn("&refresh_source=false", inbox_js_response.text)
        self.assertIn("&refresh_source=true", inbox_js_response.text)
        self.assertIn("updateConversationSelectionUI", inbox_js_response.text)
        self.assertIn("focusMessageComposer", inbox_js_response.text)
        self.assertIn("scrollCurrentThread", inbox_js_response.text)
        self.assertIn("archiveSelectedConversation", inbox_js_response.text)
        self.assertIn("openLabelPicker", inbox_js_response.text)
        self.assertIn("applyLabelDraft", inbox_js_response.text)
        self.assertIn("openWritingAssistant", inbox_js_response.text)
        self.assertIn("runWritingAssistant", inbox_js_response.text)
        self.assertIn("rewriteDraftInline", inbox_js_response.text)
        self.assertIn('messageComposerShell.classList.add("is-rewriting")', inbox_js_response.text)
        self.assertIn("addPendingOptimisticMessage", inbox_js_response.text)
        self.assertIn("sendMessage({ instant: true })", inbox_js_response.text)
        self.assertIn("nativeReceiptLabel", inbox_js_response.text)
        self.assertIn("reactionsByTarget", inbox_js_response.text)
        self.assertIn("openProviderToReact", inbox_js_response.text)
        self.assertIn('event.key.toLowerCase() === "j"', inbox_js_response.text)
        self.assertIn("openGifDialog", inbox_js_response.text)
        self.assertIn("queueMessageTranslation", inbox_js_response.text)
        self.assertIn("togglePinnedMessage", inbox_js_response.text)
        self.assertIn("PENGUIN_CONTACT_ACTION", inbox_js_response.text)
        self.assertIn("/penguin-connect/search/hybrid", inbox_js_response.text)
        self.assertIn("/penguin-connect/attachment-library/sync", inbox_js_response.text)
        self.assertIn('data-view="files"', inbox_response.text)
        self.assertIn('data-view="links"', inbox_response.text)
        self.assertIn('data-view="people"', inbox_response.text)
        self.assertNotIn('id="resourceTabs"', inbox_response.text)
        self.assertNotIn('id="toggleAgentButton"', inbox_response.text)
        self.assertIn('id="contactDialog"', inbox_response.text)
        self.assertIn('id="conversationMetaDialog"', inbox_response.text)
        self.assertIn('id="labelPickerDialog"', inbox_response.text)
        self.assertIn('id="shortcutDialog"', inbox_response.text)
        self.assertIn("Keyboard shortcuts", inbox_response.text)
        self.assertIn("Archive and move", inbox_response.text)
        self.assertIn("Current chat first, then your inbox", inbox_response.text)
        self.assertIn('aria-label="Agent context and workspace access"', inbox_response.text)
        self.assertIn("What should we do next?", inbox_response.text)
        self.assertIn('<option value="ask">Ask first</option>', inbox_response.text)
        self.assertIn('<option value="yolo">Full access</option>', inbox_response.text)
        self.assertIn("Sources used", inbox_response.text)
        self.assertIn("Live activity", inbox_response.text)
        self.assertIn('id="pinnedMessagesBar"', inbox_response.text)
        self.assertIn('id="autoTranslateToggle"', inbox_response.text)
        self.assertIn('id="conversationLabels"', inbox_response.text)
        self.assertIn('id="scheduleDialog"', inbox_response.text)
        self.assertIn('data-view="queue"', inbox_response.text)
        self.assertIn('id="conversationAvatarInput"', inbox_response.text)
        self.assertIn('id="writingDialog"', inbox_response.text)
        self.assertIn('id="composerAiState"', inbox_response.text)
        self.assertIn("Write with Codex (⌘J)", inbox_response.text)
        self.assertEqual(html_response.status_code, 200)
        self.assertIn("PenguinConnect Console", html_response.text)
        self.assertIn('rel="icon"', html_response.text)
        self.assertIn("Checking Messages", html_response.text)
        self.assertIn("Search people, groups, handles", html_response.text)
        self.assertIn('aria-controls="conversationList"', html_response.text)
        self.assertIn("Search local Messages", html_response.text)
        self.assertNotIn("Search all synced messages", html_response.text)
        self.assertIn('id="syncButton" type="button">Refresh</button>', html_response.text)
        self.assertIn("shortcutsButton", html_response.text)
        self.assertIn("Keyboard shortcuts", html_response.text)
        self.assertIn("shortcutHelp", html_response.text)
        self.assertIn("shortcutHelpBody", html_response.text)
        self.assertIn("closeShortcutsButton", html_response.text)
        self.assertIn("contactSearch", html_response.text)
        self.assertIn('aria-controls="contactList"', html_response.text)
        self.assertIn("contactSourceFilters", html_response.text)
        self.assertIn("contactSort", html_response.text)
        self.assertIn('<option value="favorite">Favorites first</option>', html_response.text)
        self.assertIn('<option value="threads">Threads first</option>', html_response.text)
        self.assertIn('<option value="unread">Unread first</option>', html_response.text)
        self.assertIn('<option value="needsReply">Needs reply first</option>', html_response.text)
        self.assertIn('<option value="followup">Follow-ups first</option>', html_response.text)
        self.assertIn('<option value="activity">Recent activity</option>', html_response.text)
        self.assertIn('<option value="unsaved">Unsaved first</option>', html_response.text)
        self.assertIn("contactViewName", html_response.text)
        self.assertIn("saveContactViewButton", html_response.text)
        self.assertIn("Save view", html_response.text)
        self.assertIn("contactSavedViews", html_response.text)
        self.assertIn("contactBulkActions", html_response.text)
        self.assertIn("contactSelectVisibleButton", html_response.text)
        self.assertIn("nextUnreadConversationButton", html_response.text)
        self.assertIn("nextReplyConversationButton", html_response.text)
        self.assertIn("nextFollowUpConversationButton", html_response.text)
        self.assertIn("contactNextUnreadButton", html_response.text)
        self.assertIn("Next unread", html_response.text)
        self.assertIn("contactNextReplyButton", html_response.text)
        self.assertIn("Next reply", html_response.text)
        self.assertIn("contactNextFollowUpButton", html_response.text)
        self.assertIn("Next follow-up", html_response.text)
        self.assertIn("contactAddVisibleButton", html_response.text)
        self.assertIn("contactFilterThreadsButton", html_response.text)
        self.assertIn("Threads visible", html_response.text)
        self.assertIn("contactSearchMessagesButton", html_response.text)
        self.assertIn("Find visible", html_response.text)
        self.assertIn("contactCopyVisibleButton", html_response.text)
        self.assertIn("contactCopyDetailsButton", html_response.text)
        self.assertIn("contactSaveVisibleButton", html_response.text)
        self.assertIn("contactFavoriteSelectedButton", html_response.text)
        self.assertIn("Star selected", html_response.text)
        self.assertIn("contactUnfavoriteSelectedButton", html_response.text)
        self.assertIn("Unstar selected", html_response.text)
        self.assertIn("contactCreateVisibleButton", html_response.text)
        self.assertIn("Create unknown", html_response.text)
        self.assertIn("contactClearSelectedButton", html_response.text)
        self.assertIn("contactInspector", html_response.text)
        self.assertIn("contactMoreBar", html_response.text)
        self.assertIn("contactCount", html_response.text)
        self.assertIn("loadMoreContactsButton", html_response.text)
        self.assertIn("globalMessageSearch", html_response.text)
        self.assertIn('aria-controls="messageSearchResults"', html_response.text)
        self.assertIn("globalMessageSearchFilters", html_response.text)
        self.assertIn('id="messageSearchResults" aria-live="polite" role="list"', html_response.text)
        self.assertIn("messageSearchViewName", html_response.text)
        self.assertIn("saveMessageSearchViewButton", html_response.text)
        self.assertIn("Save search", html_response.text)
        self.assertIn("messageSavedViews", html_response.text)
        self.assertIn("messageDateFrom", html_response.text)
        self.assertIn("messageDateTo", html_response.text)
        self.assertIn("clearMessageDatesButton", html_response.text)
        self.assertIn("messageViewFilters", html_response.text)
        self.assertIn("messageSearchMoreBar", html_response.text)
        self.assertIn("messageSearchCount", html_response.text)
        self.assertIn("starSearchLoadedButton", html_response.text)
        self.assertIn("Star loaded", html_response.text)
        self.assertIn("markSearchReadButton", html_response.text)
        self.assertIn("Mark read", html_response.text)
        self.assertIn("markSearchUnreadButton", html_response.text)
        self.assertIn("Mark unread", html_response.text)
        self.assertIn("addSearchSendersButton", html_response.text)
        self.assertIn("Add senders", html_response.text)
        self.assertIn("addSearchParticipantsButton", html_response.text)
        self.assertIn("Add participants", html_response.text)
        self.assertIn("saveSearchSendersButton", html_response.text)
        self.assertIn("Save senders", html_response.text)
        self.assertIn("saveSearchParticipantsButton", html_response.text)
        self.assertIn("Save participants", html_response.text)
        self.assertIn("createSearchSendersButton", html_response.text)
        self.assertIn("Create contacts", html_response.text)
        self.assertIn("createSearchParticipantsButton", html_response.text)
        self.assertIn("Create participants", html_response.text)
        self.assertIn("loadMoreSearchButton", html_response.text)
        self.assertIn("Show more", html_response.text)
        self.assertIn("messageHistoryBar", html_response.text)
        self.assertIn("loadedMessageCount", html_response.text)
        self.assertIn('aria-controls="messageList"', html_response.text)
        self.assertIn('id="messageList" aria-live="polite" role="list"', html_response.text)
        self.assertIn("replyFocusedMessageButton", html_response.text)
        self.assertIn("Reply focused", html_response.text)
        self.assertIn("copyFocusedMessageButton", html_response.text)
        self.assertIn("Copy focused", html_response.text)
        self.assertIn("draftFocusedMessageButton", html_response.text)
        self.assertIn("Draft focused", html_response.text)
        self.assertIn("starFocusedMessageButton", html_response.text)
        self.assertIn("Star focused", html_response.text)
        self.assertIn("readFocusedMessageButton", html_response.text)
        self.assertIn("Mark focused", html_response.text)
        self.assertIn("noteFocusedMessageButton", html_response.text)
        self.assertIn("Note focused", html_response.text)
        self.assertIn("nextUnreadMessageButton", html_response.text)
        self.assertIn("Next unread", html_response.text)
        self.assertIn("latestMessageButton", html_response.text)
        self.assertIn("Latest", html_response.text)
        self.assertIn("copyVisibleMessagesButton", html_response.text)
        self.assertIn("Copy visible", html_response.text)
        self.assertIn("starVisibleMessagesButton", html_response.text)
        self.assertIn("Star visible", html_response.text)
        self.assertIn("markVisibleMessagesReadButton", html_response.text)
        self.assertIn("markVisibleMessagesUnreadButton", html_response.text)
        self.assertIn("loadMoreMessagesButton", html_response.text)
        self.assertIn("Load older", html_response.text)
        self.assertIn("sendDraftButton", html_response.text)
        self.assertIn("Send if existing", html_response.text)
        self.assertIn("Send if existing (Cmd+Enter)", html_response.text)
        self.assertIn("stageDraftButton", html_response.text)
        self.assertIn("draftRecipientSuggestions", html_response.text)
        self.assertIn('aria-controls="draftRecipientSuggestions"', html_response.text)
        self.assertIn('role="listbox"', html_response.text)
        self.assertIn("draftRecipientChips", html_response.text)
        self.assertIn("draftThreadMatch", html_response.text)
        self.assertIn("draftPreviewText", html_response.text)
        self.assertIn("copyDraftRecipientsButton", html_response.text)
        self.assertIn("copyDraftBodyButton", html_response.text)
        self.assertIn("copyDraftPreviewButton", html_response.text)
        self.assertIn("draftCreateUnknownButton", html_response.text)
        self.assertIn("Create unknown", html_response.text)
        self.assertIn("openAddressedDraftButton", html_response.text)
        self.assertIn("Open addressed", html_response.text)
        self.assertIn("draftEmojiRow", html_response.text)
        self.assertIn("draftVoiceMemoButton", html_response.text)
        self.assertIn("draftVoiceMemoTimer", html_response.text)
        self.assertIn("draftAttachmentDrop", html_response.text)
        self.assertIn("draftFileInput", html_response.text)
        self.assertIn("draftAttachmentList", html_response.text)
        self.assertIn("draftOpenAttachmentsToggle", html_response.text)
        self.assertIn("recipientListName", html_response.text)
        self.assertIn("saveRecipientListButton", html_response.text)
        self.assertIn("recipientLists", html_response.text)
        self.assertIn("createContactButton", html_response.text)
        self.assertIn("voiceMemoButton", html_response.text)
        self.assertIn("voiceMemoTimer", html_response.text)
        self.assertIn("markReadButton", html_response.text)
        self.assertIn("openMessagesButton", html_response.text)
        self.assertIn("Open Messages", html_response.text)
        self.assertIn("connectionButton", html_response.text)
        self.assertIn("conversationFilters", html_response.text)
        self.assertIn('data-view="needsReply"', html_response.text)
        self.assertIn('data-view="direct"', html_response.text)
        self.assertIn('data-view="groups"', html_response.text)
        self.assertIn('data-view="unknown"', html_response.text)
        self.assertIn('data-view="favorites"', html_response.text)
        self.assertIn('data-view="drafts"', html_response.text)
        self.assertIn('data-view="unlabeled"', html_response.text)
        self.assertIn('data-view="muted"', html_response.text)
        self.assertIn('data-action="reply">Reply</button>', js_response.text)
        self.assertIn("labelFilters", html_response.text)
        self.assertIn("conversationViewName", html_response.text)
        self.assertIn("saveConversationViewButton", html_response.text)
        self.assertIn("Saved rail view name", html_response.text)
        self.assertIn("conversationSavedViews", html_response.text)
        self.assertIn("bulkActions", html_response.text)
        self.assertIn("bulkLabelsInput", html_response.text)
        self.assertIn("bulkLabelButton", html_response.text)
        self.assertIn("bulkRemoveLabelButton", html_response.text)
        self.assertIn("bulkFollowUpAt", html_response.text)
        self.assertIn("bulkSetFollowUpButton", html_response.text)
        self.assertIn("bulkClearFollowUpButton", html_response.text)
        self.assertIn("selectUnknownButton", html_response.text)
        self.assertIn("Select unknown", html_response.text)
        self.assertIn("bulkCopyThreadsButton", html_response.text)
        self.assertIn("Copy threads", html_response.text)
        self.assertIn("bulkAddPeopleButton", html_response.text)
        self.assertIn("Add people", html_response.text)
        self.assertIn("bulkCopyPeopleButton", html_response.text)
        self.assertIn("Copy people", html_response.text)
        self.assertIn("bulkSavePeopleButton", html_response.text)
        self.assertIn("Save people", html_response.text)
        self.assertIn("bulkStarPeopleButton", html_response.text)
        self.assertIn("Star people", html_response.text)
        self.assertIn("bulkUnstarPeopleButton", html_response.text)
        self.assertIn("Unstar people", html_response.text)
        self.assertIn("bulkCreatePeopleButton", html_response.text)
        self.assertIn("Create people", html_response.text)
        self.assertIn("bulkClearDraftsButton", html_response.text)
        self.assertIn("bulkPinButton", html_response.text)
        self.assertIn("bulkMuteButton", html_response.text)
        self.assertIn("bulkArchiveButton", html_response.text)
        self.assertIn("conversationSort", html_response.text)
        self.assertIn('<option value="priority">Priority</option>', html_response.text)
        self.assertIn('<option value="followup">Follow-up</option>', html_response.text)
        self.assertIn('<option value="unknown">Unknown first</option>', html_response.text)
        self.assertIn('<option value="favorites">Favorites first</option>', html_response.text)
        self.assertIn("pinButton", html_response.text)
        self.assertIn("muteButton", html_response.text)
        self.assertIn("archiveButton", html_response.text)
        self.assertIn("threadLocalTitle", html_response.text)
        self.assertIn("threadFollowUpAt", html_response.text)
        self.assertIn("threadFollowUpPresets", html_response.text)
        self.assertIn('data-follow-up-preset="tomorrow"', html_response.text)
        self.assertIn('data-follow-up-preset="weekend"', html_response.text)
        self.assertIn('data-follow-up-preset="week"', html_response.text)
        self.assertIn('data-follow-up-preset="clear"', html_response.text)
        self.assertIn("threadTags", html_response.text)
        self.assertIn("threadNote", html_response.text)
        self.assertIn("useSourceTitleButton", html_response.text)
        self.assertIn("Use group name", html_response.text)
        self.assertIn("saveManagementButton", html_response.text)
        self.assertIn("replyContext", html_response.text)
        self.assertIn("replyQuoteToggle", html_response.text)
        self.assertIn("Quote target", html_response.text)
        self.assertIn("Send reply (Cmd+Enter)", html_response.text)
        self.assertIn("codexModes", html_response.text)
        self.assertIn("codexQuestion", html_response.text)
        self.assertIn("codexAnswer", html_response.text)
        self.assertIn("askCodexButton", html_response.text)
        self.assertIn("useCodexDraftButton", html_response.text)
        self.assertIn("useCodexNewChatButton", html_response.text)
        self.assertIn("Use in new chat", html_response.text)
        self.assertIn("codexAuthRow", html_response.text)
        self.assertIn("codexAuthState", html_response.text)
        self.assertIn("refreshCodexAuthButton", html_response.text)
        self.assertIn("Check auth", html_response.text)
        self.assertIn("copyCodexLoginButton", html_response.text)
        self.assertIn("Copy login", html_response.text)
        self.assertIn("copyCodexTokenLoginButton", html_response.text)
        self.assertIn("Copy token login", html_response.text)
        self.assertIn("senderBadge", html_response.text)
        self.assertIn("threadPeople", html_response.text)
        self.assertIn("threadPeopleState", html_response.text)
        self.assertIn("threadPeopleBulkActions", html_response.text)
        self.assertIn("threadPeopleAddAllButton", html_response.text)
        self.assertIn("threadPeopleCopyAllButton", html_response.text)
        self.assertIn("Copy all", html_response.text)
        self.assertIn("threadPeopleSaveListButton", html_response.text)
        self.assertIn("threadPeopleCreateAllButton", html_response.text)
        self.assertIn("threadMedia", html_response.text)
        self.assertIn("threadMediaState", html_response.text)
        self.assertIn("mediaFilters", html_response.text)
        self.assertEqual(css_response.status_code, 200)
        self.assertIn(".contact-list", css_response.text)
        self.assertIn(".contact-source-filters", css_response.text)
        self.assertIn(".contact-sort", css_response.text)
        self.assertIn(".contact-sort select", css_response.text)
        self.assertIn(".contact-saved-views", css_response.text)
        self.assertIn(".contact-saved-view-tools", css_response.text)
        self.assertIn(".contact-saved-view-list", css_response.text)
        self.assertIn(".contact-saved-view.active", css_response.text)
        self.assertIn(".contact-bulk-actions", css_response.text)
        self.assertIn(".contact-more", css_response.text)
        self.assertIn(".contact-select-toggle", css_response.text)
        self.assertIn(".selected-contact", css_response.text)
        self.assertIn(".favorite-contact", css_response.text)
        self.assertIn(".contact-favorite", css_response.text)
        self.assertIn(".contact-copy", css_response.text)
        self.assertIn(".contact-search-messages", css_response.text)
        self.assertIn(".contact-thread-filter", css_response.text)
        self.assertIn(".contact-message", css_response.text)
        self.assertIn(".contact-note-editor", css_response.text)
        self.assertIn(".contact-note-button", css_response.text)
        self.assertIn(".contact-add", css_response.text)
        self.assertIn(".contact-actions", css_response.text)
        self.assertIn(".contact-inspector", css_response.text)
        self.assertIn(".contact-inspector-actions", css_response.text)
        self.assertIn(".contact-inspector-messages", css_response.text)
        self.assertIn(".contact-message-preview", css_response.text)
        self.assertIn(".contact-message-preview.unread", css_response.text)
        self.assertIn(".contact-message-preview.starred", css_response.text)
        self.assertIn(".contact-message-preview.noted", css_response.text)
        self.assertIn(".contact-message-preview-actions", css_response.text)
        self.assertIn(".contact-inspector-message-bulk", css_response.text)
        self.assertIn(".contact-message-preview-actions button.active", css_response.text)
        self.assertIn(".contact-message-preview-note-editor", css_response.text)
        self.assertIn(".contact-message-preview-note-actions", css_response.text)
        self.assertIn(".contact-message-preview-more", css_response.text)
        self.assertIn(".conversation-saved-views", css_response.text)
        self.assertIn(".conversation-saved-view-tools", css_response.text)
        self.assertIn(".conversation-saved-view-list", css_response.text)
        self.assertIn(".conversation-saved-view.active", css_response.text)
        self.assertIn(".active-contact", css_response.text)
        self.assertIn(".contact-related", css_response.text)
        self.assertIn(".contact-thread-link", css_response.text)
        self.assertIn(".contact-thread-message", css_response.text)
        self.assertIn(".contact-create-result", css_response.text)
        self.assertIn(".draft-recipient-chip", css_response.text)
        self.assertIn(".draft-recipient-chip.known-recipient", css_response.text)
        self.assertIn(".draft-recipient-chip-detail", css_response.text)
        self.assertIn(".draft-recipient-contact-button", css_response.text)
        self.assertIn(".draft-recipient-suggestions", css_response.text)
        self.assertIn(".draft-recipient-suggestion", css_response.text)
        self.assertIn(".draft-recipient-suggestion.active", css_response.text)
        self.assertIn(".draft-thread-match", css_response.text)
        self.assertIn(".draft-thread-match.exact", css_response.text)
        self.assertIn(".recipient-list-tools", css_response.text)
        self.assertIn(".recipient-list-item", css_response.text)
        self.assertIn(".recipient-list-actions", css_response.text)
        self.assertIn(".search-result", css_response.text)
        self.assertIn(".search-result.active-search-result", css_response.text)
        self.assertIn(".search-result.starred", css_response.text)
        self.assertIn(".search-result.unread", css_response.text)
        self.assertIn(".search-result-actions", css_response.text)
        self.assertIn(".search-result-actions button.active", css_response.text)
        self.assertIn(".search-result-actions button:disabled", css_response.text)
        self.assertIn(".compact-attachments", css_response.text)
        self.assertIn(".compact-attachment", css_response.text)
        self.assertIn(".compact-attachment.audio", css_response.text)
        self.assertIn(".compact-attachment-label", css_response.text)
        self.assertIn(".message-search-filters", css_response.text)
        self.assertIn(".message-saved-views", css_response.text)
        self.assertIn(".message-saved-view-tools", css_response.text)
        self.assertIn(".message-saved-view-list", css_response.text)
        self.assertIn(".message-saved-view.active", css_response.text)
        self.assertIn(".message-view-filters", css_response.text)
        self.assertIn(".message-search-more", css_response.text)
        self.assertIn(".message-search-actions", css_response.text)
        self.assertIn(".message-history-bar", css_response.text)
        self.assertIn(".loaded-message-actions", css_response.text)
        self.assertIn(".message-date-range", css_response.text)
        self.assertIn(".message-date-field", css_response.text)
        self.assertIn(".toggle-row", css_response.text)
        self.assertIn(".unread-badge", css_response.text)
        self.assertIn(".label-badge", css_response.text)
        self.assertIn(".draft-badge", css_response.text)
        self.assertIn(".muted-badge", css_response.text)
        self.assertIn(".unknown-badge", css_response.text)
        self.assertIn(".followup-badge", css_response.text)
        self.assertIn(".attachment-link", css_response.text)
        self.assertIn(".audio-attachment", css_response.text)
        self.assertIn(".image-attachment", css_response.text)
        self.assertIn(".conversation-filters", css_response.text)
        self.assertIn(".conversation-sort", css_response.text)
        self.assertIn(".conversation-sort select", css_response.text)
        self.assertIn(".label-filters", css_response.text)
        self.assertIn(".bulk-actions", css_response.text)
        self.assertIn(".rail-actions", css_response.text)
        self.assertIn(".shortcut-help-backdrop", css_response.text)
        self.assertIn(".shortcut-help-body", css_response.text)
        self.assertIn(".shortcut-help-row kbd", css_response.text)
        self.assertIn(".bulk-label-row", css_response.text)
        self.assertIn(".bulk-draft-row", css_response.text)
        self.assertIn(".bulk-label-input", css_response.text)
        self.assertIn(".conversation-select", css_response.text)
        self.assertIn(".conversation-row-actions", css_response.text)
        self.assertIn(".conversation-row-actions button.active", css_response.text)
        self.assertIn(".conversation-contact-context", css_response.text)
        self.assertIn(".conversation-preview", css_response.text)
        self.assertIn(".message.starred", css_response.text)
        self.assertIn(".message.noted", css_response.text)
        self.assertIn(".search-highlight", css_response.text)
        self.assertIn(".search-result.noted", css_response.text)
        self.assertIn(".search-result-note-editor", css_response.text)
        self.assertIn(".message-note-editor", css_response.text)
        self.assertIn(".message-actions", css_response.text)
        self.assertIn(".message-followup-select", css_response.text)
        self.assertIn(".message-label-select", css_response.text)
        self.assertIn(".reply-context", css_response.text)
        self.assertIn(".reply-context label", css_response.text)
        self.assertIn(".voice-memo-row", css_response.text)
        self.assertIn(".voice-memo-timer", css_response.text)
        self.assertIn(".thread-management", css_response.text)
        self.assertIn(".followup-control", css_response.text)
        self.assertIn(".followup-presets", css_response.text)
        self.assertIn(".codex-modes", css_response.text)
        self.assertIn(".codex-auth-row", css_response.text)
        self.assertIn("#codexQuestion", css_response.text)
        self.assertIn("#codexAnswer", css_response.text)
        self.assertIn(".thread-people", css_response.text)
        self.assertIn(".thread-people-bulk", css_response.text)
        self.assertIn(".thread-person-actions", css_response.text)
        self.assertIn(".thread-person-name", css_response.text)
        self.assertIn(".thread-person.known-contact", css_response.text)
        self.assertIn(".thread-person.favorite-contact", css_response.text)
        self.assertIn(".media-filters", css_response.text)
        self.assertIn(".thread-media", css_response.text)
        self.assertIn(".media-item", css_response.text)
        self.assertEqual(js_response.status_code, 200)
        self.assertIn("buildCodexPrompt", js_response.text)
        self.assertIn("selectedConversationContext", js_response.text)
        self.assertIn("messageSearchContext", js_response.text)
        self.assertIn("highlightTerms", js_response.text)
        self.assertIn("appendHighlightedText", js_response.text)
        self.assertIn('mark.className = "search-highlight"', js_response.text)
        self.assertIn('appendHighlightedText(mainButton.querySelector(".conversation-name")', js_response.text)
        self.assertIn('appendHighlightedText(mainButton.querySelector(".conversation-meta")', js_response.text)
        self.assertIn("appendHighlightedText(contactContext, contactContextText, terms)", js_response.text)
        self.assertIn("appendHighlightedText(preview, previewText, terms)", js_response.text)
        self.assertIn('appendHighlightedText(noteBox.querySelector("span"), noteText, terms)', js_response.text)
        self.assertIn("appendHighlightedText(pill, attachmentLabel(attachment), terms)", js_response.text)
        self.assertIn("renderCompactAttachmentChips(message, { conversationId = \"\", limit = 4, terms = [] }", js_response.text)
        self.assertIn("appendHighlightedText(label, attachmentLabel(attachment), terms)", js_response.text)
        self.assertIn("visibleLoadedMessages", js_response.text)
        self.assertIn("manageableLoadedMessages", js_response.text)
        self.assertIn("bulkUpdateVisibleLoadedMessages", js_response.text)
        self.assertIn("starVisibleLoadedMessages", js_response.text)
        self.assertIn("markVisibleLoadedMessagesRead", js_response.text)
        self.assertIn("markVisibleLoadedMessagesUnread", js_response.text)
        self.assertIn("copyVisibleLoadedMessages", js_response.text)
        self.assertIn('el.copyVisibleMessagesButton.addEventListener("click"', js_response.text)
        self.assertIn('el.starVisibleMessagesButton.addEventListener("click"', js_response.text)
        self.assertIn("contactRecentContext", js_response.text)
        self.assertIn("Loaded contact recent messages:", js_response.text)
        self.assertIn("newChatThreadMatchText", js_response.text)
        self.assertIn("newChatDraftContext", js_response.text)
        self.assertIn("New chat draft:", js_response.text)
        self.assertIn("Existing thread check:", js_response.text)
        self.assertIn("Staged attachment folder:", js_response.text)
        self.assertIn("contactContext", js_response.text)
        self.assertIn("contactSources", js_response.text)
        self.assertIn('{ key: "threaded", label: "Threaded" }', js_response.text)
        self.assertIn('{ key: "unread", label: "Unread" }', js_response.text)
        self.assertIn('{ key: "needs_reply", label: "Needs reply" }', js_response.text)
        self.assertIn('{ key: "followup", label: "Follow-up" }', js_response.text)
        self.assertIn('{ key: "phones", label: "Phones" }', js_response.text)
        self.assertIn('{ key: "emails", label: "Emails" }', js_response.text)
        self.assertIn("contactSourceCounts", js_response.text)
        self.assertIn("contactSortLabels", js_response.text)
        self.assertIn("compareContactFavorite", js_response.text)
        self.assertIn("compareContactNoted", js_response.text)
        self.assertIn("contactThreadContextCount", js_response.text)
        self.assertIn("contactThreadActivityText", js_response.text)
        self.assertIn("Needs reply threads:", js_response.text)
        self.assertIn("Follow-up threads:", js_response.text)
        self.assertIn("Next follow-up:", js_response.text)
        self.assertIn("compareContactThreads", js_response.text)
        self.assertIn('threads: "Threads first"', js_response.text)
        self.assertIn("compareContactUnread", js_response.text)
        self.assertIn('unread: "Unread first"', js_response.text)
        self.assertIn("compareContactNeedsReply", js_response.text)
        self.assertIn('needsReply: "Needs reply first"', js_response.text)
        self.assertIn("compareContactFollowUp", js_response.text)
        self.assertIn('followup: "Follow-ups first"', js_response.text)
        self.assertIn("compareContactActivity", js_response.text)
        self.assertIn('activity: "Recent activity"', js_response.text)
        self.assertIn("compareContactSaved", js_response.text)
        self.assertIn("compareContactUnsaved", js_response.text)
        self.assertIn("compareContactRecent", js_response.text)
        self.assertIn("visibleContacts", js_response.text)
        self.assertIn("contactHasUnreadActivity", js_response.text)
        self.assertIn("contactNeedsReplyActivity", js_response.text)
        self.assertIn("contactHasFollowUpActivity", js_response.text)
        self.assertIn("focusNextContactByPredicate", js_response.text)
        self.assertIn("focusNextUnreadContact", js_response.text)
        self.assertIn("focusNextNeedsReplyContact", js_response.text)
        self.assertIn("focusNextFollowUpContact", js_response.text)
        self.assertIn("Selected ${label} contact", js_response.text)
        self.assertIn("No unread contacts visible", js_response.text)
        self.assertIn("No needs-reply contacts visible", js_response.text)
        self.assertIn("No follow-up contacts visible", js_response.text)
        self.assertIn('el.contactSort.addEventListener("change"', js_response.text)
        self.assertIn('appendHighlightedText(item.querySelector(".contact-name")', js_response.text)
        self.assertIn('appendHighlightedText(item.querySelector(".contact-handle")', js_response.text)
        self.assertIn('appendHighlightedText(noteBox.querySelector("span"), noteText, terms)', js_response.text)
        self.assertIn('appendHighlightedText(el.contactInspector.querySelector(".contact-inspector-name")', js_response.text)
        self.assertIn("quickCreateContact", js_response.text)
        self.assertIn("quickCreateStatus", js_response.text)
        self.assertIn("Contact created", js_response.text)
        self.assertIn("source_counts", js_response.text)
        self.assertIn('{ key: "direct", label: "Direct" }', js_response.text)
        self.assertIn('{ key: "groups", label: "Groups" }', js_response.text)
        self.assertIn('{ key: "noted", label: "Noted" }', js_response.text)
        self.assertIn("direct_thread_count", js_response.text)
        self.assertIn("group_thread_count", js_response.text)
        self.assertIn("Direct threads:", js_response.text)
        self.assertIn("Group threads:", js_response.text)
        self.assertIn("No threaded contacts", js_response.text)
        self.assertIn("No direct contacts", js_response.text)
        self.assertIn("No group contacts", js_response.text)
        self.assertIn("No unread contacts", js_response.text)
        self.assertIn("No contacts need reply", js_response.text)
        self.assertIn("No follow-up contacts", js_response.text)
        self.assertIn("No phone contacts", js_response.text)
        self.assertIn("No email contacts", js_response.text)
        self.assertIn("No noted contacts", js_response.text)
        self.assertIn("Loading contacts", js_response.text)
        self.assertIn("Loading threaded contacts", js_response.text)
        self.assertIn("Loading direct contacts", js_response.text)
        self.assertIn("Loading group contacts", js_response.text)
        self.assertIn("Loading unread contacts", js_response.text)
        self.assertIn("Loading contacts needing reply", js_response.text)
        self.assertIn("Loading follow-up contacts", js_response.text)
        self.assertIn("Loading phone contacts", js_response.text)
        self.assertIn("Loading email contacts", js_response.text)
        self.assertIn("Loading saved contacts", js_response.text)
        self.assertNotIn("Type 2+ chars to search contacts", js_response.text)
        self.assertIn("No saved contacts", js_response.text)
        self.assertIn("renderCodexModes", js_response.text)
        self.assertIn("askCodex", js_response.text)
        self.assertIn("loadCodexStatus", js_response.text)
        self.assertIn("copyCodexLoginCommand", js_response.text)
        self.assertIn("/penguin-connect/codex/status", js_response.text)
        self.assertIn("codex_auth_required", js_response.text)
        self.assertIn("Codex ready", js_response.text)
        self.assertIn("useCodexAnswerAsDraft", js_response.text)
        self.assertIn("useCodexAnswerAsNewChatDraft", js_response.text)
        self.assertIn("Codex answer moved to new chat", js_response.text)
        self.assertIn("renderCodexAnswerControls", js_response.text)
        self.assertIn("Messages ready", js_response.text)
        self.assertIn("local send enabled", js_response.text)
        self.assertIn("refreshLocalMessages", js_response.text)
        self.assertIn("autoRefreshIntervalFromUrl", js_response.text)
        self.assertIn("auto_refresh_ms", js_response.text)
        self.assertIn("autoRefreshIntervalMs", js_response.text)
        self.assertIn("shouldAutoRefreshLocalState", js_response.text)
        self.assertIn("autoRefreshLocalState", js_response.text)
        self.assertIn("startAutoRefresh", js_response.text)
        self.assertIn("conversationActivitySnapshot", js_response.text)
        self.assertIn("conversationActivitySignature", js_response.text)
        self.assertIn("updateConversationActivitySnapshot", js_response.text)
        self.assertIn("newConversationActivity", js_response.text)
        self.assertIn("announceNewConversationActivity", js_response.text)
        self.assertIn("activityStatusHoldMs = 45000", js_response.text)
        self.assertIn("activityStatusUntil", js_response.text)
        self.assertIn("Date.now() + activityStatusHoldMs", js_response.text)
        self.assertIn("New message ·", js_response.text)
        self.assertIn("announceActivity: true", js_response.text)
        self.assertIn("preserveManagementEditing", js_response.text)
        self.assertIn("quiet = false", js_response.text)
        self.assertIn("document.addEventListener(\"visibilitychange\"", js_response.text)
        self.assertIn("Local Messages refreshed", js_response.text)
        self.assertIn("localRefreshBusy", js_response.text)
        self.assertNotIn("Sync requested", js_response.text)
        self.assertNotIn('/penguin-connect/conversations/sync', js_response.text)
        self.assertIn("Messages", js_response.text)
        self.assertIn("conversationDisplayName", js_response.text)
        self.assertIn("conversationSourceTitle", js_response.text)
        self.assertIn("conversationSourceTitleText", js_response.text)
        self.assertIn("Group name:", js_response.text)
        self.assertIn("Messages group name:", js_response.text)
        self.assertIn("useSourceTitleAsLocalTitle", js_response.text)
        self.assertIn("Already using Messages name", js_response.text)
        self.assertIn("Set local title to", js_response.text)
        self.assertIn("conversationSortLabels", js_response.text)
        self.assertIn('direct: "Direct"', js_response.text)
        self.assertIn('groups: "Groups"', js_response.text)
        self.assertIn('unknown: "Unknown"', js_response.text)
        self.assertIn('favorites: "Favorites"', js_response.text)
        self.assertIn("isDirectConversation", js_response.text)
        self.assertIn("isGroupConversation", js_response.text)
        self.assertIn("conversationUnknownParticipantCount", js_response.text)
        self.assertIn("conversationHasUnknownParticipants", js_response.text)
        self.assertIn("conversationFavoriteParticipantCount", js_response.text)
        self.assertIn("conversationHasFavoriteParticipants", js_response.text)
        self.assertIn("conversationViewsStorageKey", js_response.text)
        self.assertIn("savedConversationViews", js_response.text)
        self.assertIn("renderConversationSavedViews", js_response.text)
        self.assertIn("saveConversationView", js_response.text)
        self.assertIn("useConversationView", js_response.text)
        self.assertIn("deleteConversationView", js_response.text)
        self.assertIn("Saved conversation view", js_response.text)
        self.assertIn("compareConversationPriority", js_response.text)
        self.assertIn("compareConversationUnread", js_response.text)
        self.assertIn("compareConversationFollowUp", js_response.text)
        self.assertIn("compareConversationUnknown", js_response.text)
        self.assertIn('unknown: "Unknown first"', js_response.text)
        self.assertIn("compareConversationFavorite", js_response.text)
        self.assertIn('favorites: "Favorites first"', js_response.text)
        self.assertIn("favorite-badge", js_response.text)
        self.assertIn("compareConversationName", js_response.text)
        self.assertIn("compareConversations", js_response.text)
        self.assertIn('el.conversationSort.addEventListener("change"', js_response.text)
        self.assertIn("conversationManagementToggleFields", js_response.text)
        self.assertIn("conversationManagementToggleLabel", js_response.text)
        self.assertIn("replyToConversationRow", js_response.text)
        self.assertIn('row.querySelector(\'[data-action="reply"]\').addEventListener("click", () => replyToConversationRow(conversation));', js_response.text)
        self.assertIn("toggleConversationRowManagement", js_response.text)
        self.assertIn("toggleConversationRowReadState", js_response.text)
        self.assertIn("selectedConversationSummary", js_response.text)
        self.assertIn("selectedConversationSummariesText", js_response.text)
        self.assertIn("copySelectedConversationSummaries", js_response.text)
        self.assertIn("bulkCopyThreadsButton.addEventListener", js_response.text)
        self.assertIn("Copied ${targets.length} selected thread", js_response.text)
        self.assertIn('data-action="read-state"', js_response.text)
        self.assertIn("sourceDisplayName", js_response.text)
        self.assertIn("conversationParticipants", js_response.text)
        self.assertIn("conversationContactContextText", js_response.text)
        self.assertIn("conversation-contact-context", js_response.text)
        self.assertIn("contact_context_text", js_response.text)
        self.assertIn("contact_context", js_response.text)
        self.assertIn("loadThreadContactMatches", js_response.text)
        self.assertIn("source=all", js_response.text)
        self.assertIn("toggleThreadParticipantFavorite", js_response.text)
        self.assertIn("showThreadParticipantContact", js_response.text)
        self.assertIn("Contact detail opened", js_response.text)
        self.assertIn("refreshContactPanelAfterExternalManagement", js_response.text)
        self.assertIn("addThreadParticipantsToDraft", js_response.text)
        self.assertIn("copyThreadParticipants", js_response.text)
        self.assertIn('el.threadPeopleCopyAllButton.addEventListener("click"', js_response.text)
        self.assertIn("saveThreadParticipantsAsRecipientList", js_response.text)
        self.assertIn("createUnknownThreadParticipants", js_response.text)
        self.assertIn("unknownThreadParticipants", js_response.text)
        self.assertIn("contactCreatePayloadFromHandle", js_response.text)
        self.assertIn("currentThreadParticipantHandles", js_response.text)
        self.assertIn("contactManagementKeyForHandle", js_response.text)
        self.assertIn("contactMatchesHandle", js_response.text)
        self.assertIn("visibleContactRecipientHandles", js_response.text)
        self.assertIn("addVisibleContactsToDraft", js_response.text)
        self.assertIn("copyVisibleContacts", js_response.text)
        self.assertIn("contactBulkDetailContacts", js_response.text)
        self.assertIn("copyBulkContactDetails", js_response.text)
        self.assertIn("contactCopyDetailsButton.addEventListener", js_response.text)
        self.assertIn("Copy selected details", js_response.text)
        self.assertIn("No contacts to copy", js_response.text)
        self.assertIn("copyContactHandle", js_response.text)
        self.assertIn("contactDetailText", js_response.text)
        self.assertIn("copyContactDetail", js_response.text)
        self.assertIn('data-action="copy-detail"', js_response.text)
        self.assertIn("Contact detail copied", js_response.text)
        self.assertIn("Thread activity:", js_response.text)
        self.assertIn("Unread threads:", js_response.text)
        self.assertIn("Unread messages:", js_response.text)
        self.assertIn("Thread names:", js_response.text)
        self.assertIn("Loaded recent messages:", js_response.text)
        self.assertIn("openContactInMessages", js_response.text)
        self.assertIn("Open contact in Messages", js_response.text)
        self.assertIn("contactConversationSearchQuery", js_response.text)
        self.assertIn("filterConversationsForContact", js_response.text)
        self.assertIn("filterConversationsForParticipant", js_response.text)
        self.assertIn("Showing ${count} thread", js_response.text)
        self.assertIn('aria-label="Filter conversations for contact"', js_response.text)
        self.assertIn("copyParticipantHandle", js_response.text)
        self.assertIn("searchMessagesForParticipant", js_response.text)
        self.assertIn('data-action="threads"', js_response.text)
        self.assertIn('data-action="messages"', js_response.text)
        self.assertIn("filterConversationsForMessageContact", js_response.text)
        self.assertIn("filterConversationsForSearchResultContact", js_response.text)
        self.assertIn("filterConversationsForLoadedMessageContact", js_response.text)
        self.assertIn("saveVisibleContactsAsRecipientList", js_response.text)
        self.assertIn("renderThreadPeople", js_response.text)
        self.assertIn("fillContactFormFromHandle", js_response.text)
        self.assertIn("fillContactFormFromContact", js_response.text)
        self.assertIn("renderContactSourceFilters", js_response.text)
        self.assertIn("contactSearchViewsStorageKey", js_response.text)
        self.assertIn("savedContactSearchViews", js_response.text)
        self.assertIn("renderContactSavedViews", js_response.text)
        self.assertIn("saveContactSearchView", js_response.text)
        self.assertIn("useContactSearchView", js_response.text)
        self.assertIn("deleteContactSearchView", js_response.text)
        self.assertIn("Saved contact search", js_response.text)
        self.assertIn("renderContactMoreControls", js_response.text)
        self.assertIn("loadMoreContacts", js_response.text)
        self.assertIn("contactLimitMax", js_response.text)
        self.assertIn("renderContacts", js_response.text)
        self.assertIn("focusNextVisibleConversationByPredicate", js_response.text)
        self.assertIn("focusNextUnreadConversation", js_response.text)
        self.assertIn("focusNextNeedsReplyConversation", js_response.text)
        self.assertIn("focusNextFollowUpConversation", js_response.text)
        self.assertIn("No unread threads visible", js_response.text)
        self.assertIn("No needs-reply threads visible", js_response.text)
        self.assertIn("No follow-up threads visible", js_response.text)
        self.assertIn("selectedContactKeys", js_response.text)
        self.assertIn("contactSelectionKey", js_response.text)
        self.assertIn("selectedContactRecipientHandles", js_response.text)
        self.assertIn("conversationSearchTerms", js_response.text)
        self.assertIn("conversationMatchesSearch", js_response.text)
        self.assertIn("filterConversationsForContactHandles", js_response.text)
        self.assertIn("Threads selected", js_response.text)
        self.assertIn("searchMessagesForContactHandles", js_response.text)
        self.assertIn("Find selected", js_response.text)
        self.assertIn("contactBulkCreatableContacts", js_response.text)
        self.assertIn("contactBulkManageableContacts", js_response.text)
        self.assertIn("setBulkContactFavorites", js_response.text)
        self.assertIn('el.contactFavoriteSelectedButton.addEventListener("click"', js_response.text)
        self.assertIn("createVisibleUnknownContacts", js_response.text)
        self.assertIn("contactCreatePayload", js_response.text)
        self.assertIn("selectVisibleContacts", js_response.text)
        self.assertIn("clearSelectedContacts", js_response.text)
        self.assertIn("Add selected", js_response.text)
        self.assertIn("toggleContactFavorite", js_response.text)
        self.assertIn("saveContactNote", js_response.text)
        self.assertIn("contactNoteText", js_response.text)
        self.assertIn("searchMessagesForContact", js_response.text)
        self.assertIn("Searching Messages for", js_response.text)
        self.assertIn('aria-label="Search local Messages for contact"', js_response.text)
        self.assertIn("contacts/management", js_response.text)
        self.assertIn("findConversationsForContact", js_response.text)
        self.assertIn("contactRelatedThreadMetaText", js_response.text)
        self.assertIn("contactThreadMessageContextText", js_response.text)
        self.assertIn("contactMessageContextsForConversation", js_response.text)
        self.assertIn("contactPrimaryMessageContext", js_response.text)
        self.assertIn("renderContactRelatedThreads", js_response.text)
        self.assertIn("message_context", js_response.text)
        self.assertIn("Opened ${conversationDisplayName(conversation)} at matching message", js_response.text)
        self.assertIn("labelsForConversation(conversation).slice(0, 2)", js_response.text)
        self.assertIn('note ? `note: ${trim(note, 72)}` : ""', js_response.text)
        self.assertIn("appendHighlightedText(meta, metaText, terms)", js_response.text)
        self.assertIn("appendHighlightedText(message, messageText, terms)", js_response.text)
        self.assertIn("contact-thread-title", js_response.text)
        self.assertIn("contact-thread-meta", js_response.text)
        self.assertIn("contact-thread-message", js_response.text)
        self.assertIn("renderContactInspector", js_response.text)
        self.assertIn("setActiveContact", js_response.text)
        self.assertIn("handleContactSearchKeydown", js_response.text)
        self.assertIn("moveActiveContactSearchResult", js_response.text)
        self.assertIn("openActiveContactSearchResult", js_response.text)
        self.assertIn("activeContactSearchResult", js_response.text)
        self.assertIn("clearActiveContact", js_response.text)
        self.assertIn("activeContact", js_response.text)
        self.assertIn("loadContactInspectorMessages", js_response.text)
        self.assertIn("renderContactInspectorMessages", js_response.text)
        self.assertIn("copyContactRecentMessage", js_response.text)
        self.assertIn("copyLoadedContactRecentMessages", js_response.text)
        self.assertIn('data-action="copy-recent"', js_response.text)
        self.assertIn("searchMessagesForContactRecentMessage", js_response.text)
        self.assertIn("filterConversationsForContactRecentMessage", js_response.text)
        self.assertIn('data-action="find-contact"', js_response.text)
        self.assertIn('data-action="threads"', js_response.text)
        self.assertIn("No recent messages to copy", js_response.text)
        self.assertIn("Recent message copied", js_response.text)
        self.assertIn("activeContactMessageNoteEditorId", js_response.text)
        self.assertIn("editContactRecentMessageNote", js_response.text)
        self.assertIn("saveContactRecentMessageNote", js_response.text)
        self.assertIn("Recent message note saved", js_response.text)
        self.assertIn("Recent message note cleared", js_response.text)
        self.assertIn("activeContactMessagesLimit", js_response.text)
        self.assertIn("activeContactMessagesLimitStep", js_response.text)
        self.assertIn("activeContactMessagesLimitMax", js_response.text)
        self.assertIn("resetActiveContactMessages", js_response.text)
        self.assertIn("Show more", js_response.text)
        self.assertIn("Compact", js_response.text)
        self.assertIn("toggleContactRecentMessageRead", js_response.text)
        self.assertIn("Recent message marked read", js_response.text)
        self.assertIn("Recent message marked unread", js_response.text)
        self.assertIn("activeContactMessagesBulkBusy", js_response.text)
        self.assertIn("contactRecentManageableMessages", js_response.text)
        self.assertIn("bulkUpdateContactRecentMessages", js_response.text)
        self.assertIn("starLoadedContactRecentMessages", js_response.text)
        self.assertIn("markLoadedContactRecentMessagesRead", js_response.text)
        self.assertIn("markLoadedContactRecentMessagesUnread", js_response.text)
        self.assertIn("Recent messages already starred", js_response.text)
        self.assertIn("Recent messages already read", js_response.text)
        self.assertIn("Recent messages already unread", js_response.text)
        self.assertIn("toggleContactRecentMessageStar", js_response.text)
        self.assertIn("Recent message starred", js_response.text)
        self.assertIn("Recent message unstarred", js_response.text)
        self.assertIn("contactMessageSearchQuery", js_response.text)
        self.assertIn("normalizedPhone.length >= 7", js_response.text)
        self.assertIn("phoneDigits.length >= 7", js_response.text)
        self.assertIn("Loading recent messages", js_response.text)
        self.assertIn("No recent local messages", js_response.text)
        self.assertIn("Show contact detail", js_response.text)
        self.assertIn("openContactConversation", js_response.text)
        self.assertIn("addDraftRecipient", js_response.text)
        self.assertIn("addContactToDraft", js_response.text)
        self.assertIn("startContactDraft", js_response.text)
        self.assertIn("Started new chat draft", js_response.text)
        self.assertNotIn("No matching synced conversation", js_response.text)
        self.assertIn("renderDraftRecipientChips", js_response.text)
        self.assertIn("draftRecipientContact", js_response.text)
        self.assertIn("draftRecipientDisplay", js_response.text)
        self.assertIn("scheduleDraftRecipientSuggestions", js_response.text)
        self.assertIn("loadDraftRecipientSuggestions", js_response.text)
        self.assertIn("renderDraftRecipientSuggestions", js_response.text)
        self.assertIn("draftRecipientEnabledSuggestions", js_response.text)
        self.assertIn("moveDraftRecipientActiveSuggestion", js_response.text)
        self.assertIn("selectActiveDraftRecipientSuggestion", js_response.text)
        self.assertIn("handleDraftRecipientsKeydown", js_response.text)
        self.assertIn("activeDraftRecipientSuggestion", js_response.text)
        self.assertIn("addDraftRecipientFromSuggestion", js_response.text)
        self.assertIn("/penguin-connect/contacts?", js_response.text)
        self.assertIn("draftRecipientCanCreateContact", js_response.text)
        self.assertIn("createDraftRecipientContactRecord", js_response.text)
        self.assertIn("createDraftRecipientContact", js_response.text)
        self.assertIn("Use a phone or email to create contact", js_response.text)
        self.assertIn(
            'contactButton.addEventListener("click", () => createDraftRecipientContact(recipient, contactButton));',
            js_response.text,
        )
        self.assertNotIn("prefillContactFromDraftRecipient", js_response.text)
        self.assertIn("refreshDraftRecipientChips", js_response.text)
        self.assertIn("Create contact from recipient", js_response.text)
        self.assertIn("known-recipient", js_response.text)
        self.assertIn("removeDraftRecipient", js_response.text)
        self.assertIn("loadRecipientLists", js_response.text)
        self.assertIn("saveRecipientList", js_response.text)
        self.assertIn("useRecipientList", js_response.text)
        self.assertIn("addRecipientListToDraft", js_response.text)
        self.assertIn('data-action="add-list"', js_response.text)
        self.assertIn("renderMessageSearchResults", js_response.text)
        self.assertIn("activeMessageSearchResultIndex", js_response.text)
        self.assertIn("moveActiveMessageSearchResult", js_response.text)
        self.assertIn("openActiveMessageSearchResult", js_response.text)
        self.assertIn("handleGlobalMessageSearchKeydown", js_response.text)
        self.assertIn("activeMessageSearchResult", js_response.text)
        self.assertIn("replyToMessageSearchResult", js_response.text)
        self.assertIn("useMessageAsNewChatDraft", js_response.text)
        self.assertIn("messageDraftText", js_response.text)
        self.assertIn("Message moved to new chat draft", js_response.text)
        self.assertIn('data-action="draft"', js_response.text)
        self.assertIn('data-action="follow-up"', js_response.text)
        self.assertIn('data-action="label"', js_response.text)
        self.assertIn("configureMessageFollowUpSelect", js_response.text)
        self.assertIn("applyMessageFollowUpPresetToConversation", js_response.text)
        self.assertIn("Set thread follow-up from this message", js_response.text)
        self.assertIn("configureMessageLabelSelect", js_response.text)
        self.assertIn("applyMessageLabelPresetToConversation", js_response.text)
        self.assertIn("Add a thread label from this message", js_response.text)
        self.assertIn("Tagged #${labels[0]}", js_response.text)
        self.assertIn("New draft", js_response.text)
        self.assertIn('data-action="add-contact"', js_response.text)
        self.assertIn("addMessageContactHandleToDraft", js_response.text)
        self.assertIn("addMessageSearchResultContactToDraft", js_response.text)
        self.assertIn("messageSearchContactHandles", js_response.text)
        self.assertIn("messageSearchParticipantHandlesForResult", js_response.text)
        self.assertIn("messageSearchParticipantHandles", js_response.text)
        self.assertIn("messageSearchCreatableContactItemsFromResults", js_response.text)
        self.assertIn("messageSearchCreatableContactItems", js_response.text)
        self.assertIn("messageSearchCreatableParticipantContactItems", js_response.text)
        self.assertIn("createMessageSearchContacts", js_response.text)
        self.assertIn("createMessageSearchContactItems", js_response.text)
        self.assertIn("createMessageSearchParticipantContacts", js_response.text)
        self.assertIn("Creating ${items.length} ${label} contact", js_response.text)
        self.assertIn("Search sender contacts already saved", js_response.text)
        self.assertIn("Search participant contacts already saved", js_response.text)
        self.assertIn("createSearchSendersButton.addEventListener", js_response.text)
        self.assertIn("createSearchParticipantsButton.addEventListener", js_response.text)
        self.assertIn("addMessageSearchContactsToDraft", js_response.text)
        self.assertIn("No sender handles in loaded results", js_response.text)
        self.assertIn("All search senders already in new chat", js_response.text)
        self.assertIn("search senders", js_response.text)
        self.assertIn("addSearchSendersButton.addEventListener", js_response.text)
        self.assertIn("addMessageSearchParticipantsToDraft", js_response.text)
        self.assertIn("No participant handles in loaded results", js_response.text)
        self.assertIn("All search participants already in new chat", js_response.text)
        self.assertIn("addSearchParticipantsButton.addEventListener", js_response.text)
        self.assertIn("messageSearchRecipientListName", js_response.text)
        self.assertIn("saveMessageSearchContactsAsRecipientList", js_response.text)
        self.assertIn("Saving search senders", js_response.text)
        self.assertIn("saveSearchSendersButton.addEventListener", js_response.text)
        self.assertIn("messageSearchParticipantListName", js_response.text)
        self.assertIn("saveMessageSearchParticipantsAsRecipientList", js_response.text)
        self.assertIn("Saving search participants", js_response.text)
        self.assertIn("saveSearchParticipantsButton.addEventListener", js_response.text)
        self.assertIn("addLoadedMessageContactToDraft", js_response.text)
        self.assertIn("Added sender to new chat", js_response.text)
        self.assertIn("Sender already in new chat", js_response.text)
        self.assertIn("toggleMessageSearchResultStar", js_response.text)
        self.assertIn("Search result starred", js_response.text)
        self.assertIn("toggleMessageSearchResultRead", js_response.text)
        self.assertIn('data-action="read-state"', js_response.text)
        self.assertIn("Search result marked read", js_response.text)
        self.assertIn("Search result marked unread", js_response.text)
        self.assertIn("messageSearchBulkBusy", js_response.text)
        self.assertIn("messageSearchManageableResults", js_response.text)
        self.assertIn("bulkUpdateMessageSearchResults", js_response.text)
        self.assertIn("starLoadedMessageSearchResults", js_response.text)
        self.assertIn("markLoadedMessageSearchResultsRead", js_response.text)
        self.assertIn("markLoadedMessageSearchResultsUnread", js_response.text)
        self.assertIn("Loaded search results already starred", js_response.text)
        self.assertIn("Loaded search results already read", js_response.text)
        self.assertIn("Loaded search results already unread", js_response.text)
        self.assertIn("starSearchLoadedButton.addEventListener", js_response.text)
        self.assertIn("markSearchReadButton.addEventListener", js_response.text)
        self.assertIn("markSearchUnreadButton.addEventListener", js_response.text)
        self.assertIn("messageSearchNoteEditorId", js_response.text)
        self.assertIn("messageSearchResultKey", js_response.text)
        self.assertIn("removeMessageSearchResultIfFiltered", js_response.text)
        self.assertIn("editMessageSearchResultNote", js_response.text)
        self.assertIn("saveMessageSearchResultNote", js_response.text)
        self.assertIn("Search result note saved", js_response.text)
        self.assertIn("participantValuesForConversation", js_response.text)
        self.assertIn("messageSearchContactHandle", js_response.text)
        self.assertIn("messageSearchContactDisplayName", js_response.text)
        self.assertIn("messageContactHandle", js_response.text)
        self.assertIn("messageContactDisplayName", js_response.text)
        self.assertIn("messageContactFromHandle", js_response.text)
        self.assertIn("lookupContactForMessageHandle", js_response.text)
        self.assertIn("searchMessagesForMessageContact", js_response.text)
        self.assertIn("searchMessagesForSearchResultContact", js_response.text)
        self.assertIn("searchMessagesForLoadedMessageContact", js_response.text)
        self.assertIn("useMessageContactHandle", js_response.text)
        self.assertIn("useMessageSearchResultContact", js_response.text)
        self.assertIn("useLoadedMessageContact", js_response.text)
        self.assertIn("Contact detail opened", js_response.text)
        self.assertIn("Contact lookup failed; form prefilled", js_response.text)
        self.assertIn(
            'contactButton.addEventListener("click", () => useMessageSearchResultContact(result));',
            js_response.text,
        )
        self.assertIn(
            'findContactButton.addEventListener("click", () => searchMessagesForSearchResultContact(result));',
            js_response.text,
        )
        self.assertIn(
            'addContactButton.addEventListener("click", () => addMessageSearchResultContactToDraft(result));',
            js_response.text,
        )
        self.assertIn(
            'contactButton.addEventListener("click", () => useLoadedMessageContact(message));',
            js_response.text,
        )
        self.assertIn(
            'findContactButton.addEventListener("click", () => searchMessagesForLoadedMessageContact(message));',
            js_response.text,
        )
        self.assertIn(
            'threadsButton.addEventListener("click", () => filterConversationsForLoadedMessageContact(message));',
            js_response.text,
        )
        self.assertIn(
            'addContactButton.addEventListener("click", () => addLoadedMessageContactToDraft(message));',
            js_response.text,
        )
        self.assertNotIn(
            'contactButton.addEventListener("click", () => fillContactFormFromMessageSearchResult(result));',
            js_response.text,
        )
        self.assertNotIn(
            'contactButton.addEventListener("click", () => fillContactFormFromMessage(message));',
            js_response.text,
        )
        self.assertIn("fillContactNameFromDisplay", js_response.text)
        self.assertIn("contactNamePartsFromDisplay", js_response.text)
        self.assertIn("Prefilled from message", js_response.text)
        self.assertIn("No contact handle on message", js_response.text)
        self.assertIn("openMessageSearchResultInMessages", js_response.text)
        self.assertIn('data-action="messages"', js_response.text)
        self.assertIn(
            'threadsButton.addEventListener("click", () => filterConversationsForSearchResultContact(result));',
            js_response.text,
        )
        self.assertIn("refreshConversationsForSearchResult", js_response.text)
        self.assertIn("Loading imported thread", js_response.text)
        self.assertIn("loadConversations({ autoSelect: false })", js_response.text)
        self.assertIn("ensureFocusedLoadedMessage", js_response.text)
        self.assertIn("loadedMessageByProviderId", js_response.text)
        self.assertIn("Loading older messages to find ${label}", js_response.text)
        self.assertIn("Opened at ${label}", js_response.text)
        self.assertIn("Opened thread; ${label} is older than the loaded window", js_response.text)
        self.assertIn("await ensureFocusedLoadedMessage", js_response.text)
        self.assertIn(
            "async function loadConversations({ autoSelect = true, preserveManagementEditing = false, announceActivity = false } = {})",
            js_response.text,
        )
        self.assertIn("renderMessageSearchFilters", js_response.text)
        self.assertIn("date_from", js_response.text)
        self.assertIn("date_to", js_response.text)
        self.assertIn("Type 2+ chars or choose dates", js_response.text)
        self.assertIn("messageSearchViews", js_response.text)
        self.assertIn("messageSearchViewsStorageKey", js_response.text)
        self.assertIn("savedMessageSearchViews", js_response.text)
        self.assertIn("renderMessageSavedViews", js_response.text)
        self.assertIn("saveMessageSearchView", js_response.text)
        self.assertIn("useMessageSearchView", js_response.text)
        self.assertIn("deleteMessageSearchView", js_response.text)
        self.assertIn("Saved message search", js_response.text)
        self.assertIn('{ key: "recent", label: "Recent" }', js_response.text)
        self.assertIn('{ key: "starred", label: "Starred" }', js_response.text)
        self.assertIn('{ key: "noted", label: "Noted" }', js_response.text)
        self.assertIn("renderMessageSearchMoreControls", js_response.text)
        self.assertIn("loadMoreMessageSearchResults", js_response.text)
        self.assertIn("messageSearchLimitMax", js_response.text)
        self.assertIn("renderMessageViewFilters", js_response.text)
        self.assertIn("renderMessageHistoryControls", js_response.text)
        self.assertIn("loadOlderMessages", js_response.text)
        self.assertIn("messageLimitMax", js_response.text)
        self.assertIn("Loading up to", js_response.text)
        self.assertIn("messageMatchesView", js_response.text)
        self.assertIn("activeLoadedMessageIndex", js_response.text)
        self.assertIn("moveActiveLoadedMessage", js_response.text)
        self.assertIn("focusActiveLoadedMessage", js_response.text)
        self.assertIn("focusNextUnreadLoadedMessage", js_response.text)
        self.assertIn("focusLatestLoadedMessage", js_response.text)
        self.assertIn("focusLoadedMessageAtIndex", js_response.text)
        self.assertIn("Selected unread message", js_response.text)
        self.assertIn("Selected latest loaded message", js_response.text)
        self.assertIn("handleMessageFilterKeydown", js_response.text)
        self.assertIn("activeLoadedMessageFilterResult", js_response.text)
        self.assertIn("focusedLoadedMessage", js_response.text)
        self.assertIn("replyToFocusedLoadedMessage", js_response.text)
        self.assertIn("copyFocusedLoadedMessage", js_response.text)
        self.assertIn("draftFocusedLoadedMessage", js_response.text)
        self.assertIn("toggleFocusedLoadedMessageStar", js_response.text)
        self.assertIn("toggleFocusedLoadedMessageRead", js_response.text)
        self.assertIn("editFocusedLoadedMessageNote", js_response.text)
        self.assertIn("Reply target set from focused message", js_response.text)
        self.assertIn("Focused message copied", js_response.text)
        self.assertIn("shortcutDefinitions", js_response.text)
        self.assertIn("shortcutHelpOnlyDefinitions", js_response.text)
        self.assertIn("shortcutSequencePrefix", js_response.text)
        self.assertIn("eventShortcutKey", js_response.text)
        self.assertIn("runKeyboardShortcut", js_response.text)
        self.assertIn("renderShortcutHelp", js_response.text)
        self.assertIn("toggleShortcutHelp", js_response.text)
        self.assertIn('key: "shift+?"', js_response.text)
        self.assertIn('key: "mod+enter"', js_response.text)
        self.assertIn('key: "mod+shift+enter"', js_response.text)
        self.assertIn('key: "mod+k"', js_response.text)
        self.assertIn('key: "mod+["', js_response.text)
        self.assertIn('key: "mod+]"', js_response.text)
        self.assertIn('key: "mod+left"', js_response.text)
        self.assertIn('key: "mod+right"', js_response.text)
        self.assertIn('key: "n+s"', js_response.text)
        self.assertIn('key: "mod+shift+u"', js_response.text)
        self.assertIn('key: "mod+shift+,"', js_response.text)
        self.assertIn('key: "mod+shift+."', js_response.text)
        self.assertIn('key: "mod+shift+p"', js_response.text)
        self.assertIn('key: "g+i"', js_response.text)
        self.assertIn('key: "g+a"', js_response.text)
        self.assertIn('key: "g+r"', js_response.text)
        self.assertIn('key: "g+h"', js_response.text)
        self.assertIn('key: "g+u"', js_response.text)
        self.assertIn('key: "g+d"', js_response.text)
        self.assertIn('key: "g+l"', js_response.text)
        self.assertIn('key: "g+s"', js_response.text)
        self.assertIn('key: "g+e"', js_response.text)
        self.assertIn('key: "g+o"', js_response.text)
        self.assertIn('key: "g+#"', js_response.text)
        self.assertIn('key: "g+!"', js_response.text)
        self.assertIn('key: "g+t"', js_response.text)
        self.assertIn('key: "g+/"', js_response.text)
        self.assertIn('key: "alt+0"', js_response.text)
        self.assertIn('key: "alt+9"', js_response.text)
        self.assertIn('key: "mod+shift+e"', js_response.text)
        self.assertIn('key: "d+a"', js_response.text)
        self.assertIn('key: "d+o"', js_response.text)
        self.assertIn('key: "d+c"', js_response.text)
        self.assertIn('key: "mod+/"', js_response.text)
        self.assertIn('key: "mod+alt+n"', js_response.text)
        self.assertIn('key: "mod+,"', js_response.text)
        self.assertIn('key: "mod+a"', js_response.text)
        self.assertIn('key: "mod+shift+a"', js_response.text)
        self.assertIn('key: "space"', js_response.text)
        self.assertIn('key: "shift+space"', js_response.text)
        self.assertIn('key: "["', js_response.text)
        self.assertIn('key: "]"', js_response.text)
        self.assertIn('key: "!"', js_response.text)
        self.assertIn('key: "mod+u"', js_response.text)
        self.assertIn('key: "t"', js_response.text)
        self.assertIn('key: "a"', js_response.text)
        self.assertIn('key: "ctrl+/"', js_response.text)
        self.assertIn('key: "shift+o"', js_response.text)
        self.assertIn('key: "4"', js_response.text)
        self.assertIn('key: "9"', js_response.text)
        self.assertIn('key: "c+a"', js_response.text)
        self.assertIn('key: "c+c"', js_response.text)
        self.assertIn('key: "mod+j"', js_response.text)
        self.assertIn("selectVisibleFromFocusedShortcut", js_response.text)
        self.assertIn("selectAndNavigateVisibleConversation", js_response.text)
        self.assertIn("archiveSelectedConversationAndMove", js_response.text)
        self.assertIn("setSelectedConversationsManagementShortcut", js_response.text)
        self.assertIn("toggleSelectedConversationArchive", js_response.text)
        self.assertIn("toggleSelectedConversationMute", js_response.text)
        self.assertIn("toggleSelectedConversationRead", js_response.text)
        self.assertIn("focusConversationFollowUpShortcut", js_response.text)
        self.assertIn("focusConversationLabelsShortcut", js_response.text)
        self.assertIn("setContactSourceShortcut", js_response.text)
        self.assertIn("setMessageViewShortcut", js_response.text)
        self.assertIn("setMediaViewShortcut", js_response.text)
        self.assertIn("toggleFollowUpTriageShortcut", js_response.text)
        self.assertIn("focusCodexShortcut", js_response.text)
        self.assertIn("focusThreadMediaShortcut", js_response.text)
        self.assertIn("focusContactCreateShortcut", js_response.text)
        self.assertIn("focusThreadManagementShortcut", js_response.text)
        self.assertIn("triggerAttachmentPickerShortcut", js_response.text)
        self.assertIn("discardActiveDraftShortcut", js_response.text)
        self.assertIn("copyCurrentLinkShortcut", js_response.text)
        self.assertIn("toggleArchivedViewShortcut", js_response.text)
        self.assertIn("copySelectedConversationShortcut", js_response.text)
        self.assertIn("draftFocusedMessageShortcut", js_response.text)
        self.assertIn("editFocusedMessageNoteShortcut", js_response.text)
        self.assertIn("messagesLoading", js_response.text)
        self.assertIn("isAudioAttachment", js_response.text)
        self.assertIn("renderAudioAttachment", js_response.text)
        self.assertIn("isImageAttachment", js_response.text)
        self.assertIn("renderImageAttachment", js_response.text)
        self.assertIn("attachmentKindLabel", js_response.text)
        self.assertIn("renderCompactAttachmentChips", js_response.text)
        self.assertIn("compact-attachment-kind", js_response.text)
        self.assertIn("threadMediaItems", js_response.text)
        self.assertIn("mediaMatchesView", js_response.text)
        self.assertIn("copyMediaLink", js_response.text)
        self.assertIn("Media link copied", js_response.text)
        self.assertIn("attachMediaToReply", js_response.text)
        self.assertIn("attachMediaToDraft", js_response.text)
        self.assertIn("replyMediaAttachments", js_response.text)
        self.assertIn("draftMediaAttachments", js_response.text)
        self.assertIn("Media attached to reply", js_response.text)
        self.assertIn("Media attached to new chat", js_response.text)
        self.assertIn("Media attachment removed", js_response.text)
        self.assertIn('attach.dataset.action = "attach-reply"', js_response.text)
        self.assertIn('attachDraft.dataset.action = "attach-draft"', js_response.text)
        self.assertIn("renderThreadMedia", js_response.text)
        self.assertIn("focusMediaMessage", js_response.text)
        self.assertIn("renderConversationFilters", js_response.text)
        self.assertIn('needsReply: "Needs reply"', js_response.text)
        self.assertIn("conversationNeedsReply", js_response.text)
        self.assertIn("last_message_direction", js_response.text)
        self.assertIn('drafts: "Drafts"', js_response.text)
        self.assertIn('unlabeled: "Unlabeled"', js_response.text)
        self.assertIn('muted: "Muted"', js_response.text)
        self.assertIn("renderLabelFilters", js_response.text)
        self.assertIn("conversationMatchesLabel", js_response.text)
        self.assertIn("conversationHasLabels", js_response.text)
        self.assertIn("followUpLabel", js_response.text)
        self.assertIn("followUpPresetValue", js_response.text)
        self.assertIn("applyFollowUpPreset", js_response.text)
        self.assertIn("messageConversationId", js_response.text)
        self.assertIn("messageFollowUpSelectLabel", js_response.text)
        self.assertIn("messageLabelPresets", js_response.text)
        self.assertIn("messageLabelSelectLabel", js_response.text)
        self.assertIn("Thread label limit reached", js_response.text)
        self.assertIn("threadFollowUpPresets", js_response.text)
        self.assertIn("Follow-up set", js_response.text)
        self.assertIn("Follow-up cleared", js_response.text)
        self.assertIn("follow_up_at", js_response.text)
        self.assertIn("bulkMarkSelectedRead", js_response.text)
        self.assertIn("shouldBulkMarkUnread", js_response.text)
        self.assertIn("selectUnknownButton", js_response.text)
        self.assertIn("Selected ${added} unknown thread", js_response.text)
        self.assertIn("selectedConversationParticipantHandles", js_response.text)
        self.assertIn("selectedConversationPeopleContactCandidates", js_response.text)
        self.assertIn("selectedConversationCreatablePeople", js_response.text)
        self.assertIn("selectedConversationFavoritePeople", js_response.text)
        self.assertIn("selectedConversationStarrablePeople", js_response.text)
        self.assertIn("selectedConversationUnstarrablePeople", js_response.text)
        self.assertIn("selectedConversationPeopleListName", js_response.text)
        self.assertIn("addSelectedConversationPeopleToDraft", js_response.text)
        self.assertIn("copySelectedConversationPeople", js_response.text)
        self.assertIn("createSelectedConversationPeopleContacts", js_response.text)
        self.assertIn("saveSelectedConversationPeopleAsRecipientList", js_response.text)
        self.assertIn("starSelectedConversationPeople", js_response.text)
        self.assertIn("Selected people already added", js_response.text)
        self.assertIn("Copied ${participants.length} selected", js_response.text)
        self.assertIn("Selected people already starred", js_response.text)
        self.assertIn("Starring ${items.length} selected", js_response.text)
        self.assertIn("unstarSelectedConversationPeople", js_response.text)
        self.assertIn("No selected people are starred", js_response.text)
        self.assertIn("Unstarring ${items.length} selected", js_response.text)
        self.assertIn("Selected people contacts already saved", js_response.text)
        self.assertIn("Creating ${items.length} selected contact", js_response.text)
        self.assertIn("No selected contacts created", js_response.text)
        self.assertIn("Saving selected people", js_response.text)
        self.assertIn("bulkAddPeopleButton.addEventListener", js_response.text)
        self.assertIn("bulkCopyPeopleButton.addEventListener", js_response.text)
        self.assertIn("bulkSavePeopleButton.addEventListener", js_response.text)
        self.assertIn("bulkStarPeopleButton.addEventListener", js_response.text)
        self.assertIn("bulkUnstarPeopleButton.addEventListener", js_response.text)
        self.assertIn("bulkCreatePeopleButton.addEventListener", js_response.text)
        self.assertIn("bulkClearDrafts", js_response.text)
        self.assertIn("conversationHasDraft", js_response.text)
        self.assertIn("bulkPinSelected", js_response.text)
        self.assertIn("shouldBulkPin", js_response.text)
        self.assertIn("bulkMuteSelected", js_response.text)
        self.assertIn("shouldBulkMute", js_response.text)
        self.assertIn("bulkArchiveSelected", js_response.text)
        self.assertIn("shouldBulkArchive", js_response.text)
        self.assertIn("bulkApplyLabels", js_response.text)
        self.assertIn("mergeConversationLabels", js_response.text)
        self.assertIn("conversationPreviewText", js_response.text)
        self.assertIn("selectedVisibleConversationIndex", js_response.text)
        self.assertIn("updateConversationSearchActiveDescendant", js_response.text)
        self.assertIn("scrollSelectedConversationIntoView", js_response.text)
        self.assertIn("navigateVisibleConversation", js_response.text)
        self.assertIn("openVisibleConversationSearchResult", js_response.text)
        self.assertIn("handleConversationSearchKeydown", js_response.text)
        self.assertIn("activeConversationSearchResult", js_response.text)
        self.assertIn("isShortcutEditableTarget", js_response.text)
        self.assertIn("handleGlobalShortcuts", js_response.text)
        self.assertIn("isSubmitShortcut", js_response.text)
        self.assertIn("handleSubmitShortcut", js_response.text)
        self.assertIn("isAttachmentShortcut", js_response.text)
        self.assertIn("handleComposerTextShortcut", js_response.text)
        self.assertIn("isMacShortcutPlatform", js_response.text)
        self.assertIn("el.composer.addEventListener(\"keydown\"", js_response.text)
        self.assertIn("el.draftMessage.addEventListener(\"keydown\"", js_response.text)
        self.assertIn("el.draftRecipients.addEventListener(\"keydown\"", js_response.text)
        self.assertIn('key: "j"', js_response.text)
        self.assertIn('key: "down"', js_response.text)
        self.assertIn('key: "k"', js_response.text)
        self.assertIn('key: "up"', js_response.text)
        self.assertIn('key: "shift+j"', js_response.text)
        self.assertIn('key: "shift+k"', js_response.text)
        self.assertIn("document.addEventListener(\"keydown\", handleGlobalShortcuts)", js_response.text)
        self.assertIn("setReplyContext", js_response.text)
        self.assertIn("replyContextQuoteText", js_response.text)
        self.assertIn("outgoingReplyText", js_response.text)
        self.assertIn("message: outboundMessage", js_response.text)
        self.assertIn("toggleMessageStar", js_response.text)
        self.assertIn("toggleMessageRead", js_response.text)
        self.assertIn("Message marked unread", js_response.text)
        self.assertIn("isStarredMessage", js_response.text)
        self.assertIn("saveMessageNote", js_response.text)
        self.assertIn("hasMessageNote", js_response.text)
        self.assertIn("messages/management", js_response.text)
        self.assertIn("messageCopyText", js_response.text)
        self.assertIn("renderManagementFields", js_response.text)
        self.assertIn("stageDraft", js_response.text)
        self.assertIn("draftAttachments", js_response.text)
        self.assertIn("newChatDraftStorageKey", js_response.text)
        self.assertIn("saveNewChatDraft", js_response.text)
        self.assertIn("restoreNewChatDraft", js_response.text)
        self.assertIn("clearSavedNewChatDraft", js_response.text)
        self.assertIn("const initialConversation = visibleConversationRows()[0]", js_response.text)
        self.assertIn("Local draft restored", js_response.text)
        self.assertIn("buildMessagesDraftText", js_response.text)
        self.assertIn("draftRecipientLine", js_response.text)
        self.assertIn("renderDraftPreview", js_response.text)
        self.assertIn("sendDraftIfExisting", js_response.text)
        self.assertIn("draftUnknownRecipientHandles", js_response.text)
        self.assertIn("createUnknownDraftRecipients", js_response.text)
        self.assertIn("draftCreatedContactFromHandle", js_response.text)
        self.assertIn("No unknown phone/email recipients", js_response.text)
        self.assertIn("scheduleDraftThreadResolve", js_response.text)
        self.assertIn("/penguin-connect/messages/resolve-draft", js_response.text)
        self.assertIn("Existing thread:", js_response.text)
        self.assertIn("Open thread", js_response.text)
        self.assertIn("/penguin-connect/messages/send-draft", js_response.text)
        self.assertIn("Sent to", js_response.text)
        self.assertIn("no exact thread", js_response.text)
        self.assertIn("multiple matching threads", js_response.text)
        self.assertIn("No recipients · message ready", js_response.text)
        self.assertIn("Message:\\n\\n", js_response.text)
        self.assertIn("filesAsBrowserAttachments", js_response.text)
        self.assertIn("draftExistingAttachmentPaths", js_response.text)
        self.assertIn("attachment_paths: attachmentPaths", js_response.text)
        self.assertIn("existing local media", js_response.text)
        self.assertIn("renderAllEmojiButtons", js_response.text)
        self.assertIn("renderAllVoiceMemoControls", js_response.text)
        self.assertIn("copyDraftRecipients", js_response.text)
        self.assertIn("copyDraftBody", js_response.text)
        self.assertIn("copyDraftPreview", js_response.text)
        self.assertIn("openAddressedDraft", js_response.text)
        self.assertIn("open_addressed", js_response.text)
        self.assertIn("open_addressed: el.draftOpenToggle.checked", js_response.text)
        self.assertIn("addressed chat opened", js_response.text)
        self.assertIn("open_attachments", js_response.text)
        self.assertIn("attachment_folder", js_response.text)
        self.assertIn("createContact", js_response.text)
        self.assertIn("setReadState", js_response.text)
        self.assertIn("openSelectedConversationInMessages", js_response.text)
        self.assertIn("/open-messages", js_response.text)
        self.assertIn("Opened Messages to", js_response.text)
        self.assertIn("setConversationManagement", js_response.text)
        self.assertIn("is_muted", js_response.text)
        self.assertIn("saveConversationManagement", js_response.text)
        self.assertIn("scheduleDraftSave", js_response.text)
        self.assertIn("saveLocalDraft", js_response.text)
        self.assertIn("toggleConnection", js_response.text)
        self.assertIn("attachmentUrl", js_response.text)
        self.assertIn("handleAttachmentPaste", js_response.text)
        self.assertIn("clipboardAttachmentFiles", js_response.text)
        self.assertIn("normalizeAttachmentFile", js_response.text)
        self.assertIn("attachmentPreviewUrls", js_response.text)
        self.assertIn("attachmentFileKind", js_response.text)
        self.assertIn("attachmentFileLabel", js_response.text)
        self.assertIn("renderAttachmentFilePreview", js_response.text)
        self.assertIn("Voice memo", js_response.text)
        self.assertIn("startVoiceMemoRecording", js_response.text)
        self.assertIn("stopVoiceMemoRecording", js_response.text)
        self.assertIn("Voice memo attached", js_response.text)
        self.assertIn("Stop voice memo before sending", js_response.text)
        self.assertIn('button.setAttribute("aria-label", `Insert emoji ${emoji}`)', js_response.text)
        self.assertIn('textarea.dispatchEvent(new Event("input", { bubbles: true }))', js_response.text)
        self.assertIn(".attachment-chip.with-preview", css_response.text)
        self.assertIn(".attachment-chip audio", css_response.text)
        self.assertIn(".attachment-chip img", css_response.text)
        self.assertIn(".draft-preview", css_response.text)
        self.assertIn(".draft-preview-actions", css_response.text)

    def test_messages_endpoint_uses_header_display_name_for_own_gmail_messages(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, 'gmail', 'gmail-self', 'email_to_imessage', ?, ?, ?, ?, ?, 1, ?)""",
                (
                    "amc_test",
                    "owner@gmail.com",
                    "Owner <owner@gmail.com>",
                    "Re: Taylor",
                    "From me in Gmail",
                    "2026-03-11T10:00:00+00:00",
                    "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["messages"][0]["provider_message_id"], "gmail-self")
        self.assertEqual(body["messages"][0]["sender_name"], "Owner")

    def test_messages_endpoint_uses_me_for_manual_messages_without_a_display_name(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)), TestClient(app_module.app) as client:
            send_response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"sender_email": "owner@gmail.com", "message": "Hello from Messages"},
            )
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(send_response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["messages"][0]["direction"], "manual_to_imessage")
        self.assertEqual(body["messages"][0]["sender_name"], "Me")

    def test_messages_endpoint_uses_me_for_historical_self_authored_imessage_rows(self):
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction, sender_email, sender_name, subject,
                    body_text, message_timestamp, is_read, metadata)
                   VALUES (?, 'imessage', 'imsg-self', 'imessage_to_email', ?, ?, ?, ?, ?, 1, ?)""",
                (
                    "amc_test",
                    "owner+am-test@gmail.com",
                    "Taylor",
                    "iMessage · Taylor",
                    "Sent from Messages",
                    "2026-03-11T11:00:00+00:00",
                    '{"is_from_me": true}',
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["messages"][0]["provider_message_id"], "imsg-self")
        self.assertEqual(body["messages"][0]["sender_name"], "Me")

    def test_messages_endpoint_caches_local_imessage_rows_without_gmail_account(self):
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM penguin_connect_messages")
            conn.execute("DELETE FROM penguin_connect_accounts")
            conn.execute(
                """UPDATE penguin_connect_conversations
                   SET gmail_email = ?, alias_email = NULL
                   WHERE conversation_id = ?""",
                (penguin_connect.LOCAL_MESSAGES_ACCOUNT_EMAIL, "amc_test"),
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch(
            "penguin_connect.fetch_imessage_messages",
            return_value=[
                {
                    "native_message_id": "http-local-1",
                    "timestamp": "2026-03-11T12:00:00+00:00",
                    "text": "HTTP local message",
                    "is_from_me": False,
                    "handle": "+15551234567",
                    "attachments": [{"filename": "/tmp/http-local.m4a", "mime_type": "audio/mp4"}],
                    "chat_id": "chat-123",
                },
                {
                    "native_message_id": "http-local-2",
                    "timestamp": "2026-03-11T12:01:00+00:00",
                    "text": "HTTP self message",
                    "is_from_me": True,
                    "handle": "",
                    "attachments": [],
                    "chat_id": "chat-123",
                },
            ],
        ), mock.patch("penguin_connect._get_imessage_unread_count", return_value=1), TestClient(
            app_module.app
        ) as client:
            response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 50})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["found"])
        self.assertEqual(
            [message["provider_message_id"] for message in body["messages"]],
            ["imessage:http-local-2", "imessage:http-local-1"],
        )
        self.assertEqual(body["messages"][0]["sender_name"], "Me")
        self.assertEqual(body["messages"][0]["direction"], "imessage_local")
        self.assertEqual(body["messages"][1]["attachments"][0]["mime_type"], "audio/mp4")
        self.assertFalse(body["messages"][1]["is_read"])

        verify_conn = self._get_connection()
        try:
            stored = verify_conn.execute(
                """SELECT direction, gmail_message_id, metadata
                   FROM penguin_connect_messages
                   WHERE provider_message_id = ?""",
                ("imessage:http-local-1",),
            ).fetchone()
        finally:
            verify_conn.close()
        self.assertEqual(stored["direction"], "imessage_local")
        self.assertIsNone(stored["gmail_message_id"])
        self.assertTrue(json.loads(stored["metadata"])["local_cache_only"])

    def test_alias_endpoint_returns_not_found_for_unknown_conversation(self):
        with TestClient(app_module.app) as client:
            response = client.get("/penguin-connect/conversations/amc-missing/alias")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "conversation_not_found")

    def test_send_endpoint_does_not_require_connected_gmail_account(self):
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM penguin_connect_accounts")
            conn.commit()
        finally:
            conn.close()

        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send, TestClient(
            app_module.app
        ) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"message": "direct local send"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_send.assert_called_once_with("chat-123", "direct local send", attachment_paths=None)

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT sender_email, sender_name, body_text, direction
                   FROM penguin_connect_messages
                   WHERE direction = 'manual_to_imessage'"""
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertIsNone(row["sender_email"])
        self.assertEqual(row["sender_name"], "Me")
        self.assertEqual(row["body_text"], "direct local send")
        self.assertEqual(row["direction"], "manual_to_imessage")

    def test_send_endpoint_persists_optional_sender_metadata(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send, TestClient(
            app_module.app
        ) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"sender_email": "owner@gmail.com", "message": "Hello from Messages"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_send.assert_called_once_with("chat-123", "Hello from Messages", attachment_paths=None)

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT sender_email, sender_name, body_text, direction
                   FROM penguin_connect_messages
                   WHERE direction = 'manual_to_imessage'"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["sender_email"], "owner@gmail.com")
        self.assertEqual(row["sender_name"], "Me")
        self.assertEqual(row["body_text"], "Hello from Messages")
        self.assertEqual(row["direction"], "manual_to_imessage")

    def test_send_endpoint_defaults_to_local_conversation_sender(self):
        with mock.patch("penguin_connect.send_imessage", return_value=(True, None)) as mock_send, TestClient(
            app_module.app
        ) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={"message": "Hello from local console"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_send.assert_called_once_with("chat-123", "Hello from local console", attachment_paths=None)

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT sender_email, sender_name, body_text, direction
                   FROM penguin_connect_messages
                   WHERE direction = 'manual_to_imessage'"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertIsNone(row["sender_email"])
        self.assertEqual(row["sender_name"], "Me")
        self.assertEqual(row["body_text"], "Hello from local console")
        self.assertEqual(row["direction"], "manual_to_imessage")

    def test_send_endpoint_forwards_attachment_paths(self):
        with mock.patch("app.refresh_contacts_now", return_value={"success": True}), mock.patch(
            "penguin_connect.send_imessage", return_value=(True, None)
        ) as mock_send, TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={
                    "sender_email": "owner@gmail.com",
                    "message": "",
                    "attachment_paths": ["/tmp/voice-memo.m4a"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_send.assert_called_once_with("chat-123", "", attachment_paths=["/tmp/voice-memo.m4a"])

        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT body_text, metadata
                   FROM penguin_connect_messages
                   WHERE direction = 'manual_to_imessage'"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["body_text"], "")
        self.assertEqual(json.loads(row["metadata"])["manual_attachment_count"], 1)

    def test_send_endpoint_stages_browser_attachments(self):
        captured_paths = []

        def fake_send(_chat_id, _message, attachment_paths=None):
            captured_paths.extend(attachment_paths or [])
            self.assertEqual(Path(captured_paths[0]).read_bytes(), b"fake-image")
            return True, None

        encoded = base64.b64encode(b"fake-image").decode("ascii")
        with mock.patch("app.refresh_contacts_now", return_value={"success": True}), mock.patch(
            "penguin_connect.send_imessage",
            side_effect=fake_send,
        ), TestClient(app_module.app) as client:
            response = client.post(
                "/penguin-connect/conversations/amc_test/send",
                json={
                    "sender_email": "owner@gmail.com",
                    "message": "see attached",
                    "attachments": [
                        {
                            "filename": "photo.png",
                            "mime_type": "image/png",
                            "size": len(b"fake-image"),
                            "data_base64": encoded,
                        }
                    ],
                },
            )
            body = response.json()
            messages_response = client.get("/penguin-connect/conversations/amc_test/messages", params={"limit": 1})
            attachment_response = client.get(
                "/penguin-connect/conversations/amc_test/attachments/0",
                params={"provider_message_id": body["provider_message_id"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["attachment_count"], 1)
        self.assertEqual(len(captured_paths), 1)
        attachment_path = Path(captured_paths[0])
        self.assertTrue(attachment_path.exists())
        self.assertTrue(str(attachment_path).startswith(str(self.db_path.parent / "sent-message-attachments")))

        self.assertEqual(messages_response.status_code, 200)
        message = messages_response.json()["messages"][0]
        self.assertEqual(message["provider_message_id"], body["provider_message_id"])
        self.assertEqual(message["attachments"][0]["transfer_name"], "photo.png")
        self.assertEqual(message["attachments"][0]["mime_type"], "image/png")
        self.assertEqual(message["attachments"][0]["filename"], str(attachment_path))

        self.assertEqual(attachment_response.status_code, 200)
        self.assertEqual(attachment_response.content, b"fake-image")
        self.assertEqual(attachment_response.headers["content-type"], "image/png")


class BrowserSafeAttachmentTests(unittest.TestCase):
    def test_is_heic_attachment_detects_by_mime_or_suffix(self):
        self.assertTrue(app_module._is_heic_attachment("IMG_0001.HEIC", ""))
        self.assertTrue(app_module._is_heic_attachment("photo.heif", ""))
        self.assertTrue(app_module._is_heic_attachment("noext", "image/heic"))
        self.assertFalse(app_module._is_heic_attachment("photo.jpg", "image/jpeg"))
        self.assertFalse(app_module._is_heic_attachment("photo.png", ""))

    def test_non_heic_attachment_passes_through_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "photo.png"
            src.write_bytes(b"not-a-real-image")
            path, name, media_type = app_module._browser_safe_image_attachment(
                src, "photo.png", "image/png"
            )
            self.assertEqual(path, src)
            self.assertEqual(name, "photo.png")
            self.assertEqual(media_type, "image/png")

    def test_heic_falls_back_to_original_when_sips_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "IMG_0001.HEIC"
            src.write_bytes(b"fake-heic-bytes")
            with mock.patch.object(app_module.shutil, "which", return_value=None):
                path, name, media_type = app_module._browser_safe_image_attachment(
                    src, "IMG_0001.HEIC", "image/heic"
                )
            self.assertEqual(path, src)
            self.assertEqual(name, "IMG_0001.HEIC")
            self.assertEqual(media_type, "image/heic")

    @unittest.skipUnless(shutil.which("sips"), "requires macOS sips")
    def test_heic_is_transcoded_to_jpeg_with_sips(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Build a real HEIC from a generated PNG using sips so the test does
            # not ship binary fixtures.
            png = Path(tmp) / "src.png"
            # 1x1 white PNG.
            png.write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                )
            )
            heic = Path(tmp) / "IMG_0001.HEIC"
            convert = subprocess.run(
                ["sips", "-s", "format", "heic", str(png), "--out", str(heic)],
                capture_output=True,
                text=True,
            )
            if convert.returncode != 0 or not heic.exists():
                self.skipTest("sips could not produce a HEIC fixture")
            path, name, media_type = app_module._browser_safe_image_attachment(
                heic, "IMG_0001.HEIC", "image/heic"
            )
            self.assertNotEqual(path, heic)
            self.assertEqual(name, "IMG_0001.jpg")
            self.assertEqual(media_type, "image/jpeg")
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(path.read_bytes()[6:10], b"JFIF")


if __name__ == "__main__":
    unittest.main()
