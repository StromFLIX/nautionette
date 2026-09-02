"""Talking to agentgateway's own configuration store.

Model integrations and MCP servers are both written as config resources, so the
handling of storage modes, resource lookups and refusals is shared.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import HTTPException


def resource_map(resources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        resource["id"]: resource
        for resource in resources
        if isinstance(resource.get("id"), str) and isinstance(resource.get("value"), dict)
    }


def storage_mode(runtime_state: dict[str, Any]) -> str:
    return (runtime_state.get("ui") or {}).get("configStoreMode", "unknown")


def require_writable(mode: str) -> None:
    if mode != "hybrid":
        raise HTTPException(
            status_code=409,
            detail=f"agentgateway configuration storage is {mode!r}; hybrid mode is required",
        )


def credential_state(credential: str, auth: dict[str, Any] | None = None) -> dict[str, str]:
    """Where the key for this resource comes from, without ever showing the key."""
    if credential.startswith("$"):
        return {"mode": "environment", "variable": credential[1:]}
    if credential:
        return {"mode": "stored", "variable": ""}
    auth = auth or {}
    if auth.get("builtin"):
        return {"mode": "gateway", "variable": str(auth.get("env", ""))}
    return {"mode": "none", "variable": ""}


def gateway_problem(exc: httpx.HTTPError, credential: str = "") -> HTTPException:
    response_status = 502
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text
        missing = re.search(r"key '([A-Z0-9_]+)' up: environment variable not found", body)
        # agentgateway refuses a credential it cannot read, and refuses an empty one outright.
        if missing or (credential.startswith("$") and "BackendAuthCompat" in body):
            variable = missing.group(1) if missing else credential[1:]
            return HTTPException(
                status_code=400,
                detail=(
                    f"agentgateway has no value for {variable}. Enter the key here "
                    "instead, or set that variable on the agentgateway service."
                ),
            )
        if status == 409:
            response_status = 409
            detail = "agentgateway rejected a conflicting integration resource"
        else:
            detail = f"agentgateway configuration request failed (HTTP {status})"
    else:
        detail = "agentgateway is unavailable"
    return HTTPException(status_code=response_status, detail=detail)


async def attempt(coro, fallback):
    """Run a call whose failure is an empty answer, not an error page."""
    try:
        return await coro
    except Exception:  # noqa: BLE001 - a catalog that fails is an empty picker
        return fallback
