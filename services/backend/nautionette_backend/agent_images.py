"""Small, self-contained agent portraits; no external URLs or active image formats."""

from __future__ import annotations

import base64
import binascii
import io
import re
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageOps

from .chat_attachments import MAX_FILE_BYTES, _validate_image


def normalize_image(value: Any) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or len(value) > (MAX_FILE_BYTES * 4 // 3) + 128:
        raise HTTPException(422, "Agent image must be a PNG, JPEG or WebP of at most 5 MiB")
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        raise HTTPException(422, "Choose a PNG, JPEG or WebP image file")
    try:
        data = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "Invalid agent image encoding") from exc
    if not data or len(data) > MAX_FILE_BYTES:
        raise HTTPException(422, "Agent images must be nonempty and at most 5 MiB")
    _validate_image(data, match[1])
    with Image.open(io.BytesIO(data)) as original:
        portrait = ImageOps.exif_transpose(original).convert("RGBA")
        portrait.thumbnail((256, 256))
        output = io.BytesIO()
        portrait.save(output, format="WEBP", quality=85)
    return "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode("ascii")
