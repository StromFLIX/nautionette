"""Authenticated mini-app authoring and app-scoped JSON requests."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Literal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Response

from .. import miniapps
from ..clients import broker
from ..db import db
from ..events import bus
from ..miniapp_models import WORKFLOW_TOOLS, AppRequest, EditApp, Manifest, Publication, Restore, ValidateApp
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
        "version": 3,
        "workflow_tools": {tool: {"required_fixed_argument": key} for tool, key in WORKFLOW_TOOLS.items()},
        "authoring": (
            "Set agent_id to the owning saved agent. icon is a Material icon name; image is an optional "
            "inline PNG/JPEG/WebP avatar (32 KiB maximum). Apps are not pinned by default. "
            "Pin the active release via PATCH with pinned=true and pin_revision=active_revision. "
            "The five most recently pinned active apps appear above chats. Publishing without pinned=true "
            "unpins the old version; failed releases never change pins. Restore with pinned=true to use "
            "and pin retained source as a new release."
        ),
        "debugging": (
            "POST /api/miniapps/validate with {manifest,compile:false} checks a draft without publishing. "
            "compile:true runs the exact offline Rust compiler, returning artifact size or compiler errors; "
            "Creates no runtime, release, data or image. Does NOT run health or grant network access. "
            "GET /api/miniapps/{id}/diagnostics returns recent bounded operation timings, outcomes, mutation "
            "IDs/states and build errors, never payloads/results/credentials. GET .../releases/{revision} "
            "retrieves retained source. Use /invoke for a targeted operation test; mutations may be real. "
            "Read-only status/overview do not wake suspended apps."
        ),
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
            "Host administration/credential tools are denied. The only workflow exceptions are "
            "workflows_run_workflow (fixed name) and workflows_list_runs (fixed workflow). "
            "These require an exact nonempty workflow name in the grant; editing/deploying workflows "
            "and arbitrary run inspection remain denied. Workflow starts are mutations: retain IDs "
            "and inspect run history after uncertain outcomes instead of retrying. MCP calls reuse sessions. "
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
            "icon": "exposure_plus_1",
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


@router.post("/api/miniapps/validate", dependencies=user)
async def validate_miniapp(body: ValidateApp) -> dict:
    """Check a draft, optionally compiling Rust offline, without publishing or touching app data.

    Compilation validates syntax/types/SDK use, not health, migrations or upstream permissions.
    """
    if body.manifest.agent_id and not db.query(
        "SELECT 1 FROM agent_profiles WHERE id=?", (body.manifest.agent_id,)
    ):
        raise HTTPException(422, "Unknown owning agent profile")
    result = {"valid": True, "compiled": False, "warnings": []}
    if not body.manifest.agent_id:
        result["warnings"].append("Set agent_id to group this app under its owning agent.")
    if body.compile and body.manifest.backend:
        try:
            result.update(
                await broker.miniapp("source-check", "check", {"source": body.manifest.backend.source})
            )
            result["compiled"] = True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 422:
                raise HTTPException(
                    422, str(exc.response.json().get("detail", "Compilation failed"))[:7000]
                ) from exc
            try:
                detail = exc.response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            if (
                isinstance(detail, dict)
                and detail.get("code") == "compiler_unavailable"
                and detail.get("stage") in {"docker", "image", "container", "artifact", "runtime_image"}
                and isinstance(detail.get("sdk_fingerprint"), str)
                and re.fullmatch(r"[0-9a-f]{12}", detail["sdk_fingerprint"])
            ):
                raise HTTPException(
                    503,
                    {
                        **{k: detail[k] for k in ("code", "stage", "sdk_fingerprint")},
                        "message": (
                            "Mini-app compiler unavailable; check the published SDK image and broker health"
                        ),
                    },
                ) from exc
            raise HTTPException(503, "Mini-app compiler unavailable") from exc
        except Exception as exc:
            raise HTTPException(503, "Mini-app compiler unavailable") from exc
    return result


async def runtime_status(item: dict) -> dict:
    if db.query("SELECT 1 FROM mini_app_releases WHERE app_id=? AND state='building'", (item["id"],)):
        return {"status": "building"}
    if not item["active_revision"]:
        return {"status": "absent"}
    if not miniapps.release(item["id"], item["active_revision"])["manifest"]["backend"]:
        return {"status": "frontend-only"}
    return await broker.miniapp(item["id"])


@router.get("/api/miniapps/overview", dependencies=user)
async def miniapp_overview() -> dict:
    """Read resource samples for every app. Does not start or resume runtimes.

    Unavailable/building apps remain visible; totals include measured samples only.
    """
    slots = asyncio.Semaphore(4)

    async def sample(item):
        async with slots:
            try:
                async with asyncio.timeout(8):
                    runtime = await runtime_status(item)
                if not isinstance(runtime, dict) or not isinstance(runtime.get("status"), str):
                    raise ValueError("Invalid runtime sample")
                return {"app": item, "runtime": runtime}
            except Exception:
                return {"app": item, "runtime": {"status": "unavailable"}}

    apps = await asyncio.gather(*(sample(item) for item in miniapps.list_apps()))
    return {
        "apps": apps,
        "sampled_at": time.time(),
        "totals": {
            key: sum(row["runtime"].get(key, 0) or 0 for row in apps)
            for key in ("memory_bytes", "data_bytes", "image_bytes")
        },
        "incomplete": any(row["runtime"]["status"] in {"unavailable", "building", "absent"} for row in apps),
    }


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
    manifest["pinned"] = body.pinned
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
    try:
        status = await runtime_status(item)
    except Exception as exc:
        raise HTTPException(503, "Mini-app runtime status unavailable") from exc
    return {"app": item, "runtime": status}


@router.get("/api/miniapps/{app_id}/diagnostics", dependencies=user)
def miniapp_diagnostics(app_id: str) -> dict:
    """Read build errors, last 100 operation timings and uncertain mutation IDs.

    No logs, request/response bodies, tool arguments, data values or credentials are returned.
    Does not wake the app or repeat any operation.
    """
    item = miniapps.app(app_id)
    return {
        "app_id": app_id,
        "active_revision": item["active_revision"],
        "releases": miniapps.details(app_id)["releases"],
        "operations": db.query(
            "SELECT revision,channel,operation,request_id,outcome,duration_ms,created_at "
            "FROM mini_app_events WHERE app_id=? ORDER BY id DESC LIMIT 100",
            (app_id,),
        ),
        "mutations": db.query(
            "SELECT revision,channel,request_id,state,created_at FROM mini_app_calls "
            "WHERE app_id=? ORDER BY created_at DESC LIMIT 100",
            (app_id,),
        ),
    }


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
    if body.pinned:
        if item["archived"] or body.archived is True or not item["active_revision"]:
            raise HTTPException(409, "Only a healthy, unarchived release can be pinned")
        if body.pin_revision != item["active_revision"]:
            raise HTTPException(409, "Set pin_revision to the active release; read before pinning")
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
        db.execute(
            "UPDATE mini_apps SET pinned=?,pinned_revision=?,pinned_at=? WHERE id=?",
            (
                int(body.pinned),
                item["active_revision"] if body.pinned else None,
                time.time() if body.pinned else 0,
                app_id,
            ),
        )
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
    try:
        async with asyncio.timeout(22):
            return await miniapps.capability(app_id, body["revision"], body["request"])
    except TimeoutError as exc:
        raise HTTPException(
            504, "Capability deadline exceeded; mutations are not automatically repeated"
        ) from exc
