"""Runs, and the triggers that start them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from .. import runs
from ..clients import temporal
from ..config import settings
from ..db import db
from ..events import bus
from ..security import require_user, token_matches

router = APIRouter()


@router.post("/api/workflows/{name}/run", dependencies=[Depends(require_user)])
async def run_workflow(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await runs.start(name, payload.get("input") or {}, trigger="manual")


@router.get("/api/runs", dependencies=[Depends(require_user)])
async def list_runs(workflow: str | None = None) -> dict[str, Any]:
    local = db.list_runs(workflow)
    try:
        remote = await temporal.recent(50)
    except Exception:  # noqa: BLE001
        remote = []
    by_id = {item["workflow_id"]: item for item in remote}
    for run in local:
        live = by_id.get(run["workflow_id"])
        if live:
            run["status"] = live["status"].lower()
            run["start_time"] = live["start_time"]
            run["close_time"] = live["close_time"]
    return {"runs": local, "temporal": remote}


@router.get("/api/runs/{workflow_id}", dependencies=[Depends(require_user)])
async def get_run(workflow_id: str) -> dict[str, Any]:
    info = await temporal.describe(workflow_id)
    row = runs.stored_run(workflow_id)
    if info["status"] == "COMPLETED" and (not row or row.get("result") is None):
        try:
            info["result"] = await temporal.result(workflow_id, timeout=10)
        except Exception:  # noqa: BLE001
            info["result"] = None
    return {"run": row, "temporal": info}


@router.post("/api/runs/{workflow_id}/cancel", dependencies=[Depends(require_user)])
async def cancel_run(workflow_id: str) -> dict[str, Any]:
    await temporal.cancel(workflow_id)
    return {"ok": True}


@router.post("/api/runs/{workflow_id}/terminate", dependencies=[Depends(require_user)])
async def terminate_run(
    workflow_id: str, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """For a run that cannot be asked nicely, because its worker cannot load it."""
    await temporal.terminate(workflow_id, payload.get("reason") or "terminated from the app")
    db.update_run(workflow_id, "terminated")
    bus.publish("run.terminated", {"workflow_id": workflow_id})
    return {"ok": True}


@router.post("/api/triggers/{name}")
async def trigger(
    name: str, request: Request, token: str | None = Query(default=None)
) -> dict[str, Any]:
    """Triggers come in here and nowhere else."""
    if settings.auth_enabled:
        header = request.headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else token
        if not token_matches(supplied, settings.app_token):
            raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a webhook may send nothing at all
        payload = {}
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    return await runs.start(name, payload, trigger="webhook")
