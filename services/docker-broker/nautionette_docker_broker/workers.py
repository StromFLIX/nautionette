"""Restarting the Temporal workers, letting in-flight activities drain."""

from __future__ import annotations

import socket
import threading
from typing import Any

from docker.types import LogConfig
from fastapi import HTTPException

from . import daemon, images
from .config import (
    ARTIFACTS_VOLUME,
    PROJECT_OVERRIDE,
    STOP_GRACE,
    TARGET_NETWORK,
    WORKER_ENVIRONMENT,
    WORKER_IMAGE,
    WORKER_LABEL,
    WORKER_REPLICAS,
    WORKFLOWS_VOLUME,
)

operation_lock = threading.Lock()
last_error: str | None = None


def _own_compose_project() -> str | None:
    """Which stack this broker belongs to.

    The host may run other people's containers, and plenty of them call a service
    "worker". Restarting one of those would be someone else's outage, so every
    lookup is scoped to this broker's own compose project.
    """
    try:
        me = daemon.client().containers.get(socket.gethostname())
        return me.labels.get("com.docker.compose.project")
    except Exception as exc:  # noqa: BLE001
        daemon.log.warning("could not determine own compose project: %s", exc)
        return None


def worker_filters() -> dict[str, Any] | None:
    project = PROJECT_OVERRIDE or _own_compose_project()
    if not project:
        return None
    return {"label": [WORKER_LABEL, f"com.docker.compose.project={project}"]}


def _state(container: Any) -> dict[str, Any]:
    state = container.attrs.get("State", {})
    health = state.get("Health", {})
    checks = health.get("Log", [])
    return {
        "name": container.name,
        "status": state.get("Status", "unknown"),
        "health": health.get("Status"),
        "detail": checks[-1].get("Output", "").strip()[:500] if checks else None,
    }


def snapshot() -> dict[str, Any]:
    try:
        filters = worker_filters()
        if filters is None:
            raise RuntimeError("this broker cannot tell which stack it belongs to")
        containers = [
            _state(container) for container in daemon.client().containers.list(all=True, filters=filters)
        ]
        ready = sum(item["status"] == "running" and item["health"] == "healthy" for item in containers)
        return {
            "status": "ready" if ready >= WORKER_REPLICAS and not last_error else "degraded",
            "desired": WORKER_REPLICAS,
            "ready": ready,
            "containers": containers,
            "error": last_error,
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "desired": WORKER_REPLICAS,
            "ready": 0,
            "containers": [],
            "error": str(exc)[:500],
        }


def reconcile() -> None:
    global last_error
    with operation_lock:
        try:
            filters = worker_filters()
            if filters is None:
                raise RuntimeError("refusing to reconcile: this broker cannot tell which stack it belongs to")
            project = filters["label"][1].split("=", 1)[1]
            containers = daemon.client().containers.list(all=True, filters=filters)
            available = len(containers)
            errors = []
            for container in containers:
                try:
                    state = _state(container)
                    if state["status"] == "dead":
                        container.remove()
                        available -= 1
                    elif state["status"] in {"created", "exited"}:
                        container.start()
                        images.note(f"started worker {container.name}")
                    elif state["status"] == "paused":
                        container.unpause()
                    elif state["status"] == "running" and state["health"] == "unhealthy":
                        container.restart(timeout=STOP_GRACE)
                        images.note(f"restarted unhealthy worker {container.name}")
                except Exception as exc:
                    errors.append(f"{container.name}: {exc}")
            names = {container.name for container in containers}
            number = 1
            while available < WORKER_REPLICAS:
                name = f"{project}-worker-{number}"
                number += 1
                if name in names:
                    continue
                label, _, value = WORKER_LABEL.partition("=")
                daemon.client().containers.run(
                    WORKER_IMAGE,
                    name=name,
                    detach=True,
                    environment=WORKER_ENVIRONMENT,
                    network=TARGET_NETWORK,
                    volumes={
                        WORKFLOWS_VOLUME: {"bind": "/workflows", "mode": "rw"},
                        ARTIFACTS_VOLUME or f"{project}_artifacts": {"bind": "/artifacts", "mode": "rw"},
                    },
                    labels={
                        label: value,
                        "com.docker.compose.project": project,
                        "com.docker.compose.container-number": str(number - 1),
                    },
                    restart_policy={"Name": "unless-stopped"},
                    log_config=LogConfig(type="json-file", config={"max-size": "10m", "max-file": "3"}),
                )
                available += 1
                images.note(f"created missing worker {name}")
            last_error = "; ".join(errors)[:500] or None
        except Exception as exc:
            last_error = str(exc)[:500]
            daemon.log.warning("worker reconciliation failed: %s", exc)


def restart() -> dict[str, Any]:
    with operation_lock:
        return _restart()


def _restart() -> dict[str, Any]:
    filters = worker_filters()
    if filters is None:
        raise HTTPException(
            status_code=503,
            detail="refusing to restart: this broker cannot tell which stack it belongs to",
        )
    try:
        containers = daemon.client().containers.list(filters=filters)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"docker unavailable: {exc}") from exc
    if not containers:
        return {"restarted": [], "detail": f"no container matched {filters['label']}"}
    restarted = []
    for container in containers:
        # A grace period, so in-flight activities finish instead of being killed.
        container.restart(timeout=STOP_GRACE)
        restarted.append(container.name)
        images.note(f"restarted worker {container.name}")
    return {"restarted": restarted, "grace_seconds": STOP_GRACE}
