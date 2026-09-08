"""Read-only, local Git summaries for the worktrees belonging to one chat."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from . import projects
from .db import db

MAX_FILE_BYTES = 8 * 1024 * 1024


def _inside(path: Path, root: Path) -> Path:
    if not path.is_relative_to(root) or any(
        p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)
    ):
        raise ValueError("Unsafe project path")
    return path


def _git(directory: Path, metadata: Path, deadline: float, *args: str) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Change summary timed out")
    # Worktrees are created inside the agent with a different metadata mount.
    # Explicit paths avoid rewriting their .git files or shared configuration.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull
    )
    result = subprocess.run(  # noqa: S603 — fixed executable and argument array, never a shell
        [
            "/usr/bin/git",
            "-c",
            f"safe.directory={directory}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.quotePath=false",
            f"--git-dir={metadata}",
            f"--work-tree={directory}",
            *args,
        ],
        env=environment,
        cwd=directory,
        capture_output=True,
        timeout=min(5, remaining),
        check=True,
    )
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("Change summary is too large")
    return result.stdout


def _untracked(directory: Path, path: str, budget: list[int]) -> dict[str, Any]:
    file = directory / path
    # Never read through a symlink (including symlinked parents), or open devices/FIFOs.
    _inside(file.parent, directory)
    info = file.lstat()
    item = {"path": path, "status": "untracked", "additions": None, "deletions": None, "binary": False}
    if stat.S_ISLNK(info.st_mode):
        return item | {"additions": 1, "deletions": 0}
    if not stat.S_ISREG(info.st_mode) or info.st_size > min(MAX_FILE_BYTES, budget[0]):
        return item
    descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            return item
        data = stream.read(min(MAX_FILE_BYTES, budget[0]) + 1)
    budget[0] -= len(data)
    if len(data) > MAX_FILE_BYTES or budget[0] < 0:
        return item
    if b"\0" in data[:8000]:
        return item | {"binary": True}
    return item | {
        "additions": data.count(b"\n") + int(bool(data) and not data.endswith(b"\n")),
        "deletions": 0,
    }


def _summary(project_id: str, chat_id: str, deadline: float) -> dict[str, Any]:
    root = projects.PROJECTS_DIR
    directory = _inside(root / ".sessions" / project_id / chat_id, root)
    pointer = _inside(directory / ".git", root)
    empty = {"files": [], "additions": 0, "deletions": 0, "file_count": 0, "scope": "pending"}
    if not pointer.exists():
        return empty
    common = _inside(projects.checkout(project_id) / ".git", root)
    # Only accept this repository's worktree metadata, not arbitrary paths from .git.
    text = pointer.read_text().strip()
    prefix = f"gitdir: /project-repositories/{project_id}/worktrees/"
    if text.startswith(prefix):
        name = text.removeprefix(prefix)
    elif text.startswith(f"gitdir: {common}/worktrees/"):
        name = text.removeprefix(f"gitdir: {common}/worktrees/")
    else:
        raise ValueError("Unexpected worktree metadata")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in {".", ".."}:
        raise ValueError("Invalid worktree metadata")
    metadata = _inside(common / "worktrees" / name, root)
    # Verify the worktree registration too; a pointer must not select another chat's HEAD/index.
    registration = _inside(metadata / "gitdir", root).read_text().strip()
    if registration != str(directory / ".git"):
        raise ValueError("Worktree belongs to another chat")
    reference = f"refs/nautionette/chats/{chat_id}"
    found = _git(directory, metadata, deadline, "for-each-ref", "--format=%(objectname)", reference).strip()
    # Push updates remote-tracking refs even for detached HEAD worktrees. Find the
    # nearest published first-parent ancestor, capped at the attachment revision.
    # Do not diff against a remote tip: it may contain unrelated work or be ahead
    # of this chat. No fetch is needed (or permitted by this read-only endpoint).
    unpublished = _git(
        directory,
        metadata,
        deadline,
        "rev-list",
        "--first-parent",
        "--boundary",
        "HEAD",
        "--not",
        "--remotes",
        *([found.decode("ascii")] if found else []),
    ).splitlines()
    boundary = next((line[1:].decode("ascii") for line in unpublished if line.startswith(b"-")), None)
    base = boundary or (found.decode("ascii") if found else "HEAD")
    if not unpublished:  # HEAD itself is already published (or is the attachment).
        base = "HEAD"
    scope = "pending" if found or boundary or not unpublished else "uncommitted"
    output = _git(
        directory,
        metadata,
        deadline,
        "diff",
        "--raw",
        "--numstat",
        "-z",
        "--find-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=dirty",
        base,
        "--",
    )
    parts = iter(output.split(b"\0"))
    files = {}
    for part in parts:
        if not part:
            continue
        if part.startswith(b":"):
            status_code = part.rsplit(b" ", 1)[-1].decode("ascii")
            path = next(parts).decode("utf-8", "replace")
            previous_path = None
            if status_code.startswith(("R", "C")):
                previous_path = path
                path = next(parts).decode("utf-8", "replace")
            # Mode-only changes can have no textual additions or deletions.
            files[path] = {
                "path": path,
                "previous_path": previous_path,
                "status": {
                    "A": "added",
                    "D": "deleted",
                    "T": "typechanged",
                    "R": "renamed",
                    "C": "copied",
                }.get(status_code[0], "modified"),
                "additions": 0,
                "deletions": 0,
                "binary": False,
            }
        else:
            added, deleted, raw_path = part.split(b"\t", 2)
            if not raw_path:  # Renames use two additional NUL-delimited paths.
                next(parts)
                raw_path = next(parts)
            path = raw_path.decode("utf-8", "replace")
            binary = added == b"-" or deleted == b"-"
            files[path] = {
                **files[path],
                "additions": None if binary else int(added),
                "deletions": None if binary else int(deleted),
                "binary": binary,
            }
    untracked = _git(directory, metadata, deadline, "ls-files", "--others", "--exclude-standard", "-z")
    budget = [32 * 1024 * 1024]
    for raw_path in untracked.split(b"\0"):
        if raw_path:
            if time.monotonic() >= deadline:
                raise TimeoutError("Change summary timed out")
            path = raw_path.decode("utf-8", "replace")
            files[path] = _untracked(directory, path, budget)
    ordered = sorted(files.values(), key=lambda item: item["path"])
    return {
        "files": ordered,
        "additions": sum(item["additions"] or 0 for item in ordered),
        "deletions": sum(item["deletions"] or 0 for item in ordered),
        "file_count": len(ordered),
        "scope": scope,
    }


def chat_changes(chat: dict[str, Any]) -> dict[str, Any]:
    chat_id = chat["id"]
    if not re.fullmatch(r"[a-f0-9]{12}", chat_id):
        raise ValueError("Invalid chat ID")
    results = []
    deadline = time.monotonic() + 12
    for project_id in chat.get("project_ids", []):
        project = db.one("SELECT id, full_name FROM projects WHERE id = ?", (project_id,))
        if not project:
            continue
        try:
            if not re.fullmatch(r"[a-f0-9]{32}", project_id):
                raise ValueError("Invalid project ID")
            summary = _summary(project_id, chat_id, deadline)
            results.append(project | summary | {"error": None})
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
            # Never present a failed read as a clean worktree or expose Git stderr/configuration.
            results.append(project | {"error": "Changes unavailable; retry shortly"})
    return {"projects": results}
