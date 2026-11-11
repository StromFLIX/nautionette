"""Age-based whole-worktree retention; never inventory dependency/file contents."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from nautionette.chat_sessions import session_lock, worktree_lock

from . import project_changes, projects, worktree_policy
from .background import run_blocking
from .clients import broker
from .db import db
from .runtime import runtime

log = logging.getLogger("nautionette.worktrees")
STATUS_KEY = "worktree_cleanup_status"
_sweep_lock = asyncio.Lock()


def policy() -> dict:
    result = {key: runtime(key) for key in worktree_policy.DEFAULTS}
    worktree_policy.validate(result)
    return result


def status() -> dict:
    return {"policy": policy(), "last_run": db.get_setting(STATUS_KEY, None)}


def candidates() -> list[tuple[str, str]]:
    root = projects.PROJECTS_DIR
    sessions = project_changes._inside(root / ".sessions", root)
    if not sessions.exists():
        return []
    result = []
    # Exactly two directory levels. Do not recurse into repositories or caches.
    for project in sessions.iterdir():
        if re.fullmatch(r"[a-f0-9]{32}", project.name) and not project.is_symlink() and project.is_dir():
            for chat in project.iterdir():
                if re.fullmatch(r"[a-f0-9]{12}", chat.name) and not chat.is_symlink() and chat.is_dir():
                    if (chat / ".git").exists() or (chat / ".git").is_symlink():
                        result.append((project.name, chat.name))
    return sorted(result)


def _busy(chat_id: str) -> bool:
    return bool(
        db.one(
            "SELECT id FROM chat_turns WHERE chat_id = ? AND state IN ('running','queued') LIMIT 1",
            (chat_id,),
        )
    )


def _last_used(project_id: str, chat_id: str, directory: Path, metadata: Path) -> float:
    activity = db.one(
        "SELECT last_used_at FROM project_worktree_activity WHERE project_id = ? AND chat_id = ?",
        (project_id, chat_id),
    )
    chat = db.one("SELECT last_activity_at, created_at FROM chats WHERE id = ?", (chat_id,))
    # Legacy worktrees have no activity row. Existing chat activity and shallow
    # Git/root timestamps are conservative fallbacks, not recursive file scans.
    return max(
        activity["last_used_at"] if activity else 0,
        (chat["last_activity_at"] or chat["created_at"]) if chat and not activity else 0,
        directory.stat().st_mtime,
        *(
            project_changes._inside(path, projects.PROJECTS_DIR).stat().st_mtime
            for path in (metadata / "HEAD", metadata / "index")
            if path.exists()
        ),
    )


def inspect(project_id: str, chat_id: str, settings: dict, now: float) -> dict:
    resolved = project_changes.worktree_metadata(project_id, chat_id)
    if resolved is None:
        return {"reason": "missing"}
    directory, metadata = resolved
    if project_changes._inside(metadata / "commondir", projects.PROJECTS_DIR).read_text().strip() != "../..":
        raise ValueError("Unexpected worktree common directory")
    age = (now - _last_used(project_id, chat_id, directory, metadata)) / 86400
    enabled_days = [
        settings[f"worktree_cleanup_{kind}_days"]
        for kind in ("clean", "changed")
        if settings[f"worktree_cleanup_{kind}_enabled"]
    ]
    if not enabled_days or age <= min(enabled_days):
        return {"reason": "recent"}
    deadline = time.monotonic() + 15
    git = lambda *args: project_changes._git(directory, metadata, deadline, *args)  # noqa: E731
    dirty = bool(
        git("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=none")
    )
    # A clean index is not proof of publication, nor is the chat's attachment ref.
    # Push updates origin tracking refs, including for detached HEAD worktrees.
    published = (
        bool(git("for-each-ref", "--format=%(objectname)", "refs/remotes/origin/"))
        and not git(
            "rev-list",
            "--max-count=1",
            "HEAD",
            "--not",
            "--remotes=origin",
        ).strip()
    )
    kind = "changed" if dirty or not published else "clean"
    eligible = (
        settings[f"worktree_cleanup_{kind}_enabled"] and age > settings[f"worktree_cleanup_{kind}_days"]
    )
    return {"reason": "eligible" if eligible else "retained", "kind": kind, "age_days": age}


def _retire(project_id: str, chat_id: str, settings: dict, now: float) -> tuple[dict, Path | None]:
    # Recheck after Docker inventory, under both shared locks. No setting, age or
    # clean/pushed decision from a preview is trusted by a later deletion.
    current = policy()
    if current != settings or not current["worktree_cleanup_enabled"] or _busy(chat_id):
        return {"reason": "changed_during_check"}, None
    result = inspect(project_id, chat_id, current, now)
    if result["reason"] != "eligible":
        return result, None
    directory, metadata = project_changes.worktree_metadata(project_id, chat_id)
    root = projects.PROJECTS_DIR
    quarantine = project_changes._inside(root / ".retired-worktrees", root)
    quarantine.mkdir(mode=0o700, exist_ok=True)
    retired = quarantine / uuid.uuid4().hex
    reference = f"refs/nautionette/chats/{chat_id}"
    deadline = time.monotonic() + 5
    refs = (
        project_changes._git(
            directory,
            metadata,
            deadline,
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            reference,
        )
        .decode("ascii")
        .splitlines()
    )
    expected = next((line.split()[1] for line in refs if line.split()[0] == reference), None)
    # Atomically detach before slow recursive deletion. A new turn can recreate
    # its worktree after the locks release; interrupted removals resume below.
    directory.rename(retired)
    directory.mkdir(mode=0o700)
    deadline = time.monotonic() + 5
    if expected:
        project_changes._git(directory, metadata, deadline, "update-ref", "-d", reference, expected)
    shutil.rmtree(metadata)
    db.execute(
        "DELETE FROM project_worktree_activity WHERE project_id = ? AND chat_id = ?", (project_id, chat_id)
    )
    return result | {"reason": "deleted"}, retired


def _collect_retired() -> int:
    root = projects.PROJECTS_DIR
    quarantine = project_changes._inside(root / ".retired-worktrees", root)
    if not quarantine.exists():
        return 0
    errors = 0
    for directory in quarantine.iterdir():
        if re.fullmatch(r"[a-f0-9]{32}", directory.name):
            try:
                project_changes._inside(directory, root)
                if directory.is_dir():
                    shutil.rmtree(directory)
            except FileNotFoundError:
                pass  # Another backend may have finished collecting it.
            except (OSError, ValueError):
                errors += 1
                log.warning("Retired worktree collection failed: %s", directory.name)
    return errors


async def sweep(*, dry_run: bool = True) -> dict:
    async with _sweep_lock:
        settings = policy()
        report = {
            "at": time.time(),
            "dry_run": dry_run,
            "checked": 0,
            "eligible": 0,
            "deleted": 0,
            "active": 0,
            "errors": 0,
            "items": [],
        }
        if not settings["worktree_cleanup_enabled"]:
            return report | {"disabled": True}
        # Fail closed if Docker/broker is unavailable, including for orphaned
        # containers that survived a broker restart and no longer hold a flock.
        await broker.chat_agents()
        if not dry_run:
            report["errors"] += await asyncio.to_thread(_collect_retired)
        rows = candidates()
        previous = db.get_setting(STATUS_KEY, {}) or {}
        cursor = tuple(previous.get("cursor", []))
        rows = [row for row in rows if row > cursor] + [row for row in rows if row <= cursor]
        for project_id, chat_id in rows[: settings["worktree_cleanup_batch_size"]]:
            report["checked"] += 1
            report["cursor"] = [project_id, chat_id]
            item = {"project_id": project_id, "chat_id": chat_id}
            retired = None
            try:
                with worktree_lock(chat_id), session_lock(chat_id):
                    if _busy(chat_id) or await broker.chat_agents(chat_id):
                        report["active"] += 1
                        continue
                    if dry_run:
                        result = await run_blocking(inspect, project_id, chat_id, settings, time.time())
                    else:
                        result, retired = await run_blocking(
                            _retire, project_id, chat_id, settings, time.time()
                        )
                if result["reason"] in {"eligible", "deleted"}:
                    report["eligible"] += 1
                    report["deleted"] += int(result["reason"] == "deleted")
                report["items"].append(item | result)
                if retired is not None:
                    await asyncio.to_thread(shutil.rmtree, retired)
            except RuntimeError:
                report["active"] += 1
            except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
                report["errors"] += 1
                report["items"].append(item | {"reason": "inspection_or_removal_failed"})
                log.warning("Worktree retention failed: project=%s chat=%s", project_id, chat_id)
        if not dry_run:
            db.save_settings({STATUS_KEY: report})
        return report


async def run() -> None:
    while True:
        # Poll settings so disabling/changing the interval takes effect without a restart.
        await asyncio.sleep(60)
        try:
            settings = policy()
            last = db.get_setting(STATUS_KEY, {}) or {}
            if settings["worktree_cleanup_enabled"] and time.time() - last.get("at", 0) >= (
                settings["worktree_cleanup_interval_hours"] * 3600
            ):
                await sweep(dry_run=False)
        except Exception:
            log.exception("Worktree retention failed; no idle state was assumed")
            db.save_settings(
                {STATUS_KEY: {"at": time.time(), "errors": 1, "error": "Cleanup failed; retrying"}}
            )
