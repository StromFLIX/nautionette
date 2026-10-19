"""Bound provider-generated tool IDs without losing retry/answer correlation."""

from __future__ import annotations

import hashlib


def publication_request_id(value: str) -> str:
    return value if len(value) <= 256 else "sha256:" + hashlib.sha256(value.encode()).hexdigest()
