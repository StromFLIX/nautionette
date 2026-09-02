"""The REST face the backend calls, so approval stays a human action."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from . import backend, store
from .validate import run_checks

router = APIRouter(prefix="/api")


def _checked_name(name: str, status: int = 400) -> str:
    try:
        return store.check_name(name)
    except store.StoreError as exc:
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _found(call, name: str) -> dict[str, Any]:
    try:
        return call(name)
    except store.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/schema")
async def api_schema() -> dict[str, Any]:
    from nautionette.manifest import MANIFEST_SCHEMA, SCHEMA_VERSION

    return {"schema_version": SCHEMA_VERSION, "manifest_schema": MANIFEST_SCHEMA}


@router.get("/workflows")
async def list_workflows() -> dict[str, Any]:
    return {"workflows": store.list_workflows()}


@router.get("/workflows/{name}")
async def read_workflow(name: str) -> dict[str, Any]:
    return _found(store.read_workflow, name)


@router.delete("/workflows/{name}")
async def delete_workflow(name: str) -> dict[str, Any]:
    result = _found(store.delete_workflow, name)
    result["worker_restart"] = await backend.request_worker_restart(f"deleted {name}")
    return result


@router.post("/validate")
async def validate(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = payload.get("name", "")
    try:
        store.check_name(name)
    except store.StoreError as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": [], "manifest": None, "steps": []}
    return run_checks(name, payload.get("code", ""))


@router.get("/drafts")
async def list_drafts() -> dict[str, Any]:
    return {"drafts": store.list_drafts()}


@router.get("/drafts/{name}")
async def read_draft(name: str) -> dict[str, Any]:
    return _found(store.read_draft, name)


@router.post("/drafts")
async def write_draft(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    name = _checked_name(payload.get("name", ""))
    code = payload.get("code", "")
    draft = store.write_draft(name, code, payload.get("message", ""))
    draft["validation"] = run_checks(name, code)
    return JSONResponse(draft)


@router.post("/drafts/{name}/publish")
async def publish(name: str) -> dict[str, Any]:
    draft = _found(store.read_draft, name)
    report = run_checks(name, draft["code"])
    if not report["valid"]:
        raise HTTPException(
            status_code=400, detail=f"draft does not validate: {'; '.join(report['errors'])}"
        )
    result = store.publish_draft(name)
    result["validation"] = report
    return result


@router.delete("/drafts/{name}")
async def discard(name: str) -> dict[str, Any]:
    return store.discard_draft(name)
