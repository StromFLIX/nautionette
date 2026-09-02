"""One Pi container per agent call, streamed, then gone."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from typing import Any

from . import daemon, images
from .config import (
    AGENT_ENVIRONMENT,
    AGENT_MEMORY,
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


def _ndjson(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str) + "\n"


def _await_image(tag: str) -> Iterator[str]:
    """Narrate a missing image being built. Yields an error only if it never arrives."""
    images.start_build()
    started = time.monotonic()
    while True:
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
    if INTERNAL_TOKEN:
        environment["INTERNAL_TOKEN"] = INTERNAL_TOKEN
    return environment


def run(job: dict[str, Any]) -> Iterator[str]:
    agent_set = job.get("agent_set") or "default"
    if agent_set not in images.discovered_agent_sets():
        yield _ndjson({"type": "error", "message": f"unknown agent set '{agent_set}'"})
        return
    tag = images.image_tag(agent_set)
    if not images.has_image(tag):
        # The image can vanish under us: an idle host prunes it between calls.
        yield from _await_image(tag)
        if not images.has_image(tag):
            return  # _await_image already said why

    timeout = min(int(job.get("timeout_seconds") or RUN_TIMEOUT), RUN_TIMEOUT)
    container = None
    yield _ndjson({"type": "started", "agent_set": agent_set, "image": tag})
    try:
        container = daemon.client().containers.run(
            tag,
            detach=True,
            environment=_environment(job),
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
        daemon.log.exception("agent run failed")
        yield _ndjson({"type": "error", "message": str(exc)[:500]})
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001, S110 - already gone is the outcome we wanted
                pass
        yield _ndjson({"type": "closed"})
