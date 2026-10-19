"""Authenticated Settings → Storage controls, also available through backend MCP."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .. import output_maintenance, output_storage
from ..events import bus
from ..security import require_user

router = APIRouter(prefix="/api/settings/storage", dependencies=[Depends(require_user)])
OutputID = Annotated[str, Field(pattern="^[a-f0-9]{32}$")]


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    output_id: OutputID
    revision: int = Field(ge=1, le=64)


class CleanupSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revisions: list[Revision] | None = Field(default=None, max_length=500)
    output_ids: list[OutputID] | None = Field(default=None, max_length=500)
    older_than_days: int | None = Field(default=None, ge=0, le=36500)
    keep_latest: bool = False


class CleanupApply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_id: OutputID
    confirm: bool = Field(description="Must be true after reviewing the cleanup preview")


@router.get("")
def output_storage_status() -> dict[str, Any]:
    """Output usage, quotas, migration status and correctly labeled filesystem capacity. No credentials."""
    return output_storage.status()


@router.put("")
async def configure_output_storage(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Set output quotas and opt-in retention. Increasing a quota does not create physical disk space."""
    policy = await run_in_threadpool(output_storage.save_policy, payload)
    bus.publish("settings.changed", {})
    return {"policy": policy}


@router.get("/outputs")
def list_stored_outputs(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort: str = Query("largest", pattern="^(largest|oldest|newest)$"),
    query: str = Query("", max_length=200),
) -> dict[str, Any]:
    """Page through every stored output revision. Search by chat/title/filename; sort by size or age."""
    return output_maintenance.inventory(offset=offset, limit=limit, sort=sort, query=query)


@router.post("/cleanup/preview")
def preview_output_cleanup(payload: CleanupSelection) -> dict[str, Any]:
    """Preview exact revisions and bytes before deleting selected outputs or anything older than X days."""
    return output_maintenance.preview_cleanup(payload.model_dump(exclude_none=True))


@router.post("/cleanup")
async def delete_stored_outputs(payload: CleanupApply) -> dict[str, Any]:
    """Apply a reviewed cleanup plan. Newer publications are never added to an existing plan."""
    from fastapi import HTTPException

    if not payload.confirm:
        raise HTTPException(422, "Review the cleanup preview and set confirm=true")
    result = await run_in_threadpool(output_maintenance.apply_cleanup, payload.plan_id)
    for chat_id in result["chat_ids"]:
        bus.publish("chat.output.deleted", {"chat_id": chat_id})
    return result


@router.post("/maintenance")
async def run_output_storage_maintenance() -> dict[str, Any]:
    """Retry verified legacy migration, enabled retention and queued S3 deletions. Errors remain visible."""
    result = await run_in_threadpool(output_maintenance.maintain)
    for chat_id in result["chat_ids"]:
        bus.publish("chat.output.deleted", {"chat_id": chat_id})
    return result


@router.post("/compact")
def compact_output_database() -> dict[str, bool]:
    """Reclaim SQLite pages freed by migration.

    Requires temporary free space; may briefly block metadata writes.
    """
    return output_maintenance.compact_database()
