"""Workflows, the drafts waiting for approval, and their schedules."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..clients import authoring, temporal
from ..db import db
from ..events import bus
from ..runs import restart_worker
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])

CHAT_MODES = {"same", "new"}


async def _schedules() -> list[dict[str, Any]]:
    try:
        return await temporal.schedules()
    except Exception:  # noqa: BLE001 - schedules are extra, not essential
        return []


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
    workflow["runs"] = db.list_runs(name, limit=25)
    workflow["settings"] = db.workflow_settings(name)
    workflow["schedule"] = next(
        (item for item in await _schedules() if item["workflow"] == name), None
    )
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


@router.delete("/api/workflows/{name}")
async def delete_workflow(name: str) -> dict[str, Any]:
    result = await authoring.delete_workflow(name)
    db.forget_workflow(name)
    bus.publish("workflow.deleted", {"workflow": name})
    asyncio.create_task(restart_worker())
    return result


@router.post("/api/workflows/validate")
async def validate_workflow(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return await authoring.validate(payload.get("name", ""), payload.get("code", ""))


@router.post("/api/workflows/{name}/schedule")
async def schedule_workflow(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    cron = (payload.get("cron") or "").strip()
    if not cron:
        raise HTTPException(status_code=400, detail="cron is required, e.g. '0 8 * * *'")
    result = await temporal.set_schedule(name, cron, payload.get("input") or {})
    bus.publish("workflow.scheduled", {"workflow": name, "cron": cron})
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
    return await authoring.get_draft(name)


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
