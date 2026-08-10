"""Tests for PenguinConnect's optional local hybrid search index."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import search_index


def sqlite_vec_is_loadable() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        return search_index._load_sqlite_vec(conn)
    finally:
        conn.close()


class SearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_db = Path(self.temp_dir.name) / "cache.db"
        self.search_db = Path(self.temp_dir.name) / "search.db"
        conn = sqlite3.connect(self.cache_db)
        conn.executescript(
            """
            CREATE TABLE penguin_connect_conversations (
                conversation_id TEXT PRIMARY KEY,
                display_name TEXT,
                source_provider TEXT,
                source_service_name TEXT
            );
            CREATE TABLE penguin_connect_messages (
                id INTEGER PRIMARY KEY,
                conversation_id TEXT,
                provider_message_id TEXT,
                body_text TEXT,
                sender_name TEXT,
                sender_email TEXT,
                message_timestamp TEXT,
                metadata TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO penguin_connect_conversations VALUES (?, ?, ?, ?)",
            ("conversation-1", "Synthetic Contact", "whatsapp", "WhatsApp"),
        )
        conn.execute(
            """INSERT INTO penguin_connect_messages
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                1,
                "conversation-1",
                "message-1",
                "The launch plan is ready for review",
                "Synthetic Contact",
                "",
                "2026-07-25T12:00:00+00:00",
                "{}",
            ),
        )
        conn.commit()
        conn.close()
        self.path_patch = mock.patch.multiple(
            search_index,
            DB_PATH=self.cache_db,
            SEARCH_DB_PATH=self.search_db,
        )
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_rebuild_and_lexical_search_messages(self):
        result = search_index.rebuild_search_index(
            include_messages=True,
            include_files=False,
            semantic=False,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["documents_indexed"], 1)
        self.assertFalse(result["semantic_enabled"])

        search = search_index.hybrid_search("launch plan", limit=10)
        self.assertEqual(search["count"], 1)
        self.assertEqual(search["results"][0]["kind"], "message")
        self.assertEqual(
            search["results"][0]["metadata"]["conversation_id"],
            "conversation-1",
        )

    def test_spotlight_search_returns_metadata_without_file_contents(self):
        file_path = Path(self.temp_dir.name) / "brief.pdf"
        file_path.write_bytes(b"%PDF synthetic")
        with mock.patch.object(
            search_index,
            "_spotlight_paths",
            return_value=[file_path],
        ), mock.patch.object(
            search_index,
            "_spotlight_metadata",
            return_value={
                "kind": "PDF document",
                "content_type": "com.adobe.pdf",
                "modified_at": "2026-07-25",
                "size": 14,
                "text": "private body",
            },
        ):
            results = search_index.spotlight_file_search(
                "brief",
                roots=[self.temp_dir.name],
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "brief.pdf")
        self.assertNotIn("text", results[0])

    def test_plain_text_files_are_extracted_locally_for_indexing(self):
        file_path = Path(self.temp_dir.name) / "notes.md"
        file_path.write_text("Synthetic launch checklist", encoding="utf-8")

        self.assertEqual(
            search_index._extract_file_text(file_path),
            "Synthetic launch checklist",
        )

    def test_image_text_is_extracted_with_local_macos_vision_helper(self):
        file_path = Path(self.temp_dir.name) / "screenshot.png"
        file_path.write_bytes(b"synthetic image bytes")
        completed = mock.Mock(
            returncode=0,
            stdout="Project Cedar\nLaunch review Friday\n",
        )

        with mock.patch.object(
            search_index,
            "_vision_ocr_binary",
            return_value=Path("/tmp/penguin-vision-ocr"),
        ), mock.patch.object(
            search_index.subprocess,
            "run",
            return_value=completed,
        ) as run:
            extracted = search_index._extract_file_text(file_path)

        self.assertEqual(extracted, "Project Cedar\nLaunch review Friday")
        run.assert_called_once_with(
            ["/tmp/penguin-vision-ocr", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_attachment_summary_is_included_in_message_document(self):
        conn = sqlite3.connect(self.cache_db)
        conn.execute(
            """CREATE TABLE penguin_connect_attachment_intelligence (
                   conversation_id TEXT,
                   provider_message_id TEXT,
                   attachment_index INTEGER,
                   filename TEXT,
                   mime_type TEXT,
                   extracted_text TEXT,
                   summary TEXT,
                   status TEXT
               )"""
        )
        conn.execute(
            """INSERT INTO penguin_connect_attachment_intelligence
               VALUES (?, ?, 0, ?, ?, ?, ?, 'summarized')""",
            (
                "conversation-1",
                "message-1",
                "brief.pdf",
                "application/pdf",
                "Project Cedar launches Friday.",
                "A launch brief for Project Cedar.",
            ),
        )
        conn.commit()
        conn.close()

        document = list(search_index._message_documents(100))[0]

        self.assertIn("Project Cedar", document["body"])
        self.assertIn("Attachment summary", document["body"])

    def test_embedding_document_text_is_bounded(self):
        text = search_index._embedding_document_text(
            "Synthetic title",
            "x" * (search_index.DEFAULT_EMBEDDING_INPUT_CHARS * 2),
        )

        self.assertEqual(
            len(text),
            search_index.DEFAULT_EMBEDDING_INPUT_CHARS,
        )
        self.assertTrue(text.startswith("search_document: Synthetic title"))

    @unittest.skipUnless(sqlite_vec_is_loadable(), "sqlite-vec extension is unavailable")
    def test_semantic_index_can_be_rebuilt_when_vector_table_exists(self):
        fake_vector = [0.0] * search_index.DEFAULT_EMBEDDING_DIMENSIONS
        with mock.patch.object(
            search_index,
            "_ollama_embeddings",
            side_effect=lambda texts: [fake_vector for _ in texts],
        ):
            first = search_index.rebuild_search_index(
                include_messages=True,
                include_files=False,
                semantic=True,
            )
            second = search_index.rebuild_search_index(
                include_messages=True,
                include_files=False,
                semantic=True,
            )

        self.assertTrue(first["semantic_enabled"])
        self.assertEqual(first["vectors_indexed"], 1)
        self.assertTrue(second["semantic_enabled"])
        self.assertEqual(second["vectors_indexed"], 1)

    @unittest.skipUnless(sqlite_vec_is_loadable(), "sqlite-vec extension is unavailable")
    def test_message_refresh_commits_lexical_text_before_bounded_vectors(self):
        fake_vector = [0.0] * search_index.DEFAULT_EMBEDDING_DIMENSIONS
        with mock.patch.object(
            search_index,
            "_ollama_embeddings",
            side_effect=lambda texts: [fake_vector for _ in texts],
        ):
            search_index.rebuild_search_index(
                include_messages=True,
                include_files=False,
                semantic=True,
            )
            conn = sqlite3.connect(self.cache_db)
            conn.execute(
                """UPDATE penguin_connect_messages
                   SET body_text = 'Distinctive narwhal launch details'
                   WHERE id = 1"""
            )
            conn.commit()
            conn.close()

            lexical_only = search_index.refresh_message_search_index(
                vector_refresh_limit=0,
            )
            lexical_result = search_index.hybrid_search(
                "distinctive narwhal",
                limit=10,
            )
            vector_catchup = search_index.refresh_message_search_index(
                vector_refresh_limit=1,
            )

        self.assertEqual(lexical_only["messages_changed"], 1)
        self.assertEqual(lexical_only["vectors_refreshed"], 0)
        self.assertEqual(lexical_only["vectors_pending"], 1)
        self.assertEqual(lexical_result["count"], 1)
        self.assertEqual(vector_catchup["messages_changed"], 0)
        self.assertEqual(vector_catchup["vectors_refreshed"], 1)
        self.assertEqual(vector_catchup["vectors_pending"], 0)


if __name__ == "__main__":
    unittest.main()
