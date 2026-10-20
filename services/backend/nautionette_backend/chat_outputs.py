"""Chat-owned immutable output manifests, with binaries in private S3 storage.

The private MCP surface exchanges small manifests/transfer capabilities only.
Binary uploads go directly to S3, then are verified and copied to unexposed,
immutable final keys before a metadata transaction makes the revision visible.
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
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from tempfile import TemporaryFile
from typing import Any, BinaryIO

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from . import chat_attachments
from . import output_storage as storage
from .db import db
from .output_ids import publication_request_id

# Compatibility bounds for agents still running the old inline transport. New
# agents use prepare + binary transfer + commit and the configurable policy.
MAX_FILE_BYTES = 20 * storage.MiB
MAX_PUBLICATION_BYTES = 40 * storage.MiB
MAX_CHAT_BYTES = 256 * storage.MiB
MAX_FILES = 8
MAX_OUTPUTS = 50
MAX_REVISIONS = 64
MAX_REQUEST_BYTES = (MAX_PUBLICATION_BYTES + 2) // 3 * 4 + 65_536
MAX_BASE64_CHARS = (MAX_FILE_BYTES + 2) // 3 * 4
RESERVED_TOOL_NAMES = frozenset(
    {
        "publish_output",
        "read_output",
        "backend_prepare_chat_output",
        "backend_publish_chat_output",
        "backend_read_chat_output",
    }
)
_TOKEN = {"type": "string", "minLength": 1, "maxLength": 128}
_ID = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
_REQUEST_ID = {"type": "string", "minLength": 1, "maxLength": 256}
_FILE = {
    "name": {"type": "string", "minLength": 1, "maxLength": 200},
    "mime_type": {"type": "string", "minLength": 1, "maxLength": 127},
    "role": {"enum": ["file", "source", "preview"]},
}
_METADATA = {
    "grant": _TOKEN,
    "request_id": _REQUEST_ID,
    "output_id": _ID,
    "expected_revision": {"type": "integer", "minimum": 1, "maximum": MAX_REVISIONS},
    "title": {"type": "string", "minLength": 1, "maxLength": 120},
    "summary": {"type": "string", "maxLength": 2000},
    "source_app": {"type": "string", "maxLength": 64},
}
PREPARE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["grant", "request_id", "title", "files"],
    "properties": {
        **_METADATA,
        "files": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_FILES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "mime_type", "role", "size", "sha256"],
                "properties": {
                    **_FILE,
                    "size": {"type": "integer", "minimum": 1, "maximum": storage.HARD_FILE_BYTES},
                    "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                },
            },
        },
    },
}
PUBLISH_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["grant", "request_id", "upload_id"],
            "properties": {"grant": _TOKEN, "request_id": _REQUEST_ID, "upload_id": _ID},
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["grant", "request_id", "title", "files"],
            "properties": {
                **_METADATA,
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_FILES,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "mime_type", "data", "role"],
                        "properties": {
                            **_FILE,
                            "data": {
                                "type": "string",
                                "minLength": 4,
                                "maxLength": MAX_BASE64_CHARS,
                            },
                        },
                    },
                },
            },
        },
    ]
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
        "binary": {"type": "boolean"},
    },
}

_grants: dict[str, tuple[str, str]] = {}
_grant_lock = threading.Lock()


@contextmanager
def publication_grant(chat_id: str, turn_id: str) -> Iterator[dict[str, str] | None]:
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


def _file_metadata(item: dict[str, Any]) -> dict[str, Any]:
    name = _text(item["name"].replace("\\", "/").split("/")[-1])
    if not name or name in {".", ".."}:
        raise HTTPException(422, "Output files need a filename")
    mime = item["mime_type"].split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+_-]+/[a-z0-9.+_-]+", mime):
        mime = "application/octet-stream"
    if item["role"] == "preview" and mime not in chat_attachments.MIME_TYPES.values():
        raise HTTPException(
            422, "Previews must be PNG, JPEG, GIF or WebP images; other files are download-only"
        )
    return {
        "name": name,
        "mime_type": mime,
        "role": item["role"],
        "size": item["size"],
        "sha256": item["sha256"],
    }


def _sizes(files: list[dict[str, Any]]) -> None:
    limits = storage.policy()
    for item in files:
        if not 0 < item["size"] <= limits["max_file_bytes"]:
            raise storage.failure(
                "output_file_too_large",
                "An output file exceeds the configured file limit. "
                "Increase it in Settings → Storage, or publish a smaller file.",
                requested_bytes=item["size"],
                limit_bytes=limits["max_file_bytes"],
            )
    total = sum(item["size"] for item in files)
    if total > limits["max_publication_bytes"]:
        raise storage.failure(
            "output_publication_too_large",
            "This publication exceeds its configured size limit. "
            "Increase it in Settings → Storage, or publish smaller snapshots.",
            requested_bytes=total,
            limit_bytes=limits["max_publication_bytes"],
        )


def _preview_reserve(files: list[dict[str, Any]]) -> int:
    # A 1600×1600 RGBA PNG fits within 11 MiB even if compression expands it.
    return sum(
        11 * storage.MiB for item in files if item["mime_type"] in chat_attachments.MIME_TYPES.values()
    )


def _preview(source: BinaryIO, mime: str) -> bytes | None:
    if mime not in chat_attachments.MIME_TYPES.values():
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source.seek(0)
            with Image.open(source) as image:
                if chat_attachments.MIME_TYPES.get(image.format) != mime:
                    raise ValueError("Mismatched image type")
                if image.width * image.height > chat_attachments.MAX_PIXELS:
                    raise ValueError("Image exceeds pixel limit")
                image.verify()
            source.seek(0)
            with Image.open(source) as image:
                image.load()
                normalized = ImageOps.exif_transpose(image).convert("RGBA")
                normalized.thumbnail((1600, 1600))
                buffer = io.BytesIO()
                normalized.save(buffer, format="PNG")
                return buffer.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(422, "Invalid image, mismatched type, or image exceeds 25 megapixels") from exc


def _prepare_file(item: dict[str, Any]) -> dict[str, Any]:
    """Bounded backwards compatibility for already-running old agent images."""
    try:
        data = base64.b64decode(item["data"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "Output files must contain valid base64 bytes") from exc
    if not data or len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Inline output files must be nonempty and at most 20 MiB each")
    if base64.b64encode(data).decode("ascii") != item["data"]:
        raise HTTPException(422, "Output files must contain canonical base64 bytes")
    result = _file_metadata({**item, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    _preview(io.BytesIO(data), result["mime_type"])
    return {**result, "data": data}


def _existing(conn: Any, turn_id: str, request_id: str, digest: str | None = None) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT manifest, request_hash FROM chat_output_revisions WHERE turn_id = ? AND request_id = ?",
        (turn_id, request_id),
    ).fetchone()
    if row:
        if digest is not None and row["request_hash"] != digest:
            raise HTTPException(409, "This publication ID was already used for different content")
        return json.loads(row["manifest"])
    return None


def _revision(conn: Any, chat_id: str, payload: dict[str, Any]) -> tuple[str, int]:
    if "output_id" in payload:
        current = conn.execute(
            "SELECT current_revision, next_revision FROM chat_outputs WHERE id = ? AND chat_id = ?",
            (payload["output_id"], chat_id),
        ).fetchone()
        if not current:
            raise HTTPException(
                404,
                "Output not found in this chat. To create a new output, omit both output_id and "
                "expected_revision; the server generates the ID. To update an existing output, "
                "call read_output with no arguments to find its ID and current revision in this chat. "
                "Never invent an ID or retry a missing ID.",
            )
        if current["current_revision"] != payload["expected_revision"]:
            raise HTTPException(409, "Output has a newer revision; read it before publishing again")
        if current["next_revision"] > MAX_REVISIONS:
            raise storage.failure(
                "output_revision_limit", "Output revision limit reached; publish a new output.", 409
            )
        return payload["output_id"], current["next_revision"]
    if (
        conn.execute("SELECT COUNT(*) FROM chat_outputs WHERE chat_id = ?", (chat_id,)).fetchone()[0]
        >= MAX_OUTPUTS
    ):
        raise storage.failure(
            "output_count_limit",
            "This chat has reached its 50-output limit. Remove outputs in Settings → Storage.",
            409,
        )
    return uuid.uuid4().hex, 1


def _register(key: str, size: int, kind: str, *, staging: bool = False) -> None:
    now = time.time()
    db.execute(
        "INSERT INTO output_storage_objects (key,size,kind,state,delete_after,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            key,
            size,
            kind,
            "staging" if staging else "pending",
            now + (storage.TRANSFER_SECONDS + 60 if staging else 3600),
            now,
        ),
    )


def prepare(payload: dict[str, Any]) -> dict[str, Any]:
    """Reserve quota and issue size/checksum-bound URLs; no binary MCP payload."""
    scope = grant_scope(payload["grant"])
    if ("output_id" in payload) != ("expected_revision" in payload):
        raise HTTPException(
            422,
            "Updating an output requires output_id and expected_revision together. "
            "To create a new output, omit both fields; the server generates the ID.",
        )
    files = [_file_metadata(item) for item in payload["files"]]
    metadata = {key: _text(payload.get(key, "")) for key in ("title", "summary", "source_app")}
    if not metadata["title"]:
        raise HTTPException(422, "An output needs a title")
    document = {
        **metadata,
        "files": files,
        **{key: payload[key] for key in ("output_id", "expected_revision") if key in payload},
    }
    digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    request_id = publication_request_id(payload["request_id"])
    with storage.lock:
        with db.transaction() as conn:
            _active(conn, scope)
            if previous := _existing(conn, scope[1], request_id, digest):
                # A committed retry consumes no new quota or disk space, even
                # if limits have since been lowered or S3 is unavailable.
                return {"output": previous}
            reservations = conn.execute(
                "SELECT reserved_bytes,payload FROM chat_output_uploads "
                "WHERE NOT (turn_id = ? AND request_id = ?) AND expires_at > ?",
                (scope[1], request_id, time.time()),
            ).fetchall()
        _sizes(files)
        store = storage.get_store()
        store.ready()
        total = sum(item["size"] for item in files)
        reserved = sum(row["reserved_bytes"] for row in reservations)
        # Space for staging plus isolated final copies and bounded thumbnails
        # from ALL concurrent reservations, not just this publication's images.
        overhead = _preview_reserve(files) + sum(
            _preview_reserve(json.loads(row["payload"])["files"]) for row in reservations
        )
        storage.require_space(2 * (total + reserved) + overhead)
        storage.require_space(max(item["size"] for item in files), backend=True)
        now = time.time()
        with db.transaction() as conn:
            _active(conn, scope)
            old = conn.execute(
                "SELECT * FROM chat_output_uploads WHERE turn_id = ? AND request_id = ?",
                (scope[1], request_id),
            ).fetchone()
            if old and old["expires_at"] <= now:
                conn.execute("DELETE FROM chat_output_uploads WHERE id = ?", (old["id"],))
                old = None
            if old and old["request_hash"] != digest:
                raise HTTPException(409, "This publication ID was already used for different content")
            # Re-preparing an unfinished reservation must respect newly saved
            # limits and a concurrently advanced/deleted output, just as commit does.
            _revision(conn, scope[0], document)
            storage.check_quota(conn, scope[0], total, old["id"] if old else "")
            if old:
                upload_id, document = old["id"], json.loads(old["payload"])
                expires_at = old["expires_at"]
            else:
                upload_id = uuid.uuid4().hex
                expires_at = now + storage.TRANSFER_SECONDS
                for item in files:
                    item["id"] = uuid.uuid4().hex
                    item["object_key"] = f"uploads/{upload_id}/{item['id']}"
                conn.execute(
                    "INSERT INTO chat_output_uploads VALUES (?,?,?,?,?,?,?,?)",
                    (
                        upload_id,
                        *scope,
                        request_id,
                        digest,
                        json.dumps(document),
                        total,
                        now + storage.TRANSFER_SECONDS,
                    ),
                )
                for item in files:
                    conn.execute(
                        "INSERT INTO output_storage_objects VALUES (?,?,?,?,?,?)",
                        (
                            item["object_key"],
                            item["size"],
                            "staging",
                            "staging",
                            now + storage.TRANSFER_SECONDS + 60,
                            now,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO chat_output_upload_files VALUES (?,?)", (upload_id, item["object_key"])
                    )
        return {
            "upload_id": upload_id,
            "files": [
                {
                    "id": item["id"],
                    **store.upload_url(
                        item["object_key"],
                        item["size"],
                        item["sha256"],
                        max(1, int(expires_at - time.time())),
                    ),
                }
                for item in document["files"]
            ],
            "limits": storage.policy(),
            "settings_url": storage.settings_url(),
        }


def publish(payload: dict[str, Any]) -> dict[str, Any]:
    if "upload_id" in payload:
        return _commit_upload(payload)
    scope = grant_scope(payload["grant"])
    with db.transaction() as conn:
        _active(conn, scope)
    if sum(len(item["data"]) for item in payload["files"]) > (MAX_PUBLICATION_BYTES + 2) // 3 * 4 + 32:
        raise HTTPException(413, "Inline publications must be at most 40 MiB")
    files = [_prepare_file(item) for item in payload["files"]]
    uploaded = prepare(
        {**payload, "files": [{key: value for key, value in item.items() if key != "data"} for item in files]}
    )
    if "output" in uploaded:
        return uploaded["output"]
    stored = db.one("SELECT payload FROM chat_output_uploads WHERE id = ?", (uploaded["upload_id"],))
    for item, target in zip(files, json.loads(stored["payload"])["files"], strict=True):
        storage.get_store().put(target["object_key"], item["data"], item["size"])
    return _commit_upload(
        {"grant": payload["grant"], "request_id": payload["request_id"], "upload_id": uploaded["upload_id"]}
    )


def _commit_upload(payload: dict[str, Any]) -> dict[str, Any]:
    scope = grant_scope(payload["grant"])
    request_id = publication_request_id(payload["request_id"])
    keys: list[str] = []
    with storage.lock:
        with db.transaction() as conn:
            _active(conn, scope)
            if previous := _existing(conn, scope[1], request_id):
                return previous
            upload = conn.execute(
                "SELECT * FROM chat_output_uploads WHERE id = ? AND chat_id = ? "
                "AND turn_id = ? AND request_id = ?",
                (payload["upload_id"], *scope, request_id),
            ).fetchone()
            if not upload or upload["expires_at"] <= time.time():
                raise HTTPException(409, "Output upload expired or not found in this turn; prepare it again")
            document = json.loads(upload["payload"])
            output_id, revision = _revision(conn, scope[0], document)
            storage.check_quota(conn, scope[0], upload["reserved_bytes"], upload["id"])
        _sizes(document["files"])
        store = storage.get_store()
        files = []
        try:
            storage.require_space(upload["reserved_bytes"])
            storage.require_space(max(item["size"] for item in document["files"]), backend=True)
            for item in document["files"]:
                # No unbounded original-file buffers and no SQLite lock during
                # network I/O or image validation. Final keys have no PUT URLs.
                with TemporaryFile(dir=settings_temp_dir()) as source:
                    store.download(item["object_key"], source, item["size"], item["sha256"])
                    preview = _preview(source, item["mime_type"])
                    key = f"outputs/{output_id}/{revision}/{uuid.uuid4().hex}"
                    _register(key, item["size"], "original")
                    keys.append(key)
                    store.copy(item["object_key"], key)
                    # Verify the isolated final copy too: a provider which ignores
                    # PUT checksums or copy preconditions cannot race the staged
                    # object between validation and copying into a publication.
                    source.seek(0)
                    source.truncate(0)
                    store.download(key, source, item["size"], item["sha256"])
                preview_key = None
                if preview is not None:
                    storage.require_space(len(preview))
                    preview_key = key + ".preview.png"
                    _register(preview_key, len(preview), "preview")
                    keys.append(preview_key)
                    store.put(preview_key, preview, len(preview))
                    store.bytes(preview_key, len(preview), hashlib.sha256(preview).hexdigest())
                files.append(
                    {
                        **item,
                        "object_key": key,
                        "preview_key": preview_key,
                        "preview_size": len(preview or b""),
                        "previewable": preview is not None,
                    }
                )
            now = time.time()
            with db.transaction() as conn:
                if grant_scope(payload["grant"]) != scope:
                    raise HTTPException(401, "Invalid or expired output capability")
                _active(conn, scope)
                _revision(conn, scope[0], document)  # Stop/deletion/update fence.
                storage.check_quota(
                    conn, scope[0], sum(item["size"] + item["preview_size"] for item in files), upload["id"]
                )
                manifest = {
                    "id": output_id,
                    "revision": revision,
                    **{key: document[key] for key in ("title", "summary", "source_app")},
                    "turn_id": scope[1],
                    "created_at": now,
                    "files": [
                        {
                            key: value
                            for key, value in item.items()
                            if key not in {"object_key", "preview_key", "preview_size"}
                        }
                        for item in files
                    ],
                }
                if revision == 1:
                    conn.execute(
                        "INSERT INTO chat_outputs "
                        "(id,chat_id,current_revision,created_at,updated_at,next_revision) "
                        "VALUES (?,?,?,?,?,?)",
                        (output_id, scope[0], 1, now, now, 2),
                    )
                conn.execute(
                    "INSERT INTO chat_output_revisions VALUES (?,?,?,?,?,?,?)",
                    (
                        output_id,
                        revision,
                        scope[1],
                        request_id,
                        upload["request_hash"],
                        json.dumps(manifest),
                        now,
                    ),
                )
                conn.executemany(
                    "INSERT INTO chat_output_files (id,output_id,revision,name,mime_type,role,size,"
                    "object_key,preview_key,preview_size,sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            item["id"],
                            output_id,
                            revision,
                            item["name"],
                            item["mime_type"],
                            item["role"],
                            item["size"],
                            item["object_key"],
                            item["preview_key"],
                            item["preview_size"],
                            item["sha256"],
                        )
                        for item in files
                    ],
                )
                conn.executemany(
                    "UPDATE output_storage_objects SET state = 'live', delete_after = 0 WHERE key = ?",
                    [(key,) for key in keys],
                )
                conn.execute(
                    "UPDATE chat_outputs SET current_revision = ?, next_revision = ?, updated_at = ? "
                    "WHERE id = ?",
                    (revision, revision + 1, now, output_id),
                )
                conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, scope[0]))
                conn.execute("DELETE FROM chat_output_uploads WHERE id = ?", (upload["id"],))
            return manifest
        except Exception as exc:
            # Cleanup is durable even if S3 is down. Staging objects cannot be
            # removed until all issued PUT capabilities expire (+ clock skew).
            with db.transaction() as conn:
                conn.executemany(
                    "UPDATE output_storage_objects SET state = 'delete', delete_after = 0 WHERE key = ?",
                    [(key,) for key in keys],
                )
                conn.execute("DELETE FROM chat_output_uploads WHERE id = ?", (upload["id"],))
            if isinstance(exc, OSError):
                raise storage.io_failure(exc) from None
            raise


def settings_temp_dir() -> str:
    from .config import settings

    return settings.data_dir


def revisions(chat_id: str, output_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT r.manifest FROM chat_output_revisions r JOIN chat_outputs o ON o.id = r.output_id "
        "WHERE o.chat_id = ? AND o.id = ? ORDER BY r.revision DESC",
        (chat_id, output_id),
    )
    if not rows:
        raise HTTPException(404, "Output not found in this chat")
    return [json.loads(row["manifest"]) for row in rows]


def file_metadata(chat_id: str, output_id: str, revision: int, file_id: str) -> dict[str, Any]:
    stored = db.one(
        "SELECT f.* FROM chat_output_files f JOIN chat_outputs o ON o.id = f.output_id "
        "WHERE o.chat_id = ? AND f.output_id = ? AND f.revision = ? AND f.id = ?",
        (chat_id, output_id, revision, file_id),
    )
    if not stored:
        raise HTTPException(404, "Output file not found in this chat/revision")
    return stored


def file(chat_id: str, output_id: str, revision: int, file_id: str) -> dict[str, Any]:
    """Compatibility helper; HTTP downloads use the streaming S3 body instead."""
    stored = file_metadata(chat_id, output_id, revision, file_id)
    if stored["object_key"]:
        stored["data"] = storage.get_store().bytes(stored["object_key"], stored["size"], stored["sha256"])
        if stored["preview_key"]:
            body, _ = storage.get_store().open(stored["preview_key"])
            try:
                stored["preview"] = body.read()
            finally:
                body.close()
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
    stored = file_metadata(chat_id, manifest["id"], manifest["revision"], payload["file_id"])
    metadata = {key: stored[key] for key in ("id", "name", "mime_type", "size", "sha256")}
    if payload.get("binary"):
        if not stored["object_key"]:
            # A source can be restored while the restart-safe bulk migration is
            # still running. Only this file needs migrating before signing it.
            from .output_maintenance import migrate_file

            migrate_file(stored)
            stored = file_metadata(chat_id, manifest["id"], manifest["revision"], payload["file_id"])
            metadata["sha256"] = stored["sha256"]
        metadata["download_url"] = storage.get_store().download_url(stored["object_key"])
    else:
        # Existing in-flight agents can finish. This path is never used by the
        # rebuilt extension and never writes new SQLite BLOBs.
        if stored["size"] > MAX_FILE_BYTES:
            raise HTTPException(413, "Rebuild the agent image to restore large files through binary transfer")
        metadata["data"] = base64.b64encode(
            file(chat_id, manifest["id"], manifest["revision"], stored["id"])["data"]
        ).decode("ascii")
    return {"output_id": manifest["id"], "revision": manifest["revision"], "file": metadata}
