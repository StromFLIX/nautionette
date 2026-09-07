from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from docker.types import Mount

from . import daemon

PROJECTS_DIR = Path(os.environ.get("PROJECTS_DIR", "/projects"))
PROJECTS_VOLUME = os.environ.get("PROJECTS_VOLUME", "nautionette-projects")
_lock = threading.Lock()
_claimed: set[tuple[str, str]] = set()


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
        for source, target in (
            (f"{project_id}/.git", f"/project-repositories/{project_id}"),
            (f".sessions/{project_id}/{chat_id}", f"/projects/.sessions/{project_id}/{chat_id}"),
        ):
            mount = Mount(target=target, source=PROJECTS_VOLUME, type="volume")
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
