"""Who is allowed to call what.

Two doors: a bearer token for the clients, an internal token for the other
services. Neither is a cookie, so nothing is ever sent on the browser's say-so.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Query

from .config import settings


def token_matches(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied or "", expected)


async def require_user(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    x_auth_token: str | None = Header(default=None),
) -> None:
    if not settings.auth_enabled:
        return
    supplied = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    supplied = supplied or x_auth_token or token
    if not token_matches(supplied, settings.app_token):
        raise HTTPException(status_code=401, detail="unauthorized")


async def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    if not settings.internal_token:
        return
    if not token_matches(x_internal_token, settings.internal_token):
        raise HTTPException(status_code=401, detail="unauthorized")
