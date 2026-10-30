"""Transactional FTS5 sources and a durable, revision-fenced sqlite-vec index.

Only visible conversation text is indexed: never jobs, prompts, tool payloads,
attachments or message metadata. Triggers cover every writer, including cascades.
"""

from __future__ import annotations

import sqlite3

import sqlite_vec

MODEL = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
DIMENSIONS = 384
# Bump when changing model weights, dimensions, preprocessing or chunking.
INDEX_VERSION = f"{MODEL}:{MODEL_REVISION}:fastembed-0.8:chunks-1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    indexed_revision INTEGER NOT NULL DEFAULT 0,
    retry_after REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS search_documents_chat ON search_documents(chat_id);
CREATE INDEX IF NOT EXISTS search_documents_message ON search_documents(message_id);
CREATE INDEX IF NOT EXISTS search_documents_pending ON search_documents(retry_after, id)
    WHERE indexed_revision != revision;
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    title, content, content='search_documents', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2', prefix='2 3 4'
);
CREATE TABLE IF NOT EXISTS search_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES search_documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS search_chunks_document ON search_chunks(document_id);
CREATE TABLE IF NOT EXISTS search_index_version (
    version TEXT NOT NULL, sources_ready INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS search_document_insert AFTER INSERT ON search_documents BEGIN
    INSERT INTO search_fts(rowid, title, content) VALUES (NEW.id, NEW.title, NEW.content);
END;
CREATE TRIGGER IF NOT EXISTS search_document_delete AFTER DELETE ON search_documents BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, content)
        VALUES ('delete', OLD.id, OLD.title, OLD.content);
END;
CREATE TRIGGER IF NOT EXISTS search_document_update AFTER UPDATE OF title, content ON search_documents BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, content)
        VALUES ('delete', OLD.id, OLD.title, OLD.content);
    INSERT INTO search_fts(rowid, title, content) VALUES (NEW.id, NEW.title, NEW.content);
    DELETE FROM search_chunks WHERE document_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS search_chat_insert AFTER INSERT ON chats BEGIN
    INSERT INTO search_documents(source, chat_id, title) VALUES ('c:' || NEW.id, NEW.id, NEW.title);
END;
CREATE TRIGGER IF NOT EXISTS search_chat_update AFTER UPDATE OF title ON chats
WHEN NEW.title != OLD.title BEGIN
    UPDATE search_documents SET title = NEW.title, revision = revision + 1, retry_after = 0
        WHERE source = 'c:' || NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS search_message_insert AFTER INSERT ON messages
WHEN NEW.role IN ('user', 'assistant') BEGIN
    INSERT INTO search_documents(source, chat_id, message_id, content)
        VALUES ('m:' || NEW.id, NEW.chat_id, NEW.id, NEW.content);
END;
CREATE TRIGGER IF NOT EXISTS search_message_update AFTER UPDATE OF content, role, chat_id ON messages BEGIN
    DELETE FROM search_documents WHERE source = 'm:' || OLD.id AND NEW.role NOT IN ('user', 'assistant');
    INSERT INTO search_documents(source, chat_id, message_id, content)
        SELECT 'm:' || NEW.id, NEW.chat_id, NEW.id, NEW.content WHERE NEW.role IN ('user', 'assistant')
        ON CONFLICT(source) DO UPDATE SET content = excluded.content, chat_id = excluded.chat_id,
            revision = revision + 1, retry_after = 0;
END;
"""


def load_extension(conn: sqlite3.Connection) -> None:
    # Must run before ANY schema migration: SQLite validates existing triggers
    # during ALTER TABLE, including triggers that reference vec0 virtual tables.
    # Extension loading is enabled only around this trusted, packaged extension.
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def install(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    version = conn.execute("SELECT version, sources_ready FROM search_index_version").fetchone()
    if not version or version[0] != INDEX_VERSION:
        conn.executescript("""
            DROP TRIGGER IF EXISTS search_chunk_delete;
            DELETE FROM search_chunks;
            DROP TABLE IF EXISTS search_vectors;
            CREATE VIRTUAL TABLE search_vectors USING vec0(embedding float[384] distance_metric=cosine);
            UPDATE search_documents SET indexed_revision = 0, retry_after = 0;
            DELETE FROM search_index_version;
        """)
        conn.execute(
            "INSERT INTO search_index_version(version, sources_ready) VALUES (?, ?)",
            (INDEX_VERSION, int(bool(version and version[1]))),
        )
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS search_chunk_delete AFTER DELETE ON search_chunks BEGIN
            DELETE FROM search_vectors WHERE rowid = OLD.id;
        END;
    """)
    if not version or not version[1]:
        # One transactional backfill, not a scan of all message bodies on every
        # restart. A crash before commit leaves sources_ready=0 for retry.
        conn.execute(
            "INSERT OR IGNORE INTO search_documents(source, chat_id, title) "
            "SELECT 'c:' || id, id, title FROM chats"
        )
        conn.execute(
            "INSERT OR IGNORE INTO search_documents(source, chat_id, message_id, content) "
            "SELECT 'm:' || id, chat_id, id, content FROM messages WHERE role IN ('user', 'assistant')"
        )
        conn.execute("UPDATE search_index_version SET sources_ready = 1")


def chunks(text: str):
    """Overlapping short passages, including long code/URLs without whitespace."""
    start = 0
    while start < len(text):
        end = min(start + 1000, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + 650, end)
            if boundary != -1:
                end = boundary
        passage = text[start:end].strip()
        if passage:
            yield passage
        if end == len(text):
            break
        start = end - 150
