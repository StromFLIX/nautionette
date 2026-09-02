"""Bits every outbound client shares."""

from __future__ import annotations

import asyncio

import httpx

from ..config import settings

_pool: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = None


def shared() -> httpx.AsyncClient:
    """One pooled client for every outbound call, so a connection is reused.

    A client belongs to the loop that opened it. Anything running on a second
    loop -- which only happens under test -- gets its own rather than a pool it
    cannot use.
    """
    global _pool
    loop = asyncio.get_running_loop()
    if _pool is None or _pool[0] is not loop or _pool[1].is_closed:
        _pool = (loop, httpx.AsyncClient())
    return _pool[1]


async def close_shared() -> None:
    global _pool
    if _pool is not None:
        await _pool[1].aclose()
        _pool = None


def internal_headers() -> dict[str, str]:
    if settings.internal_token:
        return {"X-Internal-Token": settings.internal_token}
    return {}


def upstream_problem(name: str, credential: str, status: int | None, body: str) -> str:
    """One actionable sentence, whichever provider refused the call."""
    text = body.lower()
    if "token not found" in text:
        return f"agentgateway found no {name} credential. Add {credential} and try again."
    if "copilot-integration-id" in text:
        return f"{name} rejected the configured integration ID."
    # xAI answers a rejected key with 400, so the body decides alongside the status.
    if status in {401, 403} or "api key" in text or "unauthorized" in text:
        return f"{name} rejected {credential}."
    suffix = f" (HTTP {status})" if status else ""
    return f"agentgateway could not reach {name}{suffix}."
