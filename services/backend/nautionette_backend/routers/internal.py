"""What the other services are allowed to ask this one for."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from .. import provenance, run_statistics, runs, workflow_agents
from ..agent import agent_job, call_agent
from ..clients import temporal
from ..db import db
from ..deployment import WorkflowSource, deploy
from ..events import bus
from ..gateway_config import attempt
from ..runtime import remember_agent_result, runtime
from ..security import require_internal
from .workflows import delete_workflow

router = APIRouter(dependencies=[Depends(require_internal)])
log = logging.getLogger(__name__)


@router.post("/internal/agent/call")
async def internal_agent_call(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Activity mode. The worker asks, the backend runs one container, one object comes back."""
    job = agent_job(
        prompt=payload.get("prompt", ""),
        mode="activity",
        system_prompt=payload.get("system_prompt"),
        history=payload.get("history") or [],
        output_schema=payload.get("output_schema"),
        agent_set=payload.get("agent_set") or runtime("default_agent_set"),
        model=runtime("default_model"),
        run_id=payload.get("run_id", ""),
        timeout_seconds=int(payload.get("timeout_seconds") or 900),
    )
    if payload.get("source"):
        job["origin"] = await provenance.workflow_call(payload["source"])
        job["prompt"] = provenance.context(job["origin"]) + job["prompt"]
    bus.publish("agent.activity", {"run_id": job["run_id"], "agent_set": job["agent_set"]})
    call_id = uuid.uuid4().hex
    accounting = run_statistics.AgentStatistics()

    def save(status: str = "running") -> None:
        try:
            run_statistics.record_call(
                job["run_id"],
                call_id,
                "agent",
                job["agent_set"],
                status,
                model=job["model"],
                statistics=accounting.snapshot(),
            )
        except Exception:  # noqa: BLE001 - statistics must never replay an agent's side effects
            log.warning("Could not record agent statistics for run %s", job["run_id"])

    def observe(event: dict[str, Any]) -> None:
        if accounting.observe(event):
            save()

    save()
    status = "interrupted"
    try:
        result = await call_agent(job, on_event=observe)
        status = "completed" if result.get("ok") else "failed"
        remember_agent_result(bool(result.get("ok")))
        return result
    finally:
        save(status)


class WorkflowCaller(BaseModel):
    workflow: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    workflow_id: str = Field(min_length=1, max_length=512)
    run_id: str = Field(min_length=1, max_length=128)
    activity_id: str = Field(min_length=1, max_length=128)


class ProfileAgentTask(BaseModel):
    source: WorkflowCaller | None = None
    agent_id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=100000)
    validate_only: bool = False


@router.post("/internal/agent/tasks/{key}")
async def start_profile_agent_task(key: str, payload: ProfileAgentTask) -> dict[str, Any]:
    """Idempotently admit a saved-profile task through the normal chat pipeline."""
    return await workflow_agents.start(
        key,
        payload.agent_id,
        payload.prompt,
        validate_only=payload.validate_only,
        source=payload.source.model_dump() if payload.source else None,
    )


@router.get("/internal/agent/tasks/{key}")
async def profile_agent_task_status(key: str) -> dict[str, Any]:
    return workflow_agents.status(key)


@router.delete("/internal/agent/tasks/{key}")
async def stop_profile_agent_task(key: str) -> dict[str, Any]:
    return await workflow_agents.stop(key)


class McpCallObservation(BaseModel):
    call_id: str = Field(min_length=1, max_length=512)
    tool: str = Field(min_length=1, max_length=512)
    status: Literal["running", "completed", "failed", "interrupted"]


@router.post("/internal/runs/{workflow_id}/mcp-calls")
async def record_mcp_call(workflow_id: str, payload: McpCallObservation) -> dict[str, bool]:
    """Idempotent observations only; never persist tool arguments or results."""
    run_statistics.record_call(
        workflow_id,
        f"mcp:{payload.call_id}",
        "mcp",
        payload.tool,
        payload.status,
    )
    # The visible Statistics tab polls; broadcasting every start/end would
    # trigger a Temporal describe per tool in every connected run view.
    return {"ok": True}


@router.post("/internal/events")
async def internal_event(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    bus.publish(
        payload.get("kind", "worker.event"),
        payload.get("payload") or {},
        scope=payload.get("scope", "worker"),
    )
    return {"ok": True}


@router.post("/internal/worker/restart")
async def internal_worker_restart() -> dict[str, Any]:
    return await runs.restart_worker()


@router.post("/internal/workflows/{name}/deploy")
async def internal_deploy_workflow(name: str, payload: WorkflowSource) -> dict[str, Any]:
    return await deploy(name, payload.code, payload.message)


@router.post("/internal/workflows/{name}/delete")
async def internal_delete_workflow(name: str) -> dict[str, Any]:
    """Use the same schedule/settings/worker cleanup as deletion from the app."""
    return await delete_workflow(name)


@router.post("/internal/workflows/{name}/run")
async def internal_run_workflow(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await runs.start(name, payload.get("input") or {}, trigger="agent")


@router.get("/internal/runs")
async def internal_runs(workflow: str | None = None, limit: int = 20) -> dict[str, Any]:
    """The run history an authoring agent reads before it changes anything."""
    live = {item["workflow_id"]: item for item in await attempt(temporal.recent(50), [])}
    rows = db.list_runs(workflow, limit=max(1, min(limit, 100)))
    return {"runs": [runs.digest(row, live.get(row["workflow_id"])) for row in rows]}


@router.get("/internal/runs/{workflow_id}")
async def internal_run(workflow_id: str, limit: int = 200) -> dict[str, Any]:
    row, info = await runs.describe_run(workflow_id)
    events, history_status = await runs.retained_history(workflow_id, info, max(1, min(limit, 2000)))
    detail = {
        **runs.digest(row, info),
        "input": (row or {}).get("input") or {},
        "result": (row or {}).get("result"),
        "events": events,
        "history_status": history_status,
        "statistics": run_statistics.statistics(workflow_id, row),
    }
    if info and info.get("status") == "COMPLETED" and detail["result"] is None:
        detail["result"] = await attempt(temporal.result(workflow_id, timeout=5), None)
    if history_status == "expired":
        detail["note"] = runs.HISTORY_EXPIRED_NOTE
    elif not events:
        # An empty timeline is not the same as an expired or unavailable run.
        detail["note"] = "Temporal has no history events for this run yet."
    return detail
