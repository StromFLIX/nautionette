from __future__ import annotations

import os
import re
import socket
import threading
from pathlib import Path

from docker.errors import DockerException
from docker.types import Mount

from . import daemon

PROJECTS_DIR = Path(os.environ.get("PROJECTS_DIR", "/projects"))
_lock = threading.Lock()
_claimed: set[tuple[str, str]] = set()


def volume_name() -> str:
    try:
        broker = daemon.client().containers.get(socket.gethostname())
    except DockerException as exc:
        raise ValueError(
            "Cannot inspect the broker's project volume; check its Docker container hostname"
        ) from exc
    mounted = [
        mount for mount in broker.attrs.get("Mounts", []) if mount.get("Destination") == str(PROJECTS_DIR)
    ]
    if len(mounted) != 1 or mounted[0].get("Type") != "volume" or not mounted[0].get("Name"):
        raise ValueError(f"The broker requires a named Docker volume mounted at {PROJECTS_DIR}")
    return mounted[0]["Name"]


def mounts(project_ids: list[str], chat_id: str = "") -> list[Mount]:
    if (
        not isinstance(project_ids, list)
        or len(project_ids) > 20
        or any(not isinstance(item, str) for item in project_ids)
    ):
        raise ValueError("Select at most 20 projects")
    if project_ids and not re.fullmatch(r"[a-f0-9]{12}", chat_id):
        raise ValueError("Project worktrees require a valid chat ID")
    result = []
    volume = None
    for project_id in dict.fromkeys(project_ids):
        if not isinstance(project_id, str) or not re.fullmatch(r"[a-f0-9]{32}", project_id):
            raise ValueError("Invalid project ID")
        directory = PROJECTS_DIR / project_id
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Project checkout is unavailable")
        session = PROJECTS_DIR / ".sessions" / project_id / chat_id
        metadata = directory / ".git"
        if (
            metadata.is_symlink()
            or not metadata.is_dir()
            or not session.is_dir()
            or any(parent.is_symlink() for parent in (session, session.parent, session.parent.parent))
        ):
            raise ValueError("Project worktree is unavailable")
        if volume is None:
            volume = volume_name()
        for source, target in (
            (f"{project_id}/.git", f"/project-repositories/{project_id}"),
            (f".sessions/{project_id}/{chat_id}", f"/projects/.sessions/{project_id}/{chat_id}"),
        ):
            mount = Mount(target=target, source=volume, type="volume")
            mount["VolumeOptions"] = {"Subpath": source}
            result.append(mount)
    return result


def labels(project_ids: list[str], chat_id: str) -> dict[str, str]:
    return {f"nautionette.project.{project_id}": chat_id for project_id in project_ids}


def claim(project_ids: list[str], chat_id: str) -> None:
    with _lock:
        for project_id in project_ids:
            if (chat_id, project_id) in _claimed or daemon.client().containers.list(
                all=True,
                filters={"label": f"nautionette.project.{project_id}={chat_id}"},
            ):
                raise ValueError("This chat's project worktree is still in use by another agent")
        _claimed.update((chat_id, project_id) for project_id in project_ids)


def release(project_ids: list[str], chat_id: str) -> None:
    with _lock:
        _claimed.difference_update((chat_id, project_id) for project_id in project_ids)
