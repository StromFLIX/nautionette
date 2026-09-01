"""The backend: the only service that publishes a port.

Auth, chats, workflows, triggers and the stream back to the clients all live
here. It never touches the Docker socket; the broker does that.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from .agent import (
    agent_job,
    build_history,
    call_agent,
    promote_chat,
    stream_agent,
    summarise_for_title,
)
from .clients import authoring, broker, gateway
from .config import settings
from .db import Database
from .events import bus, sse
from .temporal_gateway import temporal


def seed_workflows() -> None:
    """Copy committed workflows into the shared volume on first start."""
    source, target = Path(settings.seed_dir), Path(settings.workflows_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".drafts").mkdir(exist_ok=True)
    if not source.exists():
        return
    for item in source.glob("*.py"):
        destination = target / item.name
        if not destination.exists():
            shutil.copy2(item, destination)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.artifacts_dir).mkdir(parents=True, exist_ok=True)
    seed_workflows()
    bus.publish("system.start", {"version": settings.version})
    yield


app = FastAPI(
    title="Nautionette",
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
db = Database(os.path.join(settings.data_dir, "nautionette.db"))


# ----------------------------------------------------------------------- auth


def _token_matches(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied or "", expected)


async def require_user(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    x_auth_token: str | None = Header(default=None),
) -> None:
    if not settings.auth_enabled:
        return
    supplied = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    supplied = supplied or x_auth_token or token
    if not _token_matches(supplied, settings.app_token):
        raise HTTPException(status_code=401, detail="unauthorized")


async def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    if not settings.internal_token:
        return
    if not _token_matches(x_internal_token, settings.internal_token):
        raise HTTPException(status_code=401, detail="unauthorized")


# --------------------------------------------------------------------- health


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": settings.version}


@app.get("/api/system", dependencies=[Depends(require_user)])
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
        "model_key_present": settings.model_key_present,
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


@app.get("/api/events", dependencies=[Depends(require_user)])
async def events_stream() -> StreamingResponse:
    return StreamingResponse(
        bus.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/events/recent", dependencies=[Depends(require_user)])
async def events_recent() -> dict[str, Any]:
    return {"events": bus.history()[-100:]}


# ---------------------------------------------------------------------- chats


@app.get("/api/chats", dependencies=[Depends(require_user)])
async def list_chats() -> dict[str, Any]:
    return {"chats": db.list_chats()}


@app.post("/api/chats", dependencies=[Depends(require_user)])
async def create_chat(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    chat = db.create_chat(
        title=(payload.get("title") or "New chat").strip()[:120],
        agent_set=payload.get("agent_set") or settings.default_agent_set,
    )
    bus.publish("chat.created", {"chat_id": chat["id"], "title": chat["title"]})
    return chat


@app.get("/api/chats/{chat_id}", dependencies=[Depends(require_user)])
async def get_chat(chat_id: str) -> dict[str, Any]:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    return {"chat": chat, "messages": db.list_messages(chat_id)}


@app.delete("/api/chats/{chat_id}", dependencies=[Depends(require_user)])
async def delete_chat(chat_id: str) -> dict[str, Any]:
    db.delete_chat(chat_id)
    bus.publish("chat.deleted", {"chat_id": chat_id})
    return {"ok": True}


@app.post("/api/chats/{chat_id}/messages", dependencies=[Depends(require_user)])
async def send_message(chat_id: str, payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    history = build_history(db.list_messages(chat_id))
    user_message = db.add_message(chat_id, "user", text)
    if chat["title"] in {"New chat", ""} and not history:
        db.execute("UPDATE chats SET title = ? WHERE id = ?", (summarise_for_title(text), chat_id))

    job = agent_job(
        prompt=text,
        mode="interactive",
        history=history,
        agent_set=chat["agent_set"],
        run_id=f"chat-{chat_id}",
    )

    async def generator():
        yield sse({"type": "user_message", "message": user_message})
        collected: list[str] = []
        failure: str | None = None
        tools: list[str] = []
        try:
            async for event in stream_agent(job):
                kind = event.get("type")
                if kind == "delta":
                    collected.append(event.get("text", ""))
                elif kind == "tool":
                    tools.append(event.get("name", "?"))
                elif kind == "error":
                    failure = event.get("message")
                elif kind == "result" and not collected and event.get("text"):
                    collected.append(event["text"])
                yield sse(event)
        except Exception as exc:  # noqa: BLE001 - always close the stream cleanly
            failure = str(exc)
            yield sse({"type": "error", "message": failure})

        content = "".join(collected).strip()
        if not content and failure:
            content = f"The agent could not answer: {failure}"
        assistant = db.add_message(
            chat_id, "assistant", content or "(no answer)", {"tools": tools, "error": failure}
        )
        bus.publish("chat.answered", {"chat_id": chat_id, "ok": failure is None})
        yield sse({"type": "done", "message": assistant})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chats/{chat_id}/promote", dependencies=[Depends(require_user)])
async def promote(chat_id: str) -> dict[str, Any]:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    messages = db.list_messages(chat_id)
    if not messages:
        raise HTTPException(status_code=400, detail="nothing to promote yet")
    bus.publish("promote.start", {"chat_id": chat_id})
    draft = await promote_chat(chat, messages)
    db.execute("UPDATE chats SET promoted_to = ? WHERE id = ?", (draft["name"], chat_id))
    bus.publish("promote.draft", {"chat_id": chat_id, "workflow": draft["name"]})
    return draft


# ------------------------------------------------------------------ workflows


@app.get("/api/workflows", dependencies=[Depends(require_user)])
async def list_workflows() -> dict[str, Any]:
    workflows = await authoring.list_workflows()
    schedules: list[dict[str, Any]] = []
    try:
        schedules = await temporal.schedules()
    except Exception:  # noqa: BLE001 - schedules are extra, not essential
        schedules = []
    by_workflow = {item["workflow"]: item for item in schedules}
    for workflow in workflows:
        workflow["schedule"] = by_workflow.get(workflow["name"])
    return {"workflows": workflows}


@app.get("/api/workflows/{name}", dependencies=[Depends(require_user)])
async def get_workflow(name: str) -> dict[str, Any]:
    workflow = await authoring.get_workflow(name)
    workflow["runs"] = db.list_runs(name, limit=25)
    return workflow


@app.delete("/api/workflows/{name}", dependencies=[Depends(require_user)])
async def delete_workflow(name: str) -> dict[str, Any]:
    result = await authoring.delete_workflow(name)
    bus.publish("workflow.deleted", {"workflow": name})
    asyncio.create_task(_restart_worker())
    return result


@app.post("/api/workflows/validate", dependencies=[Depends(require_user)])
async def validate_workflow(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return await authoring.validate(payload.get("name", ""), payload.get("code", ""))


@app.post("/api/workflows/{name}/run", dependencies=[Depends(require_user)])
async def run_workflow(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _start_run(name, payload.get("input") or {}, trigger="manual")


@app.post("/api/workflows/{name}/schedule", dependencies=[Depends(require_user)])
async def schedule_workflow(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    cron = (payload.get("cron") or "").strip()
    if not cron:
        raise HTTPException(status_code=400, detail="cron is required, e.g. '0 8 * * *'")
    result = await temporal.set_schedule(name, cron, payload.get("input") or {})
    bus.publish("workflow.scheduled", {"workflow": name, "cron": cron})
    return result


@app.delete("/api/workflows/{name}/schedule", dependencies=[Depends(require_user)])
async def unschedule_workflow(name: str) -> dict[str, Any]:
    await temporal.delete_schedule(name)
    bus.publish("workflow.unscheduled", {"workflow": name})
    return {"ok": True}


# --------------------------------------------------------------------- drafts


@app.get("/api/drafts", dependencies=[Depends(require_user)])
async def list_drafts() -> dict[str, Any]:
    return {"drafts": await authoring.list_drafts()}


@app.get("/api/drafts/{name}", dependencies=[Depends(require_user)])
async def get_draft(name: str) -> dict[str, Any]:
    return await authoring.get_draft(name)


@app.post("/api/drafts/{name}/approve", dependencies=[Depends(require_user)])
async def approve_draft(name: str) -> dict[str, Any]:
    published = await authoring.publish(name)
    bus.publish("workflow.published", {"workflow": name})
    restart = await _restart_worker()
    published["worker_restart"] = restart
    return published


@app.delete("/api/drafts/{name}", dependencies=[Depends(require_user)])
async def discard_draft(name: str) -> dict[str, Any]:
    result = await authoring.discard(name)
    bus.publish("workflow.draft_discarded", {"workflow": name})
    return result


# ----------------------------------------------------------------------- runs


async def _start_run(name: str, payload: dict[str, Any], trigger: str) -> dict[str, Any]:
    workflow = await authoring.get_workflow(name)
    manifest = workflow.get("manifest") or {}
    workflow_id = f"{name}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    started = await temporal.start(
        name, workflow_id, payload, timeout_minutes=manifest.get("timeout_minutes", 30)
    )
    db.record_run(name, started["workflow_id"], started.get("run_id"), trigger, payload)
    bus.publish("run.started", {"workflow": name, "workflow_id": workflow_id, "trigger": trigger})
    asyncio.create_task(_watch_run(name, workflow_id))
    return {"workflow": name, **started, "trigger": trigger}


async def _watch_run(name: str, workflow_id: str) -> None:
    """Follow a run so the UI sees an end state without polling Temporal."""
    for _ in range(240):  # up to ~20 minutes at 5s
        await asyncio.sleep(5)
        try:
            info = await temporal.describe(workflow_id)
        except Exception:  # noqa: BLE001, S112 - a describe that fails is just a slow answer
            continue
        if info["status"] in {"RUNNING", "UNKNOWN"}:
            continue
        result: Any = None
        if info["status"] == "COMPLETED":
            try:
                result = await temporal.result(workflow_id, timeout=10)
            except Exception:  # noqa: BLE001
                result = None
        db.update_run(workflow_id, info["status"].lower(), result)
        bus.publish(
            "run.finished",
            {"workflow": name, "workflow_id": workflow_id, "status": info["status"]},
        )
        return


@app.get("/api/runs", dependencies=[Depends(require_user)])
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


@app.get("/api/runs/{workflow_id}", dependencies=[Depends(require_user)])
async def get_run(workflow_id: str) -> dict[str, Any]:
    info = await temporal.describe(workflow_id)
    row = db.one("SELECT * FROM runs WHERE workflow_id = ?", (workflow_id,))
    if row:
        row["input"] = json.loads(row["input"] or "{}")
        row["result"] = json.loads(row["result"]) if row["result"] else None
    if info["status"] == "COMPLETED" and (not row or row.get("result") is None):
        try:
            info["result"] = await temporal.result(workflow_id, timeout=10)
        except Exception:  # noqa: BLE001
            info["result"] = None
    return {"run": row, "temporal": info}


@app.post("/api/runs/{workflow_id}/cancel", dependencies=[Depends(require_user)])
async def cancel_run(workflow_id: str) -> dict[str, Any]:
    await temporal.cancel(workflow_id)
    return {"ok": True}


# -------------------------------------------------------------------- trigger


@app.post("/api/triggers/{name}")
async def trigger(
    name: str, request: Request, token: str | None = Query(default=None)
) -> dict[str, Any]:
    """Triggers come in here and nowhere else."""
    if settings.auth_enabled:
        header = request.headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else token
        if not _token_matches(supplied, settings.app_token):
            raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a webhook may send nothing at all
        payload = {}
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    return await _start_run(name, payload, trigger="webhook")


# ------------------------------------------------------------------- internal


@app.post("/internal/agent/call", dependencies=[Depends(require_internal)])
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
    return await call_agent(job)


@app.post("/internal/events", dependencies=[Depends(require_internal)])
async def internal_event(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    kind = payload.get("kind", "worker.event")
    body = payload.get("payload") or {}
    db.add_event(payload.get("scope", "worker"), kind, body)
    bus.publish(kind, body)
    return {"ok": True}


@app.post("/internal/worker/restart", dependencies=[Depends(require_internal)])
async def internal_worker_restart() -> dict[str, Any]:
    return await _restart_worker()


async def _restart_worker() -> dict[str, Any]:
    try:
        result = await broker.restart_worker()
        bus.publish("worker.restarted", result)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001 - a failed restart must be visible, not fatal
        bus.publish("worker.restart_failed", {"error": str(exc)[:200]})
        return {"ok": False, "error": str(exc)[:200]}


# ------------------------------------------------------------------ artifacts


@app.get("/api/artifacts/{name}", dependencies=[Depends(require_user)])
async def get_artifact(name: str) -> Response:
    safe = os.path.basename(name)
    path = Path(settings.artifacts_dir) / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))


# --------------------------------------------------------------- frontend pass


_EXCLUDED_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def frontend(path: str, request: Request) -> Response:
    """One door: the SPA is served through the backend, not published itself."""
    if path.startswith(("api/", "internal/")):
        return JSONResponse({"detail": "not found"}, status_code=404)
    target = f"{settings.frontend_web_url.rstrip('/')}/{path}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            upstream = await client.request(
                request.method, target, params=dict(request.query_params)
            )
    except httpx.HTTPError as exc:
        return PlainTextResponse(f"frontend unavailable: {exc}", status_code=502)
    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _EXCLUDED_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )
