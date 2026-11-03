"""The docker broker: the only container that touches the Docker socket.

It exposes fixed verbs. There is no "run this image with these arguments"
endpoint, and no shell. Two things happen here:

* `POST /agent/run`      - one Pi container per call, streamed, then gone.
* `POST /worker/restart` - restart Temporal workers, letting activities drain.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from nautionette.telemetry import instrument_app
from opentelemetry import propagate
from pydantic import BaseModel, ConfigDict, Field

from . import agent_run, artcraft, daemon, git_credentials, images, miniapps, monitor, packages, workers
from .config import INTERNAL_TOKEN


def _check_internal(token: str | None) -> None:
    if INTERNAL_TOKEN and token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(_: FastAPI):
    stopping = threading.Event()
    thread = threading.Thread(target=monitor.run, args=(stopping,), name="container-monitor", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopping.set()
        await asyncio.to_thread(thread.join)


app = FastAPI(title="nautionette docker-broker", docs_url=None, redoc_url=None, lifespan=lifespan)
instrument_app(app, "nautionette-docker-broker")


@app.middleware("http")
async def control_boundary(request: Request, call_next):
    # Reject runtime credentials BEFORE FastAPI parses an administrative body.
    # Mini-apps can reach the broker, but cannot spend unbounded host RAM by
    # sending large JSON to privileged routes with an invalid/missing token.
    if request.url.path not in {"/miniapps/capability", "/healthz"}:
        try:
            _check_internal(request.headers.get("x-internal-token"))
        except HTTPException as exc:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    try:
        daemon.client().ping()
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "docker": False, "error": str(exc)[:200]}
    state = images.snapshot()
    worker_state = workers.snapshot()
    return {
        "status": "ok" if state["status"] == "ready" and worker_state["status"] == "ready" else "degraded",
        "docker": True,
        "images": state["images"],
        "image_status": state["status"],
        "missing_images": state.get("missing", []),
        "workers": worker_state,
        "error": state["error"],
    }


@app.get("/agent-sets")
def agent_sets(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    return {
        "agent_sets": [
            # Ask the daemon: an image built earlier can be pruned while the broker runs.
            {"name": name, "image": tag, "ready": images.has_image(tag)}
            for name in images.discovered_agent_sets()
            if (tag := images.image_tag(name))
        ]
    }


@app.post("/images/rebuild")
def rebuild(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    building = images.start_build(force=True)
    return {"ok": True, "status": "building" if building else "already building"}


@app.post("/agent/run")
def agent(
    job: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> StreamingResponse:
    _check_internal(x_internal_token)
    # Capture before StreamingResponse crosses into its generator/thread boundary.
    carrier = {}
    propagate.inject(carrier)
    job.pop("_trace_context", None)
    if carrier:
        job["_trace_context"] = carrier
    return StreamingResponse(agent_run.run(job), media_type="application/x-ndjson")


@app.post("/packages/install")
def install_package(
    payload: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _check_internal(x_internal_token)
    try:
        return packages.install(
            payload.get("installation_id"), payload.get("source"), payload.get("allow_scripts", False)
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Package installer unavailable or timed out; retry") from exc


@app.post("/artcraft/install")
def install_artcraft(
    payload: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> dict[str, str]:
    _check_internal(x_internal_token)
    if set(payload) != {"installation_id", "release"}:
        raise HTTPException(422, "Provide an installation ID and fixed ArtCraft release")
    try:
        return artcraft.install(payload["installation_id"], payload["release"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            503, "ArtCraft installer unavailable or timed out; previous version unchanged"
        ) from exc


@app.post("/worker/restart")
def worker_restart(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    return workers.restart()


@app.get("/agent/chats")
def chat_agents(chat_id: str = "", x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    return {"agents": agent_run.chat_inventory(chat_id)}


@app.post("/agent/cleanup")
def cleanup_chat_agent(
    payload: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _check_internal(x_internal_token)
    chat_id, turn_id = payload.get("chat_id"), payload.get("turn_id")
    if not isinstance(chat_id, str) or not chat_id or not isinstance(turn_id, str) or not turn_id:
        raise HTTPException(status_code=422, detail="chat_id and turn_id are required")
    try:
        agent_run.cleanup_chat(chat_id, turn_id)
    except Exception as exc:
        daemon.log.exception("Chat agent cleanup failed: chat=%s turn=%s", chat_id, turn_id)
        raise HTTPException(status_code=503, detail="Old chat agent cleanup failed; retry") from exc
    return {"ok": True}


@app.post("/agent/project-credentials")
def refresh_project_credentials(
    payload: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _check_internal(x_internal_token)
    chat_id, turn_id = payload.get("chat_id"), payload.get("turn_id")
    if not isinstance(chat_id, str) or not chat_id or not isinstance(turn_id, str) or not turn_id:
        raise HTTPException(422, "chat_id and turn_id are required")
    try:
        credentials = payload.get("credentials")
        git_credentials.validate(credentials)
        return {"ok": agent_run.refresh_project_credentials(chat_id, turn_id, credentials)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        # Docker responses can contain sensitive payloads. Do not echo or log them.
        raise HTTPException(503, "Git credential delivery failed; retry") from exc


@app.post("/agent/control")
def agent_control(
    payload: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _check_internal(x_internal_token)
    chat_id, turn_id = payload.get("chat_id"), payload.get("turn_id")
    command = payload.get("command")
    if (
        not isinstance(chat_id, str)
        or not chat_id
        or not isinstance(turn_id, str)
        or not turn_id
        or not isinstance(command, dict)
        or command.get("type") not in {"stop", "steer"}
        or not isinstance(command.get("id"), str)
        or not command["id"]
        or (
            command["type"] == "steer"
            and (not isinstance(command.get("text"), str) or not command["text"].strip())
        )
    ):
        raise HTTPException(status_code=422, detail="Valid chat, turn and command are required")
    return {"ok": agent_run.control(chat_id, turn_id, command)}


class MiniAppDeploy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, le=1000000)
    source: str = Field(min_length=1, max_length=65536)
    memory_mb: int = 64
    mode: str = "on-demand"


@app.post("/miniapps/{app_id}/deploy")
def deploy_miniapp(
    app_id: str, payload: MiniAppDeploy, x_internal_token: str | None = Header(default=None)
) -> dict:
    _check_internal(x_internal_token)
    try:
        return miniapps.deploy(app_id, **payload.model_dump())
    except miniapps.CompilerUnavailable as exc:
        raise HTTPException(503, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        if os.environ.get("MINIAPP_TEST_DIAGNOSTICS") == "1":
            daemon.log.exception("Mini-app integration test runtime failure")
        raise HTTPException(
            503, "Mini-app build/runtime unavailable; inspect status before retrying"
        ) from exc


class MiniAppCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1, max_length=65536)


@app.post("/miniapps/{app_id}/check")
def check_miniapp(
    app_id: str, payload: MiniAppCheck, x_internal_token: str | None = Header(default=None)
) -> dict:
    _check_internal(x_internal_token)
    try:
        miniapps.checked(app_id)
        return miniapps.build(app_id, 1, payload.source, validate_only=True)
    except miniapps.CompilerUnavailable as exc:
        raise HTTPException(503, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Mini-app compiler unavailable") from exc


@app.get("/miniapps/{app_id}")
def miniapp_status(app_id: str, x_internal_token: str | None = Header(default=None)) -> dict:
    _check_internal(x_internal_token)
    try:
        return miniapps.snapshot(app_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Mini-app runtime unavailable") from exc


@app.get("/miniapps/{app_id}/state")
def miniapp_state(app_id: str, x_internal_token: str | None = Header(default=None)):
    _check_internal(x_internal_token)
    config = miniapps.load(app_id)
    return {
        "revision": (config.get("active") or {}).get("revision"),
        "disabled": bool(config.get("disabled")),
        "deleting": bool(config.get("deleting")),
    }


@app.post("/miniapps/{app_id}/request")
def request_miniapp(
    app_id: str, payload: dict = Body(...), x_internal_token: str | None = Header(default=None)
):
    _check_internal(x_internal_token)
    if set(payload) != {"revision", "request"} or len(json.dumps(payload).encode()) > 65536:
        raise HTTPException(422, "Invalid app request")
    try:
        return miniapps.request(app_id, payload["revision"], payload["request"])
    except Exception as exc:
        raise HTTPException(
            503, "Mini-app request unavailable; a mutation may already have executed"
        ) from exc


@app.delete("/miniapps/{app_id}")
def remove_miniapp(app_id: str, x_internal_token: str | None = Header(default=None)) -> dict:
    _check_internal(x_internal_token)
    try:
        return miniapps.remove(app_id)
    except Exception as exc:
        raise HTTPException(503, "Mini-app cleanup incomplete; retry the same removal") from exc


@app.post("/miniapps/{app_id}/control")
def control_miniapp(
    app_id: str, payload: dict = Body(...), x_internal_token: str | None = Header(default=None)
) -> dict:
    _check_internal(x_internal_token)
    if set(payload) != {"action"}:
        raise HTTPException(422, "Provide a fixed mini-app action")
    try:
        return miniapps.control(app_id, payload["action"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Mini-app control failed; inspect its state") from exc


@app.post("/miniapps/capability")
async def miniapp_capability(request: Request, authorization: str = Header(default="")):
    # This is the ONLY runtime-facing verb. Bound the raw body before JSON parsing.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, "Capability request exceeds 64 KiB")
    try:
        payload = json.loads(data)
    except ValueError as exc:
        raise HTTPException(422, "Invalid capability request") from exc
    if not isinstance(payload, dict) or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid mini-app credential")
    return await asyncio.to_thread(miniapps.capability, authorization[7:], payload)
