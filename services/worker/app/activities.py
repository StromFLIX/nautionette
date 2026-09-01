"""The activities every workflow can call by name.

A workflow file imports nothing from the runtime: it calls "agent_call",
"http_fetch", "emit_event", "save_artifact" or "read_artifact" as strings.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from temporalio import activity

log = logging.getLogger("worker.activities")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8080").rstrip("/")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "").strip()
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "/artifacts"))
AGENT_TIMEOUT_SECONDS = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "900"))
HTTP_MAX_BYTES = 512_000


def _headers() -> dict[str, str]:
    return {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}


@activity.defn(name="agent_call")
async def agent_call(params: dict[str, Any]) -> dict[str, Any]:
    """One agent step. Structured output in, one JSON object out."""
    info = activity.info()
    payload = {
        "prompt": params.get("prompt", ""),
        "system_prompt": params.get("system_prompt"),
        "history": params.get("history") or [],
        "output_schema": params.get("output_schema"),
        "agent_set": params.get("agent_set"),
        "run_id": info.workflow_id,
        "timeout_seconds": int(params.get("timeout_seconds") or AGENT_TIMEOUT_SECONDS),
    }
    timeout = payload["timeout_seconds"] + 60
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        response = await client.post(f"{BACKEND_URL}/internal/agent/call", json=payload, headers=_headers())
        response.raise_for_status()
        result = response.json()

    if not result.get("ok"):
        # Fail the activity so Temporal retries it, instead of passing junk downstream.
        raise RuntimeError(result.get("error") or "agent call failed")
    schema = params.get("output_schema")
    if schema and result.get("output") is None:
        raise RuntimeError("agent returned text where a structured object was declared")
    return result


@activity.defn(name="http_fetch")
async def http_fetch(params: dict[str, Any]) -> dict[str, Any]:
    url = params.get("url")
    if not url or not str(url).startswith(("http://", "https://")):
        raise ValueError("http_fetch needs an http(s) url")
    method = (params.get("method") or "GET").upper()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.request(
            method,
            url,
            headers=params.get("headers") or {},
            json=params.get("json"),
            params=params.get("params"),
        )
    body = response.text[:HTTP_MAX_BYTES]
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    return {"status": response.status_code, "body": body, "json": parsed}


@activity.defn(name="emit_event")
async def emit_event(params: dict[str, Any]) -> dict[str, Any]:
    """Progress goes to the backend, which is what the clients are watching."""
    info = activity.info()
    payload = {
        "scope": "workflow",
        "kind": params.get("kind") or "workflow.event",
        "payload": {"workflow_id": info.workflow_id, **(params.get("payload") or {})},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"{BACKEND_URL}/internal/events", json=payload, headers=_headers())
    except Exception as exc:  # noqa: BLE001 - never fail a run because a notice did not land
        log.warning("emit_event failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True}


def _artifact_path(name: str) -> Path:
    safe = os.path.basename(str(name)).strip() or "artifact.txt"
    return ARTIFACTS_DIR / safe


@activity.defn(name="save_artifact")
async def save_artifact(params: dict[str, Any]) -> dict[str, Any]:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _artifact_path(params.get("name", "artifact.txt"))
    path.write_text(str(params.get("content", "")), encoding="utf-8")
    return {"path": str(path), "name": path.name, "bytes": path.stat().st_size}


@activity.defn(name="read_artifact")
async def read_artifact(params: dict[str, Any]) -> dict[str, Any]:
    path = _artifact_path(params.get("name", ""))
    if not path.is_file():
        raise FileNotFoundError(f"artifact '{path.name}' does not exist")
    return {"name": path.name, "content": path.read_text(encoding="utf-8", errors="replace")}


ALL = [agent_call, http_fetch, emit_event, save_artifact, read_artifact]
