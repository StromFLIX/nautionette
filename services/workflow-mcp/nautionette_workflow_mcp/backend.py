"""Talking back to the backend, which is the only service that holds Temporal."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger("workflow-mcp")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8080").rstrip("/")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "").strip()


def _headers() -> dict[str, str]:
    return {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}


async def get(path: str, **params: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            f"{BACKEND_URL}{path}",
            headers=_headers(),
            params={key: value for key, value in params.items() if value not in (None, "")},
        )
        response.raise_for_status()
        return response.json()


async def request_worker_restart(reason: str) -> dict[str, Any]:
    """A published workflow is useless until a worker has it loaded."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{BACKEND_URL}/internal/worker/restart",
                headers=_headers(),
                json={"reason": reason},
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001 - surface it, do not fail the publish
        log.warning("worker restart request failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
