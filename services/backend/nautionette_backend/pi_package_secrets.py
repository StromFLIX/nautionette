"""Encrypt managed configuration at rest with a backend-only, auto-created key.

Back up the whole backend data volume (including this key), not just SQLite.
This protects DB exports, not a compromised backend or a running extension that
has explicitly been given the values.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from .config import settings


def _cipher(*, create: bool = False) -> Fernet:
    key_file = Path(settings.data_dir) / "pi-packages.key"
    if create and not key_file.exists():
        # Publish a fully-written mode-0600 file atomically. Concurrent writers
        # keep the winner's key; they must never overwrite it with another key.
        with tempfile.NamedTemporaryFile(dir=settings.data_dir) as temporary:
            temporary.write(Fernet.generate_key())
            temporary.flush()
            os.fsync(temporary.fileno())
            try:
                os.link(temporary.name, key_file)
            except FileExistsError:
                pass
    fd = os.open(key_file, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as key:
        return Fernet(key.read())


def seal(value: dict[str, Any]) -> str:
    try:
        return _cipher(create=True).encrypt(json.dumps(value).encode()).decode()
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Package configuration encryption is unavailable") from exc


def unseal(value: str) -> dict[str, Any]:
    try:
        return json.loads(_cipher().decrypt(value.encode()))
    except (OSError, ValueError, InvalidToken) as exc:
        raise HTTPException(
            503, "Package configuration key is missing or invalid; restore the backend data backup"
        ) from exc
