"""One Pi container per agent call, streamed, then gone."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator
from typing import Any

from . import daemon, images, projects
from .config import (
    AGENT_EGRESS_NETWORK,
    AGENT_ENVIRONMENT,
    AGENT_MEMORY,
    AGENT_NETWORK,
    IMAGE_BUILD_TIMEOUT,
    INTERNAL_TOKEN,
    RUN_TIMEOUT,
    TARGET_NETWORK,
    WORKFLOWS_VOLUME,
)

# What the caller is told while it waits for an image to be built, in order.
BUILD_STAGES = (
    "Setting things up",
    "Building the agent image",
    "Installing the agent's tools",
    "Almost ready",
)
STAGE_SECONDS = 20
BUILD_POLL_SECONDS = 3
_controls_lock = threading.Lock()
_stopped: dict[tuple[str, str], threading.Event] = {}


def control(chat_id: str, turn_id: str, command: dict[str, Any]) -> bool:
    if command["type"] == "stop":
        with _controls_lock:
            stopped = _stopped.get((chat_id, turn_id))
            if stopped is not None:
                stopped.set()
    containers = daemon.client().containers.list(filters={"label": [
        f"nautionette.chat={chat_id}", f"nautionette.turn={turn_id}",
    ]})
    if not containers:
        return command["type"] == "stop" and stopped is not None
    delivered = False
    for container in containers:
        if command["type"] == "stop":
            container.kill()
            delivered = True
            continue
        script = (
            "const net = require('node:net');"
            "const client = net.connect('/tmp/nautionette-chat.sock');"
            "client.setTimeout(5000, () => process.exit(1));"
            "client.on('error', () => process.exit(1));"
            "client.on('connect', () => client.write(process.argv[1] + '\\n'));"
            "client.on('data', chunk => process.stdout.write(chunk));"
        )
        result = container.exec_run(["node", "-e", script, json.dumps(command)])
        if result.exit_code == 0:
            try:
                delivered = json.loads(result.output).get("ok") is True
            except (ValueError, TypeError):
                pass
    return delivered


def _ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str) + "\n"


def _await_image(tag: str, stopped: threading.Event | None = None) -> Iterator[str]:
    """Narrate a missing image being built. Yields an error only if it never arrives."""
    images.start_build()
    started = time.monotonic()
    while True:
        if stopped is not None and stopped.is_set():
            return
        if images.has_image(tag):
            return
        state = images.snapshot()
        elapsed = time.monotonic() - started
        if state["status"] in {"ready", "failed"}:
            reason = state["error"] or f"{tag} was not produced by the build"
            yield _ndjson({"type": "error", "message": f"the agent image failed to build: {reason}"})
            return
        if elapsed > IMAGE_BUILD_TIMEOUT:
            yield _ndjson(
                {"type": "error", "message": f"{tag} was still building after {int(elapsed)}s"}
            )
            return
        yield _ndjson(
            {
                "type": "status",
                "state": "building",
                "message": BUILD_STAGES[min(int(elapsed // STAGE_SECONDS), len(BUILD_STAGES) - 1)],
                "seconds": int(elapsed),
            }
        )
        time.sleep(BUILD_POLL_SECONDS)


def _environment(job: dict[str, Any]) -> dict[str, str]:
    environment = dict(AGENT_ENVIRONMENT)
    environment["AGENT_JOB"] = base64.b64encode(
        json.dumps(job, default=str).encode("utf-8")
    ).decode("ascii")
    if job.get("project_ids"):
        environment["HOME"] = "/workspace"
        environment["PI_CODING_AGENT_DIR"] = "/workspace/.pi-agent"
    if job.get("chat_id"):
        environment.pop("BACKEND_URL", None)
    elif INTERNAL_TOKEN:
        environment["INTERNAL_TOKEN"] = INTERNAL_TOKEN
    return environment


def decide_internet(chat_id: str, turn_id: str, allowed: bool) -> bool:
    containers = daemon.client().containers.list(filters={"label": [
        f"nautionette.chat={chat_id}", f"nautionette.turn={turn_id}",
    ]})
    if not containers:
        return False
    for container in containers:
        container.reload()
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        network = daemon.client().networks.get(AGENT_EGRESS_NETWORK)
        newly_connected = allowed and AGENT_EGRESS_NETWORK not in networks
        if newly_connected:
            network.connect(container)
        elif not allowed and AGENT_EGRESS_NETWORK in networks:
            network.disconnect(container)
        decision = "allowed" if allowed else "denied"
        try:
            result = container.exec_run([
                "node", "-e",
                'require("node:fs").writeFileSync("/tmp/nautionette-internet-decision", '
                + json.dumps(decision) + ')',
            ])
            if result.exit_code != 0:
                raise RuntimeError("Could not deliver the internet approval decision")
        except Exception:
            if newly_connected:
                try:
                    network.disconnect(container)
                except Exception:
                    container.kill()
            raise
    return True


def run(job: dict[str, Any]) -> Iterator[str]:
    stopped = threading.Event()
    key = (job.get("chat_id", ""), job.get("turn_id", ""))
    if key[0]:
        with _controls_lock:
            if key in _stopped:
                yield _ndjson({"type": "error", "message": "This turn is already running"})
                return
            _stopped[key] = stopped
    try:
        yield from _run(job, stopped)
    finally:
        if key[0]:
            with _controls_lock:
                _stopped.pop(key, None)


def _run(job: dict[str, Any], stopped: threading.Event) -> Iterator[str]:
    agent_set = job.get("agent_set") or "default"
    if agent_set not in images.discovered_agent_sets():
        yield _ndjson({"type": "error", "message": f"unknown agent set '{agent_set}'"})
        return
    tag = images.image_tag(agent_set)
    if not images.has_image(tag):
        # The image can vanish under us: an idle host prunes it between calls.
        yield from _await_image(tag, stopped)
        if not images.has_image(tag):
            return  # _await_image already said why

    timeout = min(int(job.get("timeout_seconds") or RUN_TIMEOUT), RUN_TIMEOUT)
    container = None
    watchdog = None
    claimed_projects = []
    yield _ndjson({"type": "started", "agent_set": agent_set, "image": tag})
    try:
        if stopped.is_set():
            return
        project_ids = job.get("project_ids", [])
        if project_ids and tuple(map(int, daemon.client().api._version.split("."))) < (1, 45):
            raise RuntimeError("Project isolation requires Docker Engine 26+ with API 1.45+")
        project_mounts = projects.mounts(project_ids, job.get("chat_id", ""))
        projects.claim(project_ids, job.get("chat_id", ""))
        claimed_projects = project_ids
        if job.get("chat_id") and not daemon.client().networks.get(AGENT_NETWORK).attrs.get("Internal"):
            raise RuntimeError("Chat agents require an internal Docker network with egress disabled")
        container = daemon.client().containers.create(
            tag,
            detach=True,
            environment=_environment(job),
            network=AGENT_NETWORK if job.get("chat_id") else TARGET_NETWORK,
            volumes={WORKFLOWS_VOLUME: {"bind": "/workflows", "mode": "ro"}},
            mounts=project_mounts,
                tmpfs=({"/workspace": "size=256m,exec,uid=10001,gid=10001",
                    "/projects": "size=1m,uid=10001,gid=10001"} if project_ids
                   else {"/workspace": "size=256m,exec"}),
            user="10001:10001" if project_ids else None,
            mem_limit=AGENT_MEMORY,
            pids_limit=512,
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            labels={
                "nautionette.chat": job.get("chat_id", ""),
                "nautionette.turn": job.get("turn_id", ""),
                **projects.labels(project_ids, job.get("chat_id", "")),
            },
            tty=False,
        )
        if job.get("chat_id") and job.get("internet_allowed") is True:
            daemon.client().networks.get(AGENT_EGRESS_NETWORK).connect(container)
        with _controls_lock:
            if stopped.is_set():
                return
            container.start()
        watchdog = threading.Timer(timeout, container.kill)
        watchdog.daemon = True
        watchdog.start()
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
        daemon.log.exception("agent run failed")
        yield _ndjson({"type": "error", "message": str(exc)[:500]})
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001, S110 - already gone is the outcome we wanted
                pass
        projects.release(claimed_projects, job.get("chat_id", ""))
        yield _ndjson({"type": "closed"})
