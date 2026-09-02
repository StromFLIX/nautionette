"""What the other services are allowed to ask this one for."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import runs
from ..agent import agent_job, call_agent
from ..clients import temporal
from ..db import db
from ..events import bus
from ..gateway_config import attempt
from ..runtime import remember_agent_result
from ..security import require_internal

router = APIRouter(dependencies=[Depends(require_internal)])


@router.post("/internal/agent/call")
async def internal_agent_call(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Activity mode. The worker asks, the backend runs one container, one object comes back."""
    job = agent_job(
        prompt=payload.get("prompt", ""),
        mode="activity",
        system_prompt=payload.get("system_prompt"),
        history=payload.get("history") or [],
        output_schema=payload.get("output_schema"),
        agent_set=payload.get("agent_set"),
        run_id=payload.get("run_id", ""),
        timeout_seconds=int(payload.get("timeout_seconds") or 900),
    )
    bus.publish("agent.activity", {"run_id": job["run_id"], "agent_set": job["agent_set"]})
    result = await call_agent(job)
    remember_agent_result(bool(result.get("ok")))
    return result


@router.post("/internal/events")
async def internal_event(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    kind = payload.get("kind", "worker.event")
    body = payload.get("payload") or {}
    db.add_event(payload.get("scope", "worker"), kind, body)
    bus.publish(kind, body)
    return {"ok": True}


@router.post("/internal/worker/restart")
async def internal_worker_restart() -> dict[str, Any]:
    return await runs.restart_worker()


@router.get("/internal/runs")
async def internal_runs(workflow: str | None = None, limit: int = 20) -> dict[str, Any]:
    """The run history an authoring agent reads before it changes anything."""
    live = {item["workflow_id"]: item for item in await attempt(temporal.recent(50), [])}
    rows = db.list_runs(workflow, limit=max(1, min(limit, 100)))
    return {"runs": [runs.digest(row, live.get(row["workflow_id"])) for row in rows]}


@router.get("/internal/runs/{workflow_id}")
async def internal_run(workflow_id: str, limit: int = 200) -> dict[str, Any]:
    row = runs.stored_run(workflow_id)
    info = await attempt(temporal.describe(workflow_id), None)
    if not row and not info:
        raise HTTPException(status_code=404, detail=f"no run with id {workflow_id}")
    detail = {
        **runs.digest(row, info),
        "input": (row or {}).get("input") or {},
        "result": (row or {}).get("result"),
        "events": await attempt(temporal.history(workflow_id, limit), []),
    }
    if not detail["events"]:
        # An empty timeline is not the same as a run that did nothing.
        detail["note"] = "Temporal has no history for this run; only what the app recorded is left."
    return detail
