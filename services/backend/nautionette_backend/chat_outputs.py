"""Chat-owned, immutable output revisions, independent of agent containers.

The bounded first version stores bytes with metadata in SQLite, like incoming
attachments. One transaction publishes everything or nothing, and normal chat
backup/deletion includes outputs. No container paths or public download URLs are
accepted. Only a short-lived, chat/turn-scoped capability can publish/read from
an agent; the app uses its normal authenticated, chat-scoped read routes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageOps

from . import chat_attachments
from .db import db

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PUBLICATION_BYTES = 40 * 1024 * 1024
MAX_CHAT_BYTES = 256 * 1024 * 1024
MAX_FILES = 8
MAX_OUTPUTS = 50
MAX_REVISIONS = 64
# Leave room for bounded metadata even when every Unicode character is escaped.
MAX_REQUEST_BYTES = (MAX_PUBLICATION_BYTES + 2) // 3 * 4 + 65_536
MAX_BASE64_CHARS = (MAX_FILE_BYTES + 2) // 3 * 4
# These are local tools or private transports, never selectable federated tools.
RESERVED_TOOL_NAMES = frozenset(
    {"publish_output", "read_output", "backend_publish_chat_output", "backend_read_chat_output"}
)

# These inline schemas are also the bounded private transport's MCP schemas.
_TOKEN = {"type": "string", "minLength": 1, "maxLength": 128}
_ID = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
PUBLISH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["grant", "request_id", "title", "files"],
    "properties": {
        "grant": _TOKEN,
        "request_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "output_id": _ID,
        "expected_revision": {"type": "integer", "minimum": 1, "maximum": MAX_REVISIONS},
        "title": {"type": "string", "minLength": 1, "maxLength": 120},
        "summary": {"type": "string", "maxLength": 2000},
        "source_app": {"type": "string", "maxLength": 64},
        "files": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_FILES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "mime_type", "data", "role"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "mime_type": {"type": "string", "minLength": 1, "maxLength": 127},
                    "data": {"type": "string", "minLength": 4, "maxLength": MAX_BASE64_CHARS},
                    "role": {"enum": ["file", "source", "preview"]},
                },
            },
        },
    },
}
READ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["grant"],
    "properties": {
        "grant": _TOKEN,
        "output_id": _ID,
        "revision": {"type": "integer", "minimum": 1, "maximum": MAX_REVISIONS},
        "file_id": _ID,
    },
}

_grants: dict[str, tuple[str, str]] = {}
_grant_lock = threading.Lock()


@contextmanager
def publication_grant(chat_id: str, turn_id: str) -> Iterator[dict[str, str] | None]:
    """Never persist a capability in queued jobs, transcripts, or tool arguments.

    The single backend process owns grants. Restart revokes them all; the usual
    interrupted-turn recovery fences old containers. Each request also checks the
    durable running/stop state, including again in the publication transaction.
    """
    if not chat_id or not turn_id:
        yield None
        return
    token = secrets.token_urlsafe(32)
    key = hashlib.sha256(token.encode()).hexdigest()
    with _grant_lock:
        _grants[key] = (chat_id, turn_id)
    try:
        yield {"grant": token}
    finally:
        with _grant_lock:
            _grants.pop(key, None)


def grant_scope(token: str) -> tuple[str, str]:
    if not isinstance(token, str) or len(token) > 128:
        raise HTTPException(401, "Invalid or expired output capability")
    with _grant_lock:
        scope = _grants.get(hashlib.sha256(token.encode()).hexdigest())
    if not scope:
        raise HTTPException(401, "Invalid or expired output capability")
    return scope


def _active(conn: Any, scope: tuple[str, str]) -> None:
    if not conn.execute(
        "SELECT 1 FROM chat_turns WHERE chat_id = ? AND id = ? AND state = 'running' AND stop_requested = 0",
        scope,
    ).fetchone():
        raise HTTPException(409, "This turn can no longer access chat outputs")


def _text(value: str) -> str:
    return "".join(char for char in value if char.isprintable() or char in "\n\t").strip()


def _prepare_file(item: dict[str, Any]) -> dict[str, Any]:
    try:
        data = base64.b64decode(item["data"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "Output files must contain valid base64 bytes") from exc
    if not data or len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Output files must be nonempty and at most 20 MiB each")
    if base64.b64encode(data).decode("ascii") != item["data"]:
        raise HTTPException(422, "Output files must contain canonical base64 bytes")
    mime = item["mime_type"].split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+_-]+/[a-z0-9.+_-]+", mime):
        mime = "application/octet-stream"
    name = _text(item["name"].replace("\\", "/").split("/")[-1])
    if not name or name in {".", ".."}:
        raise HTTPException(422, "Output files need a filename")
    preview = None
    if mime in chat_attachments.MIME_TYPES.values():
        chat_attachments._validate_image(data, mime)
        with Image.open(io.BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original).convert("RGBA")
            image.thumbnail((1600, 1600))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            preview = buffer.getvalue()
    elif item["role"] == "preview":
        raise HTTPException(
            422, "Previews must be PNG, JPEG, GIF or WebP images; other files are download-only"
        )
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "mime_type": mime,
        "role": item["role"],
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "previewable": preview is not None,
        "data": data,
        "preview": preview,
    }


def publish(payload: dict[str, Any]) -> dict[str, Any]:
    scope = grant_scope(payload["grant"])
    # Fail early before image decoding, then recheck at commit to fence Stop,
    # deletion, completion, or grant revocation during validation.
    with db.transaction() as conn:
        _active(conn, scope)
    if ("output_id" in payload) != ("expected_revision" in payload):
        raise HTTPException(422, "Updating an output requires output_id and expected_revision together")
    if sum(len(item["data"]) for item in payload["files"]) > (MAX_PUBLICATION_BYTES + 2) // 3 * 4 + 32:
        raise HTTPException(413, "An output publication must be at most 40 MiB")
    files = [_prepare_file(item) for item in payload["files"]]
    if sum(item["size"] for item in files) > MAX_PUBLICATION_BYTES:
        raise HTTPException(413, "An output publication must be at most 40 MiB")
    metadata = {
        "title": _text(payload["title"]),
        "summary": _text(payload.get("summary", "")),
        "source_app": _text(payload.get("source_app", "")),
    }
    if not metadata["title"]:
        raise HTTPException(422, "An output needs a title")
    public_files = [{k: v for k, v in item.items() if k not in {"data", "preview"}} for item in files]
    fingerprint = {
        **metadata,
        "output_id": payload.get("output_id"),
        "expected_revision": payload.get("expected_revision"),
        "files": [{k: v for k, v in item.items() if k != "id"} for item in public_files],
    }
    digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    chat_id, turn_id = scope
    now = time.time()
    with db.transaction() as conn:
        if grant_scope(payload["grant"]) != scope:
            raise HTTPException(401, "Invalid or expired output capability")
        _active(conn, scope)
        existing = conn.execute(
            "SELECT manifest, request_hash FROM chat_output_revisions WHERE turn_id = ? AND request_id = ?",
            (turn_id, payload["request_id"]),
        ).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise HTTPException(409, "This publication ID was already used for different content")
            return json.loads(existing["manifest"])
        output_id = payload.get("output_id") or uuid.uuid4().hex
        revision = 1
        if "output_id" in payload:
            current = conn.execute(
                "SELECT current_revision FROM chat_outputs WHERE id = ? AND chat_id = ?", (output_id, chat_id)
            ).fetchone()
            if not current:
                raise HTTPException(404, "Output not found in this chat")
            if current["current_revision"] != payload["expected_revision"]:
                raise HTTPException(409, "Output has a newer revision; read it before publishing again")
            revision = current["current_revision"] + 1
            if revision > MAX_REVISIONS:
                raise HTTPException(409, "Output revision limit reached; publish a new output")
        elif (
            conn.execute("SELECT COUNT(*) FROM chat_outputs WHERE chat_id = ?", (chat_id,)).fetchone()[0]
            >= MAX_OUTPUTS
        ):
            raise HTTPException(409, "This chat has reached its 50-output limit")
        used = conn.execute(
            "SELECT COALESCE(SUM(f.size + COALESCE(length(f.preview), 0)), 0) "
            "FROM chat_output_files f JOIN chat_outputs o ON o.id = f.output_id WHERE o.chat_id = ?",
            (chat_id,),
        ).fetchone()[0]
        if used + sum(item["size"] + len(item["preview"] or b"") for item in files) > MAX_CHAT_BYTES:
            raise HTTPException(413, "This chat has reached its 256 MiB output storage limit")
        if revision == 1:
            conn.execute("INSERT INTO chat_outputs VALUES (?,?,?,?,?)", (output_id, chat_id, 1, now, now))
        manifest = {
            "id": output_id,
            "revision": revision,
            **metadata,
            "turn_id": turn_id,
            "created_at": now,
            "files": public_files,
        }
        conn.execute(
            "INSERT INTO chat_output_revisions VALUES (?,?,?,?,?,?,?)",
            (output_id, revision, turn_id, payload["request_id"], digest, json.dumps(manifest), now),
        )
        conn.executemany(
            "INSERT INTO chat_output_files VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    item["id"],
                    output_id,
                    revision,
                    item["name"],
                    item["mime_type"],
                    item["role"],
                    item["size"],
                    item["data"],
                    item["preview"],
                )
                for item in files
            ],
        )
        conn.execute(
            "UPDATE chat_outputs SET current_revision = ?, updated_at = ? WHERE id = ?",
            (revision, now, output_id),
        )
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
    return manifest


def revisions(chat_id: str, output_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT r.manifest FROM chat_output_revisions r JOIN chat_outputs o ON o.id = r.output_id "
        "WHERE o.chat_id = ? AND o.id = ? ORDER BY r.revision DESC",
        (chat_id, output_id),
    )
    if not rows:
        raise HTTPException(404, "Output not found in this chat")
    return [json.loads(row["manifest"]) for row in rows]


def file(chat_id: str, output_id: str, revision: int, file_id: str) -> dict[str, Any]:
    stored = db.one(
        "SELECT f.* FROM chat_output_files f JOIN chat_outputs o ON o.id = f.output_id "
        "WHERE o.chat_id = ? AND f.output_id = ? AND f.revision = ? AND f.id = ?",
        (chat_id, output_id, revision, file_id),
    )
    if not stored:
        raise HTTPException(404, "Output file not found in this chat/revision")
    return stored


def read(payload: dict[str, Any]) -> dict[str, Any]:
    scope = grant_scope(payload["grant"])
    with db.transaction() as conn:
        _active(conn, scope)
    chat_id, _ = scope
    if "output_id" not in payload:
        if "file_id" in payload or "revision" in payload:
            raise HTTPException(422, "Provide output_id to select a revision or file")
        return {"outputs": db.list_chat_outputs(chat_id)}
    versions = revisions(chat_id, payload["output_id"])
    manifest = next(
        (item for item in versions if item["revision"] == payload.get("revision", versions[0]["revision"])),
        None,
    )
    if not manifest:
        raise HTTPException(404, "Output revision not found")
    if "file_id" not in payload:
        return {"output": manifest, "revisions": [item["revision"] for item in versions]}
    stored = file(chat_id, manifest["id"], manifest["revision"], payload["file_id"])
    return {
        "output_id": manifest["id"],
        "revision": manifest["revision"],
        "file": {
            "id": stored["id"],
            "name": stored["name"],
            "mime_type": stored["mime_type"],
            "size": stored["size"],
            "data": base64.b64encode(stored["data"]).decode("ascii"),
        },
    }
