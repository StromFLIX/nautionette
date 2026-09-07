"""Bounded, authenticated chat images. Bytes never enter transcript snapshots or events."""

from __future__ import annotations

import base64
import io
import time
import uuid
import warnings
from typing import Any

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from .db import db

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 4
MAX_PIXELS = 25_000_000
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


def store_image(chat_id: str, data: bytes, mime_type: str, name: str) -> dict[str, Any]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Images must be nonempty and at most 5 MiB each")
    if mime_type not in MIME_TYPES.values():
        raise HTTPException(415, "Choose a PNG, JPEG, GIF or WebP image")
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
    # Unsent uploads expire; attached images live and die with their message/chat.
    db.execute("DELETE FROM chat_images WHERE message_id IS NULL AND created_at < ?", (time.time() - 86400,))
    pending = db.one(
        "SELECT COUNT(*) AS n FROM chat_images WHERE chat_id = ? AND message_id IS NULL", (chat_id,)
    )
    if pending and pending["n"] >= 20:
        raise HTTPException(409, "Too many unsent images in this chat; remove unused attachments first")
    metadata = {
        "id": uuid.uuid4().hex,
        "name": name.replace("\\", "/").split("/")[-1][:200] or "image",
        "mime_type": mime_type,
        "size": len(data),
    }
    db.execute(
        "INSERT INTO chat_images (id, chat_id, name, mime_type, size, data, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (metadata["id"], chat_id, metadata["name"], mime_type, len(data), data, time.time()),
    )
    return metadata


def image_ids(value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > MAX_IMAGES
        or any(not isinstance(item, str) or len(item) > 128 for item in value)
    ):
        raise HTTPException(422, "attachment_ids must contain at most 4 image IDs")
    if len(set(value)) != len(value):
        raise HTTPException(422, "Duplicate image IDs are not allowed")
    return value


def load_images(chat_id: str, attachments: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for attachment in attachments:
        image = db.one(
            "SELECT mime_type, data FROM chat_images WHERE id = ? AND chat_id = ?",
            (attachment["id"], chat_id),
        )
        if not image:
            raise ValueError("A chat image is no longer available")
        out.append(
            {
                "type": "image",
                "mimeType": image["mime_type"],
                "data": base64.b64encode(image["data"]).decode("ascii"),
            }
        )
    return out
