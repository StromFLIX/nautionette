"""The docker broker: the only container that touches the Docker socket.

It exposes fixed verbs. There is no "run this image with these arguments"
endpoint, and no shell. Two things happen here:

* `POST /agent/run`      - one Pi container per call, streamed, then gone.
* `POST /worker/restart` - restart Temporal workers, letting activities drain.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from . import agent_run, daemon, images, monitor, workers
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
    return StreamingResponse(agent_run.run(job), media_type="application/x-ndjson")


@app.post("/worker/restart")
def worker_restart(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    return workers.restart()


@app.post("/agent/internet")
def agent_internet(
    payload: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> dict[str, Any]:
    _check_internal(x_internal_token)
    chat_id, turn_id = payload.get("chat_id"), payload.get("turn_id")
    if not chat_id or not turn_id or type(payload.get("allowed")) is not bool:
        raise HTTPException(status_code=422, detail="chat_id, turn_id and allowed are required")
    if not agent_run.decide_internet(chat_id, turn_id, payload["allowed"]):
        raise HTTPException(status_code=409, detail="The requesting agent is no longer running")
    return {"ok": True}


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
