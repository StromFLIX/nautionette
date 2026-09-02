"""Restarting the Temporal workers, letting in-flight activities drain."""

from __future__ import annotations

import socket
from typing import Any

from fastapi import HTTPException

from . import daemon, images
from .config import PROJECT_OVERRIDE, STOP_GRACE, WORKER_LABEL


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


def restart() -> dict[str, Any]:
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
