import json
import tempfile
import unittest
from pathlib import Path

import db


class AttachmentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_data_dir = db.DATA_DIR
        db.DATA_DIR = Path(self.tmpdir.name)
        db.DB_PATH = Path(self.tmpdir.name) / "cache.db"

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.DATA_DIR = self.old_data_dir
        self.tmpdir.cleanup()

    def _insert_conversation(self, conn):
        conn.execute(
            """INSERT INTO penguin_connect_conversations
               (gmail_email, source_provider, conversation_id, source_chat_id,
                display_name, status)
               VALUES ('local@penguinconnect.invalid', 'imessage', 'catalog-test',
                       'chat-catalog-test', 'Catalog Test', 'active')"""
        )

    def test_catalog_tracks_attachment_insert_update_and_delete(self):
        db.init_db()
        conn = db.get_connection()
        try:
            self._insert_conversation(conn)
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction,
                    body_text, message_timestamp, metadata)
                   VALUES ('catalog-test', 'imessage', 'message-1', 'imessage_local',
                           '', '2026-07-26T12:00:00+00:00', ?)""",
                (
                    json.dumps({
                        "attachments": [
                            {"transfer_name": "first.png", "mime_type": "image/png"},
                            {"transfer_name": "second.pdf", "mime_type": "application/pdf"},
                        ]
                    }),
                ),
            )
            inserted = conn.execute(
                """SELECT attachment_index, message_timestamp, attachment_json
                   FROM penguin_connect_attachments
                   ORDER BY attachment_index"""
            ).fetchall()

            conn.execute(
                """UPDATE penguin_connect_messages
                   SET message_timestamp = '2026-07-26T12:05:00+00:00',
                       metadata = ?
                   WHERE conversation_id = 'catalog-test'
                     AND provider_message_id = 'message-1'""",
                (
                    json.dumps({
                        "attachments": [
                            {"transfer_name": "replacement.mov", "mime_type": "video/quicktime"}
                        ]
                    }),
                ),
            )
            updated = conn.execute(
                """SELECT attachment_index, message_timestamp, attachment_json
                   FROM penguin_connect_attachments"""
            ).fetchall()

            conn.execute(
                """DELETE FROM penguin_connect_messages
                   WHERE conversation_id = 'catalog-test'
                     AND provider_message_id = 'message-1'"""
            )
            remaining = conn.execute(
                "SELECT COUNT(*) FROM penguin_connect_attachments"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual([row["attachment_index"] for row in inserted], [0, 1])
        self.assertEqual(
            [json.loads(row["attachment_json"])["transfer_name"] for row in inserted],
            ["first.png", "second.pdf"],
        )
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["attachment_index"], 0)
        self.assertEqual(updated[0]["message_timestamp"], "2026-07-26T12:05:00+00:00")
        self.assertEqual(
            json.loads(updated[0]["attachment_json"])["transfer_name"],
            "replacement.mov",
        )
        self.assertEqual(remaining, 0)

    def test_init_backfills_attachments_from_a_legacy_database(self):
        conn = db.get_connection()
        try:
            conn.executescript(db.SCHEMA)
            conn.execute("DROP TRIGGER IF EXISTS penguin_connect_messages_attachment_insert")
            conn.execute("DROP TRIGGER IF EXISTS penguin_connect_messages_attachment_update")
            conn.execute("DROP TRIGGER IF EXISTS penguin_connect_messages_attachment_delete")
            conn.execute("DROP TABLE IF EXISTS penguin_connect_attachments")
            self._insert_conversation(conn)
            conn.execute(
                """INSERT INTO penguin_connect_messages
                   (conversation_id, provider, provider_message_id, direction,
                    body_text, message_timestamp, metadata)
                   VALUES ('catalog-test', 'imessage', 'legacy-message', 'imessage_local',
                           '', '2026-07-26T11:00:00+00:00', ?)""",
                (
                    json.dumps({
                        "attachments": [
                            {"transfer_name": "legacy-image.jpg", "mime_type": "image/jpeg"}
                        ]
                    }),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        conn = db.get_connection()
        try:
            row = conn.execute(
                """SELECT message_id, attachment_index, attachment_json
                   FROM penguin_connect_attachments"""
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertGreater(row["message_id"], 0)
        self.assertEqual(row["attachment_index"], 0)
        self.assertEqual(
            json.loads(row["attachment_json"])["transfer_name"],
            "legacy-image.jpg",
        )


if __name__ == "__main__":
    unittest.main()
