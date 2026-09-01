"""The docker broker: the only container that touches the Docker socket.

It exposes fixed verbs. There is no "run this image with these arguments"
endpoint, and no shell. Two things happen here:

* `POST /agent/run`    - one Pi container per call, streamed, then gone.
* `POST /worker/restart` - restart Temporal workers, letting activities drain.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s broker %(levelname)s %(message)s")
log = logging.getLogger("broker")

AGENT_IMAGES_DIR = os.environ.get("AGENT_IMAGES_DIR", "/agent-images")
IMAGE_PREFIX = os.environ.get("IMAGE_PREFIX", "nautionette/pi-")
BASE_IMAGE = os.environ.get("BASE_IMAGE", "nautionette/pi-base:dev")
TARGET_NETWORK = os.environ.get("TARGET_NETWORK", "nautionette-internal")
WORKFLOWS_VOLUME = os.environ.get("WORKFLOWS_VOLUME", "nautionette-workflows")
WORKER_LABEL = os.environ.get("WORKER_SERVICE_LABEL", "com.docker.compose.service=worker")
RUN_TIMEOUT = int(os.environ.get("AGENT_RUN_TIMEOUT_SECONDS", "900"))
AGENT_MEMORY = os.environ.get("AGENT_MEMORY_LIMIT", "1g")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "").strip()
STOP_GRACE = int(os.environ.get("WORKER_STOP_GRACE_SECONDS", "60"))

AGENT_ENVIRONMENT = {
    "AGENTGATEWAY_URL": os.environ.get("AGENT_AGENTGATEWAY_URL", "http://agentgateway:4000"),
    "BACKEND_URL": os.environ.get("AGENT_BACKEND_URL", "http://backend:8080"),
    "PI_OFFLINE": "1",
    "PI_SKIP_VERSION_CHECK": "1",
    "PI_TELEMETRY": "0",
}

app: FastAPI
client: docker.DockerClient | None = None

image_state: dict[str, Any] = {"status": "pending", "images": {}, "log": [], "error": None}
_state_lock = threading.Lock()


def _note(message: str) -> None:
    log.info(message)
    with _state_lock:
        image_state["log"] = (image_state["log"] + [message])[-40:]


def docker_client() -> docker.DockerClient:
    global client
    if client is None:
        client = docker.from_env()
    return client


def _check_internal(token: str | None) -> None:
    if INTERNAL_TOKEN and token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


# ------------------------------------------------------------------- images


def discovered_agent_sets() -> list[str]:
    sets_dir = os.path.join(AGENT_IMAGES_DIR, "agent-sets")
    if not os.path.isdir(sets_dir):
        return []
    return sorted(
        name
        for name in os.listdir(sets_dir)
        if os.path.isfile(os.path.join(sets_dir, name, "Dockerfile"))
    )


def image_tag(agent_set: str) -> str:
    """Tagged by the hash of its build context, so a changed agent set is a new image."""
    return f"{IMAGE_PREFIX}agent-{agent_set}:{_agent_set_hash(agent_set)}"


def _context_hash(path: str) -> str:
    digest = hashlib.sha256()
    root = Path(path)
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(file.relative_to(root)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:12]


def _base_hash() -> str:
    return _context_hash(os.path.join(AGENT_IMAGES_DIR, "pi-base"))


def _agent_set_hash(agent_set: str) -> str:
    directory = os.path.join(AGENT_IMAGES_DIR, "agent-sets", agent_set)
    if not os.path.isdir(directory):
        return "missing"
    return hashlib.sha256(
        (_context_hash(directory) + _base_hash()).encode()
    ).hexdigest()[:12]


def _has_image(tag: str) -> bool:
    try:
        docker_client().images.get(tag)
        return True
    except (ImageNotFound, DockerException):
        return False


def _build(path: str, tag: str) -> None:
    _note(f"building {tag}")
    started = time.time()
    _, logs = docker_client().images.build(path=path, tag=tag, rm=True, pull=False)
    for chunk in logs:
        stream = (chunk.get("stream") or "").strip()
        if stream:
            log.debug(stream)
    _note(f"built {tag} in {time.time() - started:.0f}s")


def ensure_images(force: bool = False) -> None:
    """Images are built once, at startup. A call never waits on a build."""
    with _state_lock:
        image_state["status"] = "building"
        image_state["error"] = None
    try:
        base_dir = os.path.join(AGENT_IMAGES_DIR, "pi-base")
        base_tag = f"{IMAGE_PREFIX}base:{_base_hash()}"
        if force or not _has_image(base_tag):
            _build(base_dir, base_tag)
        else:
            _note(f"{base_tag} already present")
        # Agent set Dockerfiles say FROM <BASE_IMAGE>, so point that alias here.
        repository, _, alias = BASE_IMAGE.rpartition(":")
        docker_client().images.get(base_tag).tag(repository, alias)

        images: dict[str, Any] = {"base": base_tag}
        for agent_set in discovered_agent_sets():
            tag = image_tag(agent_set)
            if force or not _has_image(tag):
                _build(os.path.join(AGENT_IMAGES_DIR, "agent-sets", agent_set), tag)
            else:
                _note(f"{tag} already present")
            images[agent_set] = tag
        with _state_lock:
            image_state["images"] = images
            image_state["status"] = "ready"
    except Exception as exc:  # noqa: BLE001 - report, never crash the broker
        log.exception("image build failed")
        with _state_lock:
            image_state["status"] = "failed"
            image_state["error"] = str(exc)[:500]


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=ensure_images, name="image-build", daemon=True).start()
    yield


app = FastAPI(title="nautionette docker-broker", docs_url=None, redoc_url=None, lifespan=lifespan)


# -------------------------------------------------------------------- health


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    try:
        docker_client().ping()
        docker_ok = True
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "docker": False, "error": str(exc)[:200]}
    with _state_lock:
        state = dict(image_state)
    return {
        "status": "ok" if state["status"] == "ready" else state["status"],
        "docker": docker_ok,
        "images": state["images"],
        "image_status": state["status"],
        "error": state["error"],
    }


@app.get("/agent-sets")
def agent_sets(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    with _state_lock:
        built = dict(image_state["images"])
    return {
        "agent_sets": [
            {"name": name, "image": image_tag(name), "ready": image_tag(name) in built.values()}
            for name in discovered_agent_sets()
        ]
    }


@app.post("/images/rebuild")
def rebuild(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    threading.Thread(target=ensure_images, kwargs={"force": True}, daemon=True).start()
    return {"ok": True, "status": "building"}


# ----------------------------------------------------------------- agent run


def _ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str) + "\n"


def _run_container(job: dict[str, Any]) -> Iterator[str]:
    agent_set = job.get("agent_set") or "default"
    if agent_set not in discovered_agent_sets():
        yield _ndjson({"type": "error", "message": f"unknown agent set '{agent_set}'"})
        return
    tag = image_tag(agent_set)
    if not _has_image(tag):
        with _state_lock:
            status = image_state["status"]
            error = image_state["error"]
        yield _ndjson(
            {
                "type": "error",
                "message": f"agent image {tag} is not ready (build status: {status})"
                + (f": {error}" if error else ""),
            }
        )
        return

    environment = dict(AGENT_ENVIRONMENT)
    environment["AGENT_JOB"] = base64.b64encode(
        json.dumps(job, default=str).encode("utf-8")
    ).decode("ascii")
    if INTERNAL_TOKEN:
        environment["INTERNAL_TOKEN"] = INTERNAL_TOKEN

    timeout = min(int(job.get("timeout_seconds") or RUN_TIMEOUT), RUN_TIMEOUT)
    container = None
    yield _ndjson({"type": "started", "agent_set": agent_set, "image": tag})
    try:
        container = docker_client().containers.run(
            tag,
            detach=True,
            environment=environment,
            network=TARGET_NETWORK,
            volumes={WORKFLOWS_VOLUME: {"bind": "/workflows", "mode": "ro"}},
            tmpfs={"/workspace": "size=256m,exec"},
            mem_limit=AGENT_MEMORY,
            pids_limit=512,
            security_opt=["no-new-privileges:true"],
            stdout=True,
            stderr=True,
            tty=False,
        )
        deadline = time.time() + timeout
        buffer = b""
        for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=False):
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip()
                if text:
                    yield text + "\n"
            if time.time() > deadline:
                yield _ndjson({"type": "error", "message": f"agent call exceeded {timeout}s"})
                container.kill()
                break
        if buffer.strip():
            yield buffer.decode("utf-8", "replace").strip() + "\n"

        status = container.wait(timeout=30)
        code = status.get("StatusCode", 0)
        if code != 0:
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
            yield _ndjson(
                {"type": "error", "message": f"agent container exited {code}: {stderr[-800:]}"}
            )
    except Exception as exc:  # noqa: BLE001 - always tell the caller what happened
        log.exception("agent run failed")
        yield _ndjson({"type": "error", "message": str(exc)[:500]})
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001, S110 - already gone is the outcome we wanted
                pass
        yield _ndjson({"type": "closed"})


@app.post("/agent/run")
def agent_run(
    job: dict[str, Any] = Body(...), x_internal_token: str | None = Header(default=None)
) -> StreamingResponse:
    _check_internal(x_internal_token)
    return StreamingResponse(_run_container(job), media_type="application/x-ndjson")


# ------------------------------------------------------------ worker restart


@app.post("/worker/restart")
def worker_restart(x_internal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_internal(x_internal_token)
    try:
        containers = docker_client().containers.list(filters={"label": WORKER_LABEL})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"docker unavailable: {exc}") from exc
    if not containers:
        return {"restarted": [], "detail": f"no container matched {WORKER_LABEL}"}
    restarted = []
    for container in containers:
        # A grace period, so in-flight activities finish instead of being killed.
        container.restart(timeout=STOP_GRACE)
        restarted.append(container.name)
        _note(f"restarted worker {container.name}")
    return {"restarted": restarted, "grace_seconds": STOP_GRACE}
