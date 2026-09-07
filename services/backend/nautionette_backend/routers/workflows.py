"""Workflows, the drafts waiting for approval, and their schedules."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from nautionette import input_problems

from ..background import spawn
from ..clients import authoring, temporal
from ..db import db
from ..deployment import WorkflowSource, deploy
from ..events import bus
from ..runs import restart_worker
from ..schedules import ScheduleRequest, temporal_spec
from ..security import require_user
from ..workflow_graph import definition_graph

router = APIRouter(dependencies=[Depends(require_user)])

CHAT_MODES = {"same", "new"}


async def _schedules() -> list[dict[str, Any]]:
    try:
        return await temporal.schedules()
    except Exception:  # noqa: BLE001 - schedules are extra, not essential
        return []


async def _schedule(workflow: str) -> dict[str, Any] | None:
    try:
        return await temporal.schedule(workflow)
    except Exception:  # noqa: BLE001 - a schedule is optional workflow metadata
        return None


@router.get("/api/workflows")
async def list_workflows() -> dict[str, Any]:
    workflows = await authoring.list_workflows()
    by_workflow = {item["workflow"]: item for item in await _schedules()}
    for workflow in workflows:
        workflow["schedule"] = by_workflow.get(workflow["name"])
        workflow["settings"] = db.workflow_settings(workflow["name"])
    return {"workflows": workflows}


@router.get("/api/workflows/{name}")
async def get_workflow(name: str) -> dict[str, Any]:
    workflow = await authoring.get_workflow(name)
    workflow["graph"] = definition_graph(workflow.get("code", ""), name)
    workflow["runs"] = db.list_runs(name, limit=25)
    workflow["settings"] = db.workflow_settings(name)
    workflow["schedule"] = await _schedule(name)
    return workflow


@router.patch("/api/workflows/{name}/settings")
async def patch_workflow_settings(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "disabled" in payload:
        fields["disabled"] = bool(payload["disabled"])
    if "chat_mode" in payload:
        if payload["chat_mode"] not in CHAT_MODES:
            raise HTTPException(status_code=400, detail="chat_mode must be 'same' or 'new'")
        fields["chat_mode"] = payload["chat_mode"]
    updated = db.set_workflow_settings(name, fields)
    bus.publish("workflow.settings", {"workflow": name, **fields})
    return updated


@router.post("/api/workflows/{name}/deploy")
async def deploy_workflow(name: str, payload: WorkflowSource) -> dict[str, Any]:
    """Validate and deploy a complete Python workflow, then reload workers. No approval required."""
    return await deploy(name, payload.code, payload.message)


@router.delete("/api/workflows/{name}")
async def delete_workflow(name: str) -> dict[str, Any]:
    result = await authoring.delete_workflow(name)
    db.forget_workflow(name)
    bus.publish("workflow.deleted", {"workflow": name})
    spawn(restart_worker(), name=f"restart-worker-after-{name}")
    return result


@router.post("/api/workflows/validate")
async def validate_workflow(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return await authoring.validate(payload.get("name", ""), payload.get("code", ""))


@router.post("/api/workflows/{name}/schedule")
async def schedule_workflow(name: str, payload: ScheduleRequest) -> dict[str, Any]:
    """Schedule a workflow with a human recurrence and an explicit IANA timezone."""
    workflow = await authoring.get_workflow(name)
    manifest = workflow.get("manifest") or {}
    problems = input_problems(manifest.get("inputs"), payload.input)
    if problems:
        raise HTTPException(status_code=400, detail={"workflow": name, "input": problems})
    result = await temporal.set_schedule(
        name, temporal_spec(payload), payload.input,
        timeout_minutes=manifest.get("timeout_minutes", 30),
    )
    bus.publish(
        "workflow.scheduled",
        {"workflow": name, "frequency": payload.frequency, "timezone": payload.timezone},
    )
    return result


@router.delete("/api/workflows/{name}/schedule")
async def unschedule_workflow(name: str) -> dict[str, Any]:
    await temporal.delete_schedule(name)
    bus.publish("workflow.unscheduled", {"workflow": name})
    return {"ok": True}


# --------------------------------------------------------------------- drafts


@router.get("/api/drafts")
async def list_drafts() -> dict[str, Any]:
    return {"drafts": await authoring.list_drafts()}


@router.get("/api/drafts/{name}")
async def get_draft(name: str) -> dict[str, Any]:
    draft = await authoring.get_draft(name)
    draft["graph"] = definition_graph(draft.get("code", ""), name)
    draft["previous_graph"] = None
    if not draft.get("is_new", False):
        published = next((item for item in await authoring.list_workflows() if item["name"] == name), None)
        if published:
            previous = await authoring.get_workflow(name)
            draft["previous_graph"] = definition_graph(previous.get("code", ""), name)
    return draft


@router.post("/api/drafts/{name}/approve")
async def approve_draft(name: str) -> dict[str, Any]:
    published = await authoring.publish(name)
    bus.publish("workflow.published", {"workflow": name})
    published["worker_restart"] = await restart_worker()
    return published


@router.delete("/api/drafts/{name}")
async def discard_draft(name: str) -> dict[str, Any]:
    result = await authoring.discard(name)
    bus.publish("workflow.draft_discarded", {"workflow": name})
    return result
