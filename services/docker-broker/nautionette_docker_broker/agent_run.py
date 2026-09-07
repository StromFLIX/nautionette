"""One Pi container per agent call, streamed, then gone."""

from __future__ import annotations

import base64
import io
import json
import tarfile
import threading
import time
from collections.abc import Iterator
from typing import Any

from docker.errors import NotFound

from . import chat_agents, daemon, images, projects
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
_completed: dict[tuple[str, str], threading.Event] = {}
# Fence delayed HTTP requests for a cleaned-up turn, including ones that had
# not yet registered when cleanup arrived. Bound retention to the call horizon.
_retired: dict[tuple[str, str], float] = {}
CLEANUP_TIMEOUT = 30


def chat_inventory(chat_id: str = "") -> list[dict[str, str]]:
    # Include calls still waiting for an image, before a container exists.
    with _controls_lock:
        keys = {key for key in _stopped if not chat_id or key[0] == chat_id}
    keys.update(
        (container.labels["nautionette.chat"], container.labels["nautionette.turn"])
        for container in chat_agents.containers(chat_id)
    )
    return [{"chat_id": chat, "turn_id": turn} for chat, turn in sorted(keys)]


def cleanup_chat(chat_id: str, turn_id: str) -> None:
    """Stop an exact turn and wait for both Docker and its worktree claim.

    The backend decides whether a turn is inactive. This verb never infers
    orphanhood from age, and never releases claims or removes worktree files.
    """
    with _controls_lock:
        _retired[(chat_id, turn_id)] = time.monotonic() + RUN_TIMEOUT + IMAGE_BUILD_TIMEOUT
        stopped = _stopped.get((chat_id, turn_id))
        completed = _completed.get((chat_id, turn_id))
        if stopped is not None:
            stopped.set()
    for container in chat_agents.containers(chat_id, turn_id):
        try:
            # Force removal also handles created/paused agents. Docker does not
            # return until removal completes; the mounted volumes are retained.
            container.remove(force=True)
        except NotFound:
            pass  # The streaming owner may have removed it concurrently.
    if completed is not None and not completed.wait(CLEANUP_TIMEOUT):
        raise RuntimeError("The old chat agent has not released its worktree yet; retry cleanup")
    if chat_agents.containers(chat_id, turn_id):
        raise RuntimeError("The old chat agent is still present; retry cleanup")


def control(chat_id: str, turn_id: str, command: dict[str, Any]) -> bool:
    if command["type"] == "stop":
        with _controls_lock:
            stopped = _stopped.get((chat_id, turn_id))
            if stopped is not None:
                stopped.set()
    containers = chat_agents.containers(chat_id, turn_id, include_stopped=False)
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
            yield _ndjson({"type": "error", "message": f"{tag} was still building after {int(elapsed)}s"})
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
    raw = json.dumps(job, default=str).encode("utf-8")
    # Linux caps a single environment value at ~128 KiB. Image bytes (and long
    # transcripts) go through Docker's archive API, never argv or environment.
    if len(raw) > 32_000:
        environment["AGENT_JOB_FILE"] = "/tmp/nautionette-job.json"  # noqa: S108 - private container filesystem
    else:
        environment["AGENT_JOB"] = base64.b64encode(raw).decode("ascii")
    if job.get("project_ids"):
        environment["HOME"] = "/workspace"
        environment["PI_CODING_AGENT_DIR"] = "/workspace/.pi-agent"
    if job.get("chat_id"):
        environment.pop("BACKEND_URL", None)
    elif INTERNAL_TOKEN:
        environment["INTERNAL_TOKEN"] = INTERNAL_TOKEN
    return environment


def _copy_job(container: Any, job: dict[str, Any]) -> None:
    raw = json.dumps(job, default=str).encode("utf-8")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        entry = tarfile.TarInfo("nautionette-job.json")
        entry.size = len(raw)
        entry.mode = 0o600
        entry.uid = entry.gid = 10001 if job.get("project_ids") else 0
        tar.addfile(entry, io.BytesIO(raw))
    if not container.put_archive("/tmp", archive.getvalue()):  # noqa: S108
        raise RuntimeError("Could not deliver the agent job")


def decide_internet(chat_id: str, turn_id: str, allowed: bool) -> bool:
    containers = chat_agents.containers(chat_id, turn_id, include_stopped=False)
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
            result = container.exec_run(
                [
                    "node",
                    "-e",
                    'require("node:fs").writeFileSync("/tmp/nautionette-internet-decision", '
                    + json.dumps(decision)
                    + ")",
                ]
            )
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
    completed = threading.Event()
    key = (job.get("chat_id", ""), job.get("turn_id", ""))
    if key[0]:
        with _controls_lock:
            now = time.monotonic()
            for retired in [item for item, deadline in _retired.items() if deadline <= now]:
                _retired.pop(retired)
            rejection = (
                "This turn was already cleaned up"
                if key in _retired
                else "This turn is already running"
                if key in _stopped
                else ""
            )
            if not rejection:
                _stopped[key] = stopped
                _completed[key] = completed
        if rejection:
            yield _ndjson({"type": "error", "message": rejection})
            return
    try:
        yield from _run(job, stopped, completed)
    finally:
        if key[0]:
            with _controls_lock:
                _stopped.pop(key, None)
                _completed.pop(key, None)
        completed.set()


def _timeout_error(timeout: int) -> dict[str, Any]:
    return {
        "type": "error",
        "reason": "timeout",
        "timeout_seconds": timeout,
        "message": (
            f"agent call exceeded its {timeout}s time limit and was stopped. "
            "Continue in a new message, or increase AGENT_RUN_TIMEOUT_SECONDS "
            "(workflow calls must also increase their timeout_seconds)."
        ),
    }


def _run(
    job: dict[str, Any], stopped: threading.Event, completed: threading.Event | None = None
) -> Iterator[str]:
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
    timed_out = threading.Event()
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
        environment = _environment(job)
        container = daemon.client().containers.create(
            tag,
            detach=True,
            environment=environment,
            network=AGENT_NETWORK if job.get("chat_id") else TARGET_NETWORK,
            volumes={WORKFLOWS_VOLUME: {"bind": "/workflows", "mode": "ro"}},
            mounts=project_mounts,
            tmpfs=(
                {
                    "/workspace": "size=256m,exec,uid=10001,gid=10001",
                    "/projects": "size=1m,uid=10001,gid=10001",
                }
                if project_ids
                else {"/workspace": "size=256m,exec"}
            ),
            user="10001:10001" if project_ids else None,
            mem_limit=AGENT_MEMORY,
            pids_limit=512,
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            labels={
                "nautionette.deployment": WORKFLOWS_VOLUME,
                "nautionette.chat": job.get("chat_id", ""),
                "nautionette.turn": job.get("turn_id", ""),
                **projects.labels(project_ids, job.get("chat_id", "")),
            },
            tty=False,
        )
        if "AGENT_JOB_FILE" in environment:
            _copy_job(container, job)
        if job.get("chat_id") and job.get("internet_allowed") is True:
            daemon.client().networks.get(AGENT_EGRESS_NETWORK).connect(container)
        with _controls_lock:
            if stopped.is_set():
                return
            container.start()

        def expire() -> None:
            # Record the reason before killing: Docker closes even a silent log
            # stream on exit, so a deadline check inside that stream can miss it.
            timed_out.set()
            daemon.log.warning(
                "agent time limit exceeded: chat=%s turn=%s timeout_seconds=%s",
                job.get("chat_id", ""),
                job.get("turn_id", ""),
                timeout,
            )
            try:
                container.kill()
            except Exception:  # noqa: BLE001 - it may have exited concurrently
                daemon.log.exception("could not kill timed-out agent container")

        watchdog = threading.Timer(timeout, expire)
        watchdog.daemon = True
        watchdog.start()
        buffer = b""
        for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=False):
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip()
                if text:
                    yield text + "\n"
        if buffer.strip():
            yield buffer.decode("utf-8", "replace").strip() + "\n"

        status = container.wait(timeout=30)
        watchdog.cancel()
        watchdog.join()
        code = status.get("StatusCode", 0)
        if stopped.is_set():
            return  # An explicit Stop is not a container failure.
        if timed_out.is_set():
            yield _ndjson(_timeout_error(timeout))
        elif code != 0:
            container.reload()
            oom_killed = container.attrs.get("State", {}).get("OOMKilled") is True
            if oom_killed:
                error = {
                    "type": "error",
                    "reason": "oom_killed",
                    "exit_code": code,
                    "message": (
                        f"agent container ran out of memory (limit {AGENT_MEMORY}, exit {code}). "
                        "Reduce memory use or increase AGENT_MEMORY_LIMIT."
                    ),
                }
            elif code == 137:
                error = {
                    "type": "error",
                    "reason": "sigkill",
                    "exit_code": code,
                    "message": (
                        "agent container was killed (SIGKILL, exit 137). "
                        "The broker timeout did not fire and Docker did not report an OOM kill; "
                        "check host memory and deployment logs."
                    ),
                }
            else:
                stderr = container.logs(stdout=False, stderr=True, tail=100).decode("utf-8", "replace")
                error = {"type": "error", "message": f"agent container exited {code}: {stderr[-800:]}"}
            daemon.log.warning(
                "agent container failed: chat=%s turn=%s exit=%s oom_killed=%s",
                job.get("chat_id", ""),
                job.get("turn_id", ""),
                code,
                oom_killed,
            )
            yield _ndjson(error)
    except Exception as exc:  # noqa: BLE001 - always tell the caller what happened
        daemon.log.exception("agent run failed")
        if not stopped.is_set():
            yield _ndjson(
                _timeout_error(timeout)
                if timed_out.is_set()
                else {"type": "error", "message": str(exc)[:500]}
            )
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001, S110 - already gone is the outcome we wanted
                pass
        projects.release(claimed_projects, job.get("chat_id", ""))
        if completed is not None:
            completed.set()
        yield _ndjson({"type": "closed"})
