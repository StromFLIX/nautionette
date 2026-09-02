"""The docker broker: the only container that touches the Docker socket.

It exposes fixed verbs. There is no "run this image with these arguments"
endpoint, and no shell. Two things happen here:

* `POST /agent/run`      - one Pi container per call, streamed, then gone.
* `POST /worker/restart` - restart Temporal workers, letting activities drain.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from . import agent_run, daemon, images, workers
from .config import INTERNAL_TOKEN


def _check_internal(token: str | None) -> None:
    if INTERNAL_TOKEN and token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(_: FastAPI):
    images.start_build()
    yield


app = FastAPI(title="nautionette docker-broker", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    try:
        daemon.client().ping()
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "docker": False, "error": str(exc)[:200]}
    state = images.snapshot()
    return {
        "status": "ok" if state["status"] == "ready" else state["status"],
        "docker": True,
        "images": state["images"],
        "image_status": state["status"],
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
