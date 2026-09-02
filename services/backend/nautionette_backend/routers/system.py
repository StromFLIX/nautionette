"""Health, the status page and the event feed behind it."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..clients import authoring, broker, gateway, temporal
from ..config import settings
from ..events import bus
from ..runtime import agent_has_answered
from ..security import require_user

router = APIRouter()

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": settings.version}


@router.get("/api/system", dependencies=[Depends(require_user)])
async def system_status() -> dict[str, Any]:
    async def safe(coro, name: str) -> dict[str, Any]:
        try:
            return {"name": name, "status": "ok", "detail": await coro}
        except Exception as exc:  # noqa: BLE001 - status page must never 500
            return {"name": name, "status": "down", "detail": str(exc)[:200]}

    temporal_ok, broker_state, gateway_state, authoring_state = await asyncio.gather(
        temporal.healthy(),
        safe(broker.health(), "broker"),
        safe(gateway.health(), "agentgateway"),
        safe(authoring.health(), "workflow-mcp"),
    )
    agent_sets: list[dict[str, Any]] = []
    if broker_state["status"] == "ok":
        try:
            agent_sets = await broker.agent_sets()
        except Exception:  # noqa: BLE001
            agent_sets = []
    return {
        "version": settings.version,
        "auth_enabled": settings.auth_enabled,
        "model": settings.agent_model,
        "model_key_present": settings.model_key_present or agent_has_answered(),
        "components": [
            {
                "name": "temporal",
                "status": "ok" if temporal_ok else "down",
                "detail": temporal.last_error or settings.temporal_address,
            },
            broker_state,
            gateway_state,
            authoring_state,
        ],
        "agent_sets": agent_sets,
    }


@router.get("/api/events", dependencies=[Depends(require_user)])
async def events_stream() -> StreamingResponse:
    return StreamingResponse(bus.stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/api/events/recent", dependencies=[Depends(require_user)])
async def events_recent() -> dict[str, Any]:
    return {"events": bus.history()[-100:]}
