"""Private composer state; never part of history, search, or agent context."""

import json
import sqlite3
from typing import Any

from fastapi import HTTPException

from . import chat_attachments
from .db import db


def read(chat_id: str) -> dict[str, Any]:
    with db.transaction() as conn:
        return _read(conn, chat_id)


def _read(conn: sqlite3.Connection, chat_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM chat_drafts WHERE chat_id = ?", (chat_id,)).fetchone()
    ids = json.loads(row["attachment_ids"]) if row else []
    attachments = []
    for attachment_id in ids:
        item = conn.execute(
            "SELECT id, name, mime_type, size FROM chat_images WHERE id = ? AND chat_id = ?",
            (attachment_id, chat_id),
        ).fetchone()
        if item:
            attachments.append(dict(item))
    return {
        "revision": row["revision"] if row else 0,
        "mutation_id": row["mutation_id"] if row else "",
        "text": row["content"] if row else "",
        "attachments": attachments,
    }


def save(chat_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    revision = payload.get("revision")
    mutation = payload.get("mutation_id")
    text = payload.get("text")
    if (
        type(revision) is not int
        or revision < 0
        or not isinstance(mutation, str)
        or not 1 <= len(mutation) <= 64
        or not isinstance(text, str)
        or len(text) > 1_000_000
    ):
        raise HTTPException(422, "Provide revision, mutation_id and text (at most 1,000,000 characters)")
    ids = chat_attachments.attachment_ids(payload.get("attachment_ids", []))
    with db.transaction() as conn:
        current = _read(conn, chat_id)
        # A lost response can safely be retried, including clears.
        if current["mutation_id"] == mutation:
            return current
        if current["revision"] != revision:
            raise HTTPException(409, "Draft changed on another device; your local draft has been kept")
        for attachment_id in ids:
            if not conn.execute(
                "SELECT 1 FROM chat_images WHERE id = ? AND chat_id = ?", (attachment_id, chat_id)
            ).fetchone():
                raise HTTPException(422, "Draft attachment not found in this chat")
        conn.execute(
            "INSERT INTO chat_drafts VALUES (?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
            "revision = excluded.revision, mutation_id = excluded.mutation_id, "
            "content = excluded.content, attachment_ids = excluded.attachment_ids",
            (chat_id, revision + 1, mutation, text, json.dumps(ids)),
        )
        return _read(conn, chat_id)
