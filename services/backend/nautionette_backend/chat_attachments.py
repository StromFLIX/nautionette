"""Bounded chat attachments. Bytes never enter transcript snapshots or events.

The legacy chat_images table also stores files, without parsing or converting them.
Only supported raster images are decoded and passed to the model as vision input.
"""

from __future__ import annotations

import base64
import io
import re
import time
import uuid
import warnings
from typing import Any

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from .db import db

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 4
MAX_PIXELS = 25_000_000
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


def is_image(attachment: dict[str, Any]) -> bool:
    return attachment.get("mime_type") in MIME_TYPES.values()


def store_attachment(chat_id: str, data: bytes, mime_type: str, name: str) -> dict[str, Any]:
    if not data or len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Files must be nonempty and at most 5 MiB each")
    mime_type = mime_type.split(";", 1)[0].strip().lower()
    if len(mime_type) > 127 or not re.fullmatch(r"[a-z0-9.+_-]+/[a-z0-9.+_-]+", mime_type):
        mime_type = "application/octet-stream"
    if mime_type in MIME_TYPES.values():
        _validate_image(data, mime_type)
    # Unsent uploads expire; attached files live and die with their message/chat.
    db.execute("DELETE FROM chat_images WHERE message_id IS NULL AND created_at < ?", (time.time() - 86400,))
    pending = db.one(
        "SELECT COUNT(*) AS n FROM chat_images WHERE chat_id = ? AND message_id IS NULL", (chat_id,)
    )
    if pending and pending["n"] >= 20:
        raise HTTPException(409, "Too many unsent files in this chat; remove unused attachments first")
    basename = name.replace("\\", "/").split("/")[-1]
    metadata = {
        "id": uuid.uuid4().hex,
        "name": "".join(char for char in basename if char.isprintable())[:200] or "attachment",
        "mime_type": mime_type,
        "size": len(data),
    }
    db.execute(
        "INSERT INTO chat_images (id, chat_id, name, mime_type, size, data, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (metadata["id"], chat_id, metadata["name"], mime_type, len(data), data, time.time()),
    )
    return metadata


def _validate_image(data: bytes, mime_type: str) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if MIME_TYPES.get(image.format) != mime_type:
                    raise ValueError("Image content does not match its type")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Images must be at most 25 megapixels")
                image.verify()
            # verify() is only a header check for some formats (notably JPEG).
            # Decode the first frame too, rejecting truncated pixel data up front.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(422, "Invalid image, mismatched type, or image exceeds 25 megapixels") from exc


def attachment_ids(value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > MAX_ATTACHMENTS
        or any(not isinstance(item, str) or len(item) > 128 for item in value)
    ):
        raise HTTPException(422, "attachment_ids must contain at most 4 attachment IDs")
    if len(set(value)) != len(value):
        raise HTTPException(422, "Duplicate attachment IDs are not allowed")
    return value


def load_attachments(chat_id: str, attachments: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Resolve metadata to bytes only for the outgoing, ephemeral agent job."""
    out: dict[str, list[dict[str, Any]]] = {"images": [], "files": []}
    for attachment in attachments:
        stored = db.one(
            "SELECT id, name, mime_type, data FROM chat_images WHERE id = ? AND chat_id = ?",
            (attachment["id"], chat_id),
        )
        if not stored:
            raise ValueError("A chat attachment is no longer available")
        data = base64.b64encode(stored["data"]).decode("ascii")
        if is_image(stored):
            out["images"].append({"type": "image", "mimeType": stored["mime_type"], "data": data})
        else:
            out["files"].append({**stored, "data": data})
    return out
