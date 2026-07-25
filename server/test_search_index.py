"""Tests for PenguinConnect's optional local hybrid search index."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import search_index


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


if __name__ == "__main__":
    unittest.main()
