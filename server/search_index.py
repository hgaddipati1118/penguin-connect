"""Local hybrid search for PenguinConnect messages and Spotlight-indexed files.

Lexical search uses SQLite FTS5. Semantic search is optional and uses
sqlite-vec plus an Ollama embedding model running on the same Mac.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterable

import httpx

from db import DB_PATH

SEARCH_DB_PATH = Path(
    os.environ.get(
        "PENGUIN_CONNECT_SEARCH_DB_PATH",
        str(DB_PATH.with_name("search.db")),
    )
).expanduser()
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_EMBEDDING_DIMENSIONS = 768
DEFAULT_EMBEDDING_INPUT_CHARS = 1_500
TEXT_FILE_EXTENSIONS = {
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".mdx",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _embedding_model() -> str:
    return (os.environ.get("PENGUIN_CONNECT_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL).strip()


def _ollama_url() -> str:
    return (os.environ.get("PENGUIN_CONNECT_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")


def _search_connection() -> sqlite3.Connection:
    SEARCH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SEARCH_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass
        return False


def _create_lexical_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_documents (
            id INTEGER PRIMARY KEY,
            document_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS search_documents_fts USING fts5(
            document_key UNINDEXED,
            title,
            body,
            tokenize = 'unicode61 remove_diacritics 2'
        );

        CREATE TABLE IF NOT EXISTS search_index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _reset_index(conn: sqlite3.Connection, *, semantic: bool) -> bool:
    vector_available = _load_sqlite_vec(conn)
    conn.executescript(
        """
        DROP TABLE IF EXISTS search_documents_fts;
        DROP TABLE IF EXISTS search_documents;
        DROP TABLE IF EXISTS search_index_meta;
        DROP TABLE IF EXISTS search_document_vectors;
        """
    )
    _create_lexical_schema(conn)
    vector_ready = semantic and vector_available
    if vector_ready:
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE search_document_vectors USING vec0(
                embedding float[{DEFAULT_EMBEDDING_DIMENSIONS}]
            )
            """
        )
    return vector_ready


def _clean_text(value: Any, limit: int = 20_000) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return text[:limit]


def _content_hash(*values: str) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8", errors="replace")).hexdigest()


def _message_documents(limit: int) -> Iterable[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                m.id,
                m.provider_message_id,
                m.body_text,
                m.sender_name,
                m.sender_email,
                m.message_timestamp,
                m.metadata,
                c.conversation_id,
                c.display_name,
                c.source_provider,
                c.source_service_name
            FROM penguin_connect_messages m
            JOIN penguin_connect_conversations c
              ON c.conversation_id = m.conversation_id
            WHERE TRIM(COALESCE(m.body_text, '')) <> ''
            ORDER BY m.message_timestamp DESC, m.id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100_000)),),
        ).fetchall()
        return [
            {
                "document_key": f"message:{row['id']}",
                "kind": "message",
                "source_id": row["provider_message_id"] or str(row["id"]),
                "title": row["display_name"] or "Conversation",
                "body": " | ".join(
                    part
                    for part in (
                        row["sender_name"] or row["sender_email"] or "",
                        row["body_text"] or "",
                    )
                    if part
                ),
                "path": "",
                "provider": row["source_provider"] or row["source_service_name"] or "",
                "timestamp": row["message_timestamp"] or "",
                "metadata": {
                    "conversation_id": row["conversation_id"],
                    "provider_message_id": row["provider_message_id"],
                },
            }
            for row in rows
        ]
    finally:
        conn.close()


def default_file_roots() -> list[Path]:
    configured = (os.environ.get("PENGUIN_CONNECT_FILE_SEARCH_ROOTS") or "").strip()
    candidates = (
        [Path(value).expanduser() for value in configured.split(os.pathsep) if value.strip()]
        if configured
        else [Path.home() / name for name in ("Desktop", "Documents", "Downloads")]
    )
    return [path.resolve() for path in candidates if path.exists() and path.is_dir()]


def _allowed_file_root(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    return resolved == home or home in resolved.parents


def _spotlight_paths(query: str, roots: list[Path], limit: int) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not _allowed_file_root(root):
            continue
        command = ["mdfind", "-onlyin", str(root), query]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        for raw in result.stdout.splitlines():
            candidate = Path(raw).expanduser()
            key = str(candidate)
            if key in seen or not candidate.is_file():
                continue
            seen.add(key)
            found.append(candidate)
            if len(found) >= limit:
                return found
    return found


def spotlight_file_search(
    query: str,
    *,
    roots: list[str] | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    clean_query = _clean_text(query, 300)
    if not clean_query:
        return []
    resolved_roots = [Path(value).expanduser().resolve() for value in (roots or []) if value]
    if not resolved_roots:
        resolved_roots = default_file_roots()
    paths = _spotlight_paths(clean_query, resolved_roots, max(1, min(limit, 100)))
    results: list[dict[str, Any]] = []
    for path in paths:
        metadata = _spotlight_metadata(path, include_text=False)
        results.append(
            {
                "path": str(path),
                "name": path.name,
                "kind": metadata.get("kind") or "",
                "content_type": metadata.get("content_type") or "",
                "modified_at": metadata.get("modified_at") or "",
                "size": metadata.get("size") or 0,
            }
        )
    return results


def _spotlight_metadata(path: Path, *, include_text: bool) -> dict[str, Any]:
    keys = [
        "kMDItemKind",
        "kMDItemContentType",
        "kMDItemFSContentChangeDate",
        "kMDItemFSSize",
    ]
    if include_text:
        keys.append("kMDItemTextContent")
    command = ["mdls"]
    for key in keys:
        command.extend(["-name", key])
    command.extend(["-plist", "-", str(path)])
    try:
        result = subprocess.run(command, capture_output=True, timeout=10, check=False)
        if result.returncode != 0:
            return {}
        import plistlib

        payload = plistlib.loads(result.stdout)
    except Exception:
        return {}
    return {
        "kind": payload.get("kMDItemKind") or "",
        "content_type": payload.get("kMDItemContentType") or "",
        "modified_at": str(payload.get("kMDItemFSContentChangeDate") or ""),
        "size": int(payload.get("kMDItemFSSize") or 0),
        "text": _clean_text(payload.get("kMDItemTextContent") or "", 40_000),
    }


def _extract_file_text(path: Path) -> str:
    """Extract a bounded amount of local text without uploading the file."""
    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_FILE_EXTENSIONS and path.stat().st_size <= 5_000_000:
            return _clean_text(path.read_text(encoding="utf-8", errors="replace"), 40_000)
    except OSError:
        return ""

    command: list[str] | None = None
    if suffix == ".pdf" and shutil.which("pdftotext"):
        command = ["pdftotext", "-f", "1", "-l", "50", str(path), "-"]
    elif suffix in {".doc", ".docx", ".html", ".htm", ".rtf"} and shutil.which("textutil"):
        command = ["textutil", "-convert", "txt", "-stdout", str(path)]
    if not command:
        return ""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return _clean_text(result.stdout if result.returncode == 0 else "", 40_000)


def _file_documents(roots: list[str] | None, limit: int) -> Iterable[dict[str, Any]]:
    resolved_roots = [Path(value).expanduser().resolve() for value in (roots or []) if value]
    if not resolved_roots:
        resolved_roots = default_file_roots()
    query = "kMDItemContentTypeTree == 'public.content'"
    paths = _spotlight_paths(query, resolved_roots, max(0, min(limit, 10_000)))
    documents = []
    for path in paths:
        metadata = _spotlight_metadata(path, include_text=True)
        text = metadata.get("text") or _extract_file_text(path)
        if not text:
            text = path.stem.replace("_", " ").replace("-", " ")
        documents.append(
            {
                "document_key": f"file:{path}",
                "kind": "file",
                "source_id": str(path),
                "title": path.name,
                "body": text,
                "path": str(path),
                "provider": "spotlight",
                "timestamp": metadata.get("modified_at") or "",
                "metadata": {
                    "content_type": metadata.get("content_type") or "",
                    "kind": metadata.get("kind") or "",
                    "size": metadata.get("size") or 0,
                },
            }
        )
    return documents


def _ollama_embeddings(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = httpx.post(
        f"{_ollama_url()}/api/embed",
        json={"model": _embedding_model(), "input": texts},
        timeout=120,
    )
    response.raise_for_status()
    embeddings = response.json().get("embeddings") or []
    if len(embeddings) != len(texts):
        raise RuntimeError("ollama_embedding_count_mismatch")
    if any(len(vector) != DEFAULT_EMBEDDING_DIMENSIONS for vector in embeddings):
        raise RuntimeError("embedding_dimensions_mismatch")
    return embeddings


def _embedding_document_text(title: str, body: str) -> str:
    return _clean_text(
        f"search_document: {title}\n{body}",
        DEFAULT_EMBEDDING_INPUT_CHARS,
    )


def rebuild_search_index(
    *,
    include_messages: bool = True,
    include_files: bool = True,
    semantic: bool = False,
    roots: list[str] | None = None,
    message_limit: int = 25_000,
    file_limit: int = 1_000,
) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    if include_messages:
        documents.extend(_message_documents(message_limit))
    if include_files:
        documents.extend(_file_documents(roots, file_limit))

    conn = _search_connection()
    try:
        vector_ready = _reset_index(conn, semantic=semantic)
        inserted = 0
        embedded = 0
        for document in documents:
            title = _clean_text(document.get("title"), 1_000)
            body = _clean_text(document.get("body"))
            metadata = json.dumps(document.get("metadata") or {}, sort_keys=True)
            content_hash = _content_hash(title, body, metadata)
            cursor = conn.execute(
                """
                INSERT INTO search_documents
                    (document_key, kind, source_id, title, body, path, provider, timestamp, content_hash, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["document_key"],
                    document["kind"],
                    document["source_id"],
                    title,
                    body,
                    document.get("path") or "",
                    document.get("provider") or "",
                    document.get("timestamp") or "",
                    content_hash,
                    metadata,
                ),
            )
            rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO search_documents_fts(rowid, document_key, title, body) VALUES (?, ?, ?, ?)",
                (rowid, document["document_key"], title, body),
            )
            inserted += 1

        if vector_ready and documents:
            batch_size = 16
            rows = conn.execute(
                "SELECT id, title, body FROM search_documents ORDER BY id"
            ).fetchall()
            for start in range(0, len(rows), batch_size):
                batch = rows[start : start + batch_size]
                texts = [
                    _embedding_document_text(row["title"], row["body"])
                    for row in batch
                ]
                vectors = _ollama_embeddings(texts)
                conn.executemany(
                    "INSERT INTO search_document_vectors(rowid, embedding) VALUES (?, ?)",
                    [(row["id"], json.dumps(vector)) for row, vector in zip(batch, vectors)],
                )
                embedded += len(batch)

        conn.execute(
            "INSERT OR REPLACE INTO search_index_meta(key, value) VALUES ('embedding_model', ?)",
            (_embedding_model() if vector_ready else "",),
        )
        conn.commit()
        return {
            "success": True,
            "documents_indexed": inserted,
            "vectors_indexed": embedded,
            "semantic_enabled": vector_ready,
            "search_db_path": str(SEARCH_DB_PATH),
        }
    finally:
        conn.close()


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w@.+-]+", query.lower(), flags=re.UNICODE)
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:16])


def hybrid_search(
    query: str,
    *,
    limit: int = 20,
    kinds: list[str] | None = None,
) -> dict[str, Any]:
    clean_query = _clean_text(query, 500)
    if not clean_query or not SEARCH_DB_PATH.exists():
        return {"query": clean_query, "count": 0, "results": [], "semantic_used": False}
    safe_limit = max(1, min(limit, 100))
    conn = _search_connection()
    try:
        _create_lexical_schema(conn)
        kind_values = [value for value in (kinds or []) if value in {"message", "file"}]
        kind_clause = ""
        kind_params: list[Any] = []
        if kind_values:
            placeholders = ",".join("?" for _ in kind_values)
            kind_clause = f"AND d.kind IN ({placeholders})"
            kind_params = kind_values

        lexical_rows = conn.execute(
            f"""
            SELECT d.*, bm25(search_documents_fts, 2.5, 1.0) AS lexical_score
            FROM search_documents_fts
            JOIN search_documents d ON d.id = search_documents_fts.rowid
            WHERE search_documents_fts MATCH ?
              {kind_clause}
            ORDER BY lexical_score
            LIMIT ?
            """,
            (_fts_query(clean_query), *kind_params, safe_limit * 3),
        ).fetchall()

        ranked: dict[int, dict[str, Any]] = {}
        for rank, row in enumerate(lexical_rows, start=1):
            ranked[row["id"]] = {"row": row, "score": 1.0 / (60 + rank), "lexical_rank": rank}

        semantic_used = False
        if _load_sqlite_vec(conn):
            vector_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'search_document_vectors'"
            ).fetchone()
            if vector_table:
                try:
                    vector = _ollama_embeddings([f"search_query: {clean_query}"])[0]
                    vector_rows = conn.execute(
                        """
                        SELECT rowid, distance
                        FROM search_document_vectors
                        WHERE embedding MATCH ?
                        ORDER BY distance
                        LIMIT ?
                        """,
                        (json.dumps(vector), safe_limit * 3),
                    ).fetchall()
                    semantic_used = True
                    for rank, vector_row in enumerate(vector_rows, start=1):
                        document_id = int(vector_row["rowid"])
                        if document_id not in ranked:
                            row = conn.execute(
                                f"SELECT * FROM search_documents d WHERE d.id = ? {kind_clause}",
                                (document_id, *kind_params),
                            ).fetchone()
                            if row is None:
                                continue
                            ranked[document_id] = {"row": row, "score": 0.0}
                        ranked[document_id]["score"] += 1.0 / (60 + rank)
                        ranked[document_id]["semantic_rank"] = rank
                        ranked[document_id]["distance"] = float(vector_row["distance"])
                except Exception:
                    semantic_used = False

        ordered = sorted(ranked.values(), key=lambda item: item["score"], reverse=True)[:safe_limit]
        results = []
        for item in ordered:
            row = item["row"]
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except Exception:
                metadata = {}
            results.append(
                {
                    "kind": row["kind"],
                    "source_id": row["source_id"],
                    "title": row["title"],
                    "snippet": _clean_text(row["body"], 700),
                    "path": row["path"],
                    "provider": row["provider"],
                    "timestamp": row["timestamp"],
                    "score": item["score"],
                    "lexical_rank": item.get("lexical_rank"),
                    "semantic_rank": item.get("semantic_rank"),
                    "metadata": metadata,
                }
            )
        return {
            "query": clean_query,
            "count": len(results),
            "results": results,
            "semantic_used": semantic_used,
        }
    except sqlite3.OperationalError:
        return {"query": clean_query, "count": 0, "results": [], "semantic_used": False}
    finally:
        conn.close()
