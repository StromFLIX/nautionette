"""Authenticated mini-app authoring and app-scoped JSON requests."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from .. import miniapps
from ..clients import broker
from ..db import db
from ..events import bus
from ..miniapp_models import AppRequest, EditApp, Manifest, Publication, Restore
from ..security import require_internal, require_user

router = APIRouter(tags=["mini-apps"])
user = [Depends(require_user)]


async def exclusive_write(app_id: str):
    with miniapps.exclusive(app_id):
        yield


writer = [*user, Depends(exclusive_write)]


@router.get("/api/miniapps/contract", dependencies=user)
def miniapp_contract() -> dict:
    """Start here: publish HTML/CSS/JS plus an optional locked Rust/SQLite module.

    No Dockerfile, host token or workflow required.
    """
    return {
        "version": 1,
        "publishing": (
            "PUT /api/miniapps/{app_id} with expected_revision=0 to create; read before updating. "
            "IDs are lowercase slugs. Keep at most five recent release sources plus the active release. "
            "Failed builds never replace working code/data. Poll GET status after a lost reply; "
            "do not blindly publish again."
        ),
        "frontend": (
            "Provide self-contained html (body markup), css and js strings. Inline scripts also work. "
            "No external scripts/styles/assets/network/storage/popups/forms/frames. "
            "Embed images/fonts as data URLs. Use nautionette.ready and "
            "nautionette.request('/state', {method:'GET'}); mutations use POST "
            "and optional retained request_id. "
            "Request results are JSON values. Listen for nautionette-theme for theme updates. "
            "Never request the instance bearer token."
        ),
        "backend": (
            "Publish an app.rs module, not a Cargo project. SDK reexports Context, Request, Result, Value, "
            "json and rusqlite. Implement pub fn health(ctx: &mut Context) -> Result<()> and "
            "pub fn handle(ctx: &mut Context, req: &Request) -> Result<Value>. "
            "ctx.db is a durable rusqlite::Connection. req has method,path,body,request_id. "
            "health performs local initialization, not remote mutations. Dependencies are fixed/locked: "
            "std, serde, serde_json and bundled rusqlite. Builds are offline, unprivileged and time-limited. "
            "Final images are FROM scratch. Runtime has no direct external/gateway network access."
        ),
        "capabilities": (
            "ctx.mcp(exact_tool_name, arguments_json, request_id) and "
            "ctx.http(https_url, method, json_body, request_id). "
            "Grant exact MCP tool names and fixed resource argument subsets, or exact public HTTPS origins, "
            "path prefixes and methods. Empty grants mean no access. "
            "Host administration/credential tools are denied. MCP calls reuse sessions. "
            "There are no automatic tool-call retries. GET/readOnlyHint calls don't need IDs; mutations do: "
            "13-digit Unix milliseconds + '-' + 32 lowercase hex digits. "
            "Generate each ID once and retain it. "
            "The bridge generates IDs automatically for app mutations. Uncertain outcomes are fenced, "
            "not replayed; inspect current state before a new operation."
        ),
        "limits": {
            "apps": 16,
            "source_bytes": 65536,
            "release_bytes": 327680,
            "response_bytes": 65536,
            "runtime_memory_mb": [64, 128, 256],
            "cpu": 0.25,
            "persistent_bytes": 50331648,
            "persistent_files": (
                "state.db, state.db-wal, state.db-shm only; each hard-limited to 16 MiB. "
                "SQLite main DB is capped at 12 MiB. No arbitrary persistent files."
            ),
            "temporary_bytes": 8388608,
            "idle_suspend_seconds": 300,
            "mutation_window_hours": 24,
            "instance_mutation_records": 4096,
        },
        "lifecycle": (
            "on-demand suspends after five idle minutes; background stays running. "
            "Archive revokes capabilities and stops the container without deleting data. "
            "Delete requires archive and removes data. Restore rebuilds old code as a NEW revision "
            "against a copy of CURRENT data: use backward-compatible migrations. "
            "Both data files and source metadata must be included in operator backups. "
            "Resources/measurements appear in /status."
        ),
        "manifest_schema": Manifest.model_json_schema(),
        "example": {
            "title": "Persistent counter",
            "frontend": {
                "html": "<h1>Counter</h1><p id='count'>Loading…</p><button id='increment'>Increment</button>",
                "css": (
                    "body{font-family:system-ui;background:var(--surface-app);color:var(--text);padding:24px}"
                    "button{padding:12px}"
                ),
                "js": (
                    "nautionette.ready.then(async()=>{ "
                    "const render=v=>document.querySelector('#count').textContent=v.value; "
                    "render(await nautionette.request('/state')); "
                    "document.querySelector('#increment').onclick=async()=>"
                    "render(await nautionette.request('/increment',{method:'POST'})); });"
                ),
            },
            "backend": {
                "source": """use crate::{Context,Request,Result,Value,json};
pub fn health(ctx:&mut Context)->Result<()> {
    ctx.db.execute_batch("CREATE TABLE IF NOT EXISTS counter(
        id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL);
        INSERT OR IGNORE INTO counter VALUES(1,0);")?;
    Ok(())
}
pub fn handle(ctx:&mut Context,req:&Request)->Result<Value> {
    if req.method=="POST" && req.path=="/increment" {
        ctx.db.execute("UPDATE counter SET value=value+1 WHERE id=1",[])?;
    } else if req.method!="GET" || req.path!="/state" {return Err("Unknown operation".into());}
    let n:i64=ctx.db.query_row("SELECT value FROM counter WHERE id=1",[],|r|r.get(0))?;
    Ok(json!({"value":n}))
}"""
            },
        },
    }


@router.get("/api/miniapps", dependencies=user)
def list_miniapps() -> dict:
    return {"apps": miniapps.list_apps()}


@router.get("/api/miniapps/{app_id}", dependencies=user)
def read_miniapp(app_id: str, response: Response) -> dict:
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        }
    )
    return miniapps.details(app_id)


@router.get("/api/miniapps/{app_id}/releases/{revision}", dependencies=user)
def read_miniapp_release(app_id: str, revision: int, response: Response) -> dict:
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        }
    )
    return miniapps.release(app_id, revision)


@router.put("/api/miniapps/{app_id}", dependencies=writer)
async def publish_miniapp(app_id: str, body: Publication) -> dict:
    """Publish HTML/CSS/JS plus optional Rust SDK source; expected_revision prevents overwrites.

    Read miniapp_contract first. Only a health-checked release becomes active;
    inspect status on lost responses.
    """
    return await miniapps.publish(app_id, body)


@router.post("/api/miniapps/{app_id}/restore", dependencies=writer)
async def restore_miniapp(app_id: str, body: Restore) -> dict:
    """Rebuild retained source as a new release, preserving current app data.

    Failed candidates never modify the previous release's data.
    """
    manifest = miniapps.release(app_id, body.revision)["manifest"]
    if manifest["agent_id"] and not db.query(
        "SELECT 1 FROM agent_profiles WHERE id=?", (manifest["agent_id"],)
    ):
        manifest["agent_id"] = None  # Restore code, not a deleted ownership profile.
    return await miniapps.publish(
        app_id,
        Publication(expected_revision=body.expected_revision, manifest=Manifest.model_validate(manifest)),
    )


@router.get("/api/miniapps/{app_id}/status", dependencies=user)
async def miniapp_status(app_id: str) -> dict:
    item = miniapps.app(app_id)
    if (
        not item["active_revision"]
        or not miniapps.release(app_id, item["active_revision"])["manifest"]["backend"]
    ):
        return {"app": item, "runtime": {"status": "frontend-only"}}
    try:
        status = await broker.miniapp(app_id)
    except Exception as exc:
        raise HTTPException(503, "Mini-app runtime status unavailable") from exc
    return {"app": item, "runtime": status}


def pending(app_id: str):
    if db.query("SELECT 1 FROM mini_app_releases WHERE app_id=? AND state='building'", (app_id,)):
        raise HTTPException(
            409, "Wait for the pending publication to resolve before changing lifecycle state"
        )


@router.patch("/api/miniapps/{app_id}", dependencies=writer)
async def edit_miniapp(app_id: str, body: EditApp) -> dict:
    item = miniapps.app(app_id)
    if body.expected_revision != item["revision"]:
        raise HTTPException(409, "Revision changed; read before editing")
    pending(app_id)
    if body.archived is not None:
        # Revoke server grants before contacting the broker. Failed suspension is
        # visibly an error, but cannot leave a valid capability behind.
        db.execute("UPDATE mini_apps SET archived=? WHERE id=?", (int(body.archived), app_id))
        await capabilities_close(app_id)
        if (
            item["active_revision"]
            and miniapps.release(app_id, item["active_revision"])["manifest"]["backend"]
        ):
            try:
                await broker.miniapp(app_id, "control", {"action": "suspend" if body.archived else "resume"})
            except Exception as exc:
                raise HTTPException(503, "Lifecycle update incomplete; retry the same edit") from exc
    if body.pinned is not None:
        db.execute("UPDATE mini_apps SET pinned=? WHERE id=?", (int(body.pinned), app_id))
    bus.publish("miniapp.changed", {"app_id": app_id})
    return miniapps.details(app_id)


async def capabilities_close(app_id: str):
    await miniapps.capabilities.close(app_id)


@router.post("/api/miniapps/{app_id}/control", dependencies=writer)
async def control_miniapp(app_id: str, action: Literal["restart"] = Body(embed=True)) -> dict:
    """Restart the active app without deleting SQLite state."""
    item = miniapps.app(app_id)
    pending(app_id)
    if item["archived"]:
        raise HTTPException(409, "Unarchive this app first")
    try:
        return await broker.miniapp(app_id, "control", {"action": action})
    except Exception as exc:
        raise HTTPException(503, "Mini-app restart unavailable; inspect status") from exc


@router.delete("/api/miniapps/{app_id}", dependencies=writer)
async def delete_miniapp(app_id: str, expected_revision: int) -> dict:
    """Permanently remove an archived app, including SQLite data and source. Archive it first."""
    item = miniapps.app(app_id)
    pending(app_id)
    if not item["archived"] or item["revision"] != expected_revision:
        raise HTTPException(409, "Archive the app and provide its current revision before deletion")
    try:
        await broker.miniapp(app_id, "remove")
    except Exception as exc:
        raise HTTPException(503, "Cleanup incomplete; app remains archived, retry this removal") from exc
    await capabilities_close(app_id)
    db.execute("DELETE FROM mini_apps WHERE id=?", (app_id,))
    bus.publish("miniapp.changed", {"app_id": app_id})
    return {"removed": True}


@router.post("/api/miniapps/{app_id}/invoke", dependencies=user)
@router.post("/api/miniapps/{app_id}/request", dependencies=user, include_in_schema=False)
async def invoke_miniapp(app_id: str, body: AppRequest):
    """Test a published app operation. GET is read-only; mutations need a fresh timestamp-UUID request_id.

    Reuse the same ID when checking a lost result. Uncertain operations are fenced, not repeated.
    App buttons use the same scoped dispatcher through their isolated bridge.
    """
    return await miniapps.request(app_id, body)


@router.post(
    "/internal/miniapps/{app_id}/capability",
    dependencies=[Depends(require_internal)],
    include_in_schema=False,
)
async def miniapp_capability(app_id: str, body: dict):
    if (
        set(body) != {"revision", "request"}
        or type(body["revision"]) is not int
        or not isinstance(body["request"], dict)
    ):
        raise HTTPException(422, "Invalid capability envelope")
    import asyncio

    try:
        async with asyncio.timeout(22):
            return await miniapps.capability(app_id, body["revision"], body["request"])
    except TimeoutError as exc:
        raise HTTPException(
            504, "Capability deadline exceeded; mutations are not automatically repeated"
        ) from exc
