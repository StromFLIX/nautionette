"""Local hybrid conversation search. CPU/model work never runs on the event loop."""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import threading
import time
from typing import TYPE_CHECKING

import sqlite_vec

from .config import settings
from .db import db
from .search_index import DIMENSIONS, chunks
from .search_model import load_encoder

if TYPE_CHECKING:
    from .db import Database

log = logging.getLogger("nautionette.search")


class ChatSearch:
    def __init__(self, database: Database) -> None:
        self.db = database
        self.encoder = None
        self.state = "loading" if settings.search_semantic_enabled else "disabled"
        self._encoder_lock = threading.Lock()
        self._query_vector = functools.lru_cache(maxsize=128)(self._embed_query)

    def load(self) -> None:
        encoder = load_encoder(settings.search_model_cache, local_files_only=settings.search_model_local_only)
        # Warm up before publishing the encoder to request threads.
        list(encoder.query_embed("conversation search"))
        self._query_vector.cache_clear()
        self.encoder = encoder
        self.state = "ready"

    def index_one(self) -> bool:
        document = self.db.one(
            "SELECT * FROM search_documents WHERE indexed_revision != revision "
            "AND retry_after <= ? ORDER BY retry_after, id LIMIT 1",
            (time.time(),),
        )
        if not document:
            return False
        try:
            passages = list(chunks(document["title"] or document["content"]))
            vectors = []
            for offset in range(0, len(passages), 8):
                # Release between batches so an interactive query can interleave
                # even when one saved message contains a very long answer.
                with self._encoder_lock:
                    vectors.extend(self.encoder.embed(passages[offset : offset + 8], batch_size=8))
            if len(vectors) != len(passages) or any(len(vector) != DIMENSIONS for vector in vectors):
                raise ValueError("embedding shape does not match the search index")
            with self.db.transaction() as conn:
                current = conn.execute(
                    "SELECT revision FROM search_documents WHERE id = ?", (document["id"],)
                ).fetchone()
                # Edits/deletes while inference runs must never resurrect stale text.
                if current is None or current["revision"] != document["revision"]:
                    return True
                conn.execute("DELETE FROM search_chunks WHERE document_id = ?", (document["id"],))
                for passage, vector in zip(passages, vectors, strict=True):
                    row = conn.execute(
                        "INSERT INTO search_chunks(document_id, content) VALUES (?, ?)",
                        (document["id"], passage),
                    )
                    conn.execute(
                        "INSERT INTO search_vectors(rowid, embedding) VALUES (?, ?)",
                        (row.lastrowid, sqlite_vec.serialize_float32(vector)),
                    )
                conn.execute(
                    "UPDATE search_documents SET indexed_revision = revision, retry_after = 0 WHERE id = ?",
                    (document["id"],),
                )
        except Exception:
            # Keep the durable job and let later documents progress. No message
            # content or model exception text (which may contain it) in the logs.
            self.db.execute(
                "UPDATE search_documents SET retry_after = ? WHERE id = ? AND revision = ?",
                (time.time() + 60, document["id"], document["revision"]),
            )
            log.error("Search embedding failed for document %s; retrying in 60s", document["id"])
        return True

    async def run(self) -> None:
        if not settings.search_semantic_enabled:
            return
        while True:
            try:
                if self.encoder is None:
                    await asyncio.to_thread(self.load)
                worked = await asyncio.to_thread(self.index_one)
                await asyncio.sleep(0.05 if worked else 2)
            except Exception:
                self.state = "unavailable"
                log.error("Semantic search unavailable; keyword search remains available. Retrying in 60s")
                await asyncio.sleep(60)

    def _embed_query(self, query: str) -> bytes:
        return sqlite_vec.serialize_float32(next(iter(self.encoder.query_embed(query))))

    def semantic(self, query: str) -> list[dict] | None:
        # Backfill must not queue interactive queries behind long inference.
        if self.encoder is None or not self._encoder_lock.acquire(timeout=0.15):
            return None
        try:
            vector = self._query_vector(query)
        finally:
            self._encoder_lock.release()
        return self.db.query(
            "SELECT d.id, d.chat_id, d.message_id, c.title, s.content AS excerpt, v.distance "
            "FROM search_vectors v JOIN search_chunks s ON s.id = v.rowid "
            "JOIN search_documents d ON d.id = s.document_id JOIN chats c ON c.id = d.chat_id "
            "WHERE v.embedding MATCH ? AND k = 200 AND d.indexed_revision = d.revision "
            # BGE similarities have a high baseline: avoid returning unrelated
            # conversations merely because KNN always has nearest neighbors.
            "AND v.distance < 0.4 ORDER BY v.distance",
            (vector,),
        )

    def keyword(self, query: str) -> list[dict]:
        # Never pass raw user text as FTS syntax. Prefix matching keeps the
        # palette useful while typing, including punctuation and code names.
        terms = re.findall(r"\w+", query, flags=re.UNICODE)[:32]
        if not terms:
            return []
        expression = " AND ".join(f'"{term}"*' for term in terms)
        return self.db.query(
            "WITH ranked AS MATERIALIZED ("
            "SELECT d.id, d.chat_id, d.message_id, c.title, "
            "snippet(search_fts, -1, '', '', '…', 40) AS excerpt, "
            "bm25(search_fts, 6.0, 1.0) AS relevance "
            "FROM search_fts JOIN search_documents d ON d.id = search_fts.rowid "
            "JOIN chats c ON c.id = d.chat_id WHERE search_fts MATCH ?), "
            "best AS (SELECT *, row_number() OVER (PARTITION BY chat_id ORDER BY relevance, id) AS n "
            "FROM ranked) SELECT * FROM best WHERE n = 1 ORDER BY relevance, id LIMIT 100",
            (expression,),
        )

    async def search(self, query: str, limit: int = 30) -> dict:
        keyword = await asyncio.to_thread(self.keyword, query)
        semantic = []
        state = self.state
        if self.encoder is not None and settings.search_semantic_enabled:
            try:
                rows = await asyncio.wait_for(asyncio.to_thread(self.semantic, query), timeout=0.8)
                if rows is None:
                    state = "busy"
                else:
                    semantic = rows
            except TimeoutError:
                state = "busy"
            except Exception:
                state = "unavailable"
                log.error("Semantic query failed; returning keyword matches")
        pending = await asyncio.to_thread(
            self.db.one, "SELECT COUNT(*) AS count FROM search_documents WHERE indexed_revision != revision"
        )
        if state == "ready" and pending["count"]:
            state = "indexing"
        # Fuse chat-level ranks, not raw chunk counts: long conversations should
        # not beat short ones simply because they have more passages.
        matches = {}
        for kind, rows in (("keyword", keyword), ("semantic", semantic)):
            seen = set()
            for row in rows:
                chat_id = row["chat_id"]
                if chat_id in seen:
                    continue
                seen.add(chat_id)
                rank = len(seen)
                if chat_id not in matches:
                    matches[chat_id] = {
                        "chat_id": chat_id,
                        "title": row["title"],
                        "message_id": row["message_id"],
                        "excerpt": row["excerpt"][:500],
                        "match": kind,
                        "score": 0.0,
                    }
                elif matches[chat_id]["match"] != kind:
                    matches[chat_id]["match"] = "both"
                matches[chat_id]["score"] += 1 / (60 + rank)
        results = sorted(matches.values(), key=lambda row: (-row["score"], row["chat_id"]))[:limit]
        return {"results": results, "semantic": {"state": state, "pending": pending["count"]}}


search = ChatSearch(db)
