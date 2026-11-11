import asyncio
import os
import subprocess
import threading
import time
from unittest.mock import AsyncMock

import pytest
from nautionette.chat_sessions import session_lock, worktree_lock
from nautionette_backend import projects
from nautionette_backend import worktree_maintenance as maintenance
from nautionette_backend.worktree_policy import DEFAULTS

from . import test_project_changes

workspace = test_project_changes.workspace


@pytest.fixture
def retained(workspace, db, monkeypatch):
    create, git = workspace
    monkeypatch.setattr(maintenance.broker, "chat_agents", AsyncMock(return_value=[]))
    monkeypatch.setattr(maintenance, "_sweep_lock", asyncio.Lock())

    def make(age=4, published=True):
        chat, project_id, path = create()
        _, metadata = maintenance.project_changes.worktree_metadata(project_id, chat["id"])
        common = projects.checkout(project_id) / ".git"
        args = [f"--git-dir={metadata}", f"--work-tree={path}"]
        if published:
            git(path, *args, "update-ref", "refs/remotes/origin/main", "HEAD")
        old = time.time() - age * 86400
        db.execute(
            "UPDATE chats SET last_activity_at = ?, created_at = ? WHERE id = ?", (old, old, chat["id"])
        )
        for file in (path, metadata / "HEAD", metadata / "index"):
            os.utime(file, (old, old))
        return chat, project_id, path, metadata, common, args, git

    return make


def age_again(path, metadata, days):
    old = time.time() - days * 86400
    for file in (path, metadata / "HEAD", metadata / "index"):
        os.utime(file, (old, old))


async def test_clean_pushed_worktree_deleted_without_removing_chat_or_clone(retained, db):
    chat, project, path, metadata, common, args, git = retained()
    (path / "ignored").mkdir()
    (path / "ignored" / "cache").write_bytes(b"reproducible")
    age_again(path, metadata, 4)
    # The agent deliberately locks its registration against broad Git pruning.
    git(path, f"--git-dir={common}", "worktree", "lock", "--reason", "Nautionette chat", str(path))
    preview = await maintenance.sweep()
    assert preview["eligible"] == 1 and preview["deleted"] == 0
    assert (path / "base.txt").exists()
    result = await maintenance.sweep(dry_run=False)
    assert result["deleted"] == 1 and result["errors"] == 0
    assert list(path.iterdir()) == []
    assert not metadata.exists()
    assert common.exists() and db.get_chat(chat["id"])
    assert not git(path, f"--git-dir={common}", "for-each-ref", f"refs/nautionette/chats/{chat['id']}")
    assert list((projects.PROJECTS_DIR / ".retired-worktrees").iterdir()) == []
    # The next turn can create a fresh, independent registered worktree.
    projects.prepare_worktrees(chat["id"], [project])
    git(path, f"--git-dir={common}", "worktree", "add", "--detach", str(path), "HEAD")
    assert (path / "base.txt").exists()


@pytest.mark.parametrize("kind", ["unstaged", "staged", "untracked", "unpublished"])
@pytest.mark.parametrize("days,eligible", [(4, False), (31, True)])
def test_changed_and_unpublished_use_longer_tier(retained, kind, days, eligible):
    chat, project, path, metadata, _, args, git = retained(age=days)
    if kind == "untracked":
        (path / "new.txt").write_text("unfinished")
    else:
        (path / "base.txt").write_text("unfinished")
        if kind in {"staged", "unpublished"}:
            git(path, *args, "add", "base.txt")
        if kind == "unpublished":
            git(path, *args, "commit", "-m", "Not pushed")
    age_again(path, metadata, days)
    result = maintenance.inspect(project, chat["id"], DEFAULTS, time.time())
    assert result["kind"] == "changed"
    assert (result["reason"] == "eligible") is eligible


def test_absent_remote_refs_cannot_prove_publication(retained):
    chat, project, _, _, _, _, _ = retained(published=False)
    assert maintenance.inspect(project, chat["id"], DEFAULTS, time.time())["kind"] == "changed"


@pytest.mark.parametrize("key", ["worktree_cleanup_enabled", "worktree_cleanup_clean_enabled"])
async def test_switches_disable_deletion(retained, db, key):
    _, _, path, *_ = retained()
    db.save_settings({key: False})
    result = await maintenance.sweep(dry_run=False)
    assert result["deleted"] == 0
    assert (path / "base.txt").exists()


async def test_changed_switch_protects_old_local_work(retained, db):
    _, _, path, metadata, *_ = retained(age=31, published=False)
    db.save_settings({"worktree_cleanup_changed_enabled": False})
    age_again(path, metadata, 31)
    assert (await maintenance.sweep(dry_run=False))["deleted"] == 0
    assert (path / "base.txt").exists()


@pytest.mark.parametrize("lock", [session_lock, worktree_lock])
async def test_shared_reservations_protect_active_agents_and_branch_snapshots(retained, lock):
    chat, _, path, *_ = retained(age=31)
    with lock(chat["id"]):
        result = await maintenance.sweep(dry_run=False)
    assert result["active"] == 1 and result["deleted"] == 0
    assert (path / "base.txt").exists()


async def test_surviving_container_after_broker_restart_is_protected(retained, monkeypatch):
    _, _, path, *_ = retained(age=31)
    monkeypatch.setattr(maintenance.broker, "chat_agents", AsyncMock(return_value=[{"chat_id": "survivor"}]))
    assert (await maintenance.sweep(dry_run=False))["active"] == 1
    assert (path / "base.txt").exists()


async def test_broker_failure_fails_closed(retained, monkeypatch):
    _, _, path, *_ = retained(age=31)
    monkeypatch.setattr(maintenance.broker, "chat_agents", AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        await maintenance.sweep(dry_run=False)
    assert (path / "base.txt").exists()


async def test_activity_and_queued_turn_are_rechecked_before_delete(retained, db):
    chat, project, path, *_ = retained(age=31)
    projects.touch_worktree(project, chat["id"])
    assert (await maintenance.sweep(dry_run=False))["deleted"] == 0
    assert (path / "base.txt").exists()


async def test_configured_batch_rotates_instead_of_starving_old_worktrees(retained, db):
    first = retained()
    second = retained()
    db.save_settings({"worktree_cleanup_batch_size": 1})
    one = await maintenance.sweep(dry_run=False)
    two = await maintenance.sweep(dry_run=False)
    assert one["checked"] == two["checked"] == 1
    assert one["deleted"] == two["deleted"] == 1
    assert list(first[2].iterdir()) == list(second[2].iterdir()) == []


async def test_old_dirty_worktree_is_actually_removed(retained):
    _, _, path, metadata, *_ = retained(age=31)
    (path / "base.txt").write_text("discard per configured policy")
    age_again(path, metadata, 31)
    result = await maintenance.sweep(dry_run=False)
    assert result["deleted"] == 1 and result["items"][0]["kind"] == "changed"
    assert list(path.iterdir()) == []


async def test_queued_turn_prevents_retirement(retained, db):
    chat, _, path, *_ = retained(age=31)
    message = db.add_message(chat["id"], "system", "queued input")
    db.execute(
        "INSERT INTO chat_turns (id, chat_id, user_id, state) VALUES (?,?,?,?)",
        ("queued", chat["id"], message["id"], "queued"),
    )
    result = await maintenance.sweep(dry_run=False)
    assert result["active"] == 1 and result["deleted"] == 0
    assert (path / "base.txt").exists()


def test_other_project_activity_does_not_extend_known_worktree_age(retained, db):
    chat, project, _, *_ = retained()
    db.execute(
        "INSERT INTO project_worktree_activity VALUES (?,?,?)", (project, chat["id"], time.time() - 4 * 86400)
    )
    db.add_message(chat["id"], "user", "Now working on another project")
    assert maintenance.inspect(project, chat["id"], DEFAULTS, time.time())["reason"] == "eligible"


async def test_preparation_waits_for_retirement_reservation(retained):
    chat, project, _, *_ = retained()
    with worktree_lock(chat["id"]):
        task = asyncio.create_task(asyncio.to_thread(projects.prepare_worktrees, chat["id"], [project]))
        await asyncio.sleep(0.1)
        assert not task.done()
    result = await task
    assert result["project_baselines"] == {project: "main"}


async def test_unsafe_git_pointer_and_timeout_never_count_as_clean(retained, monkeypatch):
    _, _, path, *_ = retained(age=31)
    (path / ".git").unlink()
    (path / ".git").symlink_to("/etc/passwd")
    assert (await maintenance.sweep(dry_run=False))["errors"] == 1
    assert (path / "base.txt").exists()


async def test_inspection_failure_is_not_changed_tier_permission(retained, monkeypatch):
    _, _, path, *_ = retained(age=31)

    def fail(*args):
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(maintenance.project_changes, "_git", fail)
    assert (await maintenance.sweep(dry_run=False))["errors"] == 1
    assert (path / "base.txt").exists()


async def test_interrupted_recursive_removal_resumes_from_quarantine(retained, monkeypatch):
    _, _, path, *_ = retained()
    original = maintenance.shutil.rmtree

    def fail_once(directory, *args, **kwargs):
        if directory.parent.name == ".retired-worktrees":
            raise OSError("interrupted")
        return original(directory, *args, **kwargs)

    monkeypatch.setattr(maintenance.shutil, "rmtree", fail_once)
    assert (await maintenance.sweep(dry_run=False))["errors"] == 1
    assert list(path.iterdir()) == []
    monkeypatch.setattr(maintenance.shutil, "rmtree", original)
    await maintenance.sweep(dry_run=False)
    assert list((projects.PROJECTS_DIR / ".retired-worktrees").iterdir()) == []


async def test_failed_quarantine_does_not_block_other_worktrees(retained, monkeypatch):
    _, _, path, *_ = retained()
    blocked = projects.PROJECTS_DIR / ".retired-worktrees" / ("a" * 32)
    blocked.mkdir(parents=True)
    original = maintenance.shutil.rmtree

    def fail_one(directory, *args, **kwargs):
        if directory == blocked:
            raise PermissionError("retry later")
        return original(directory, *args, **kwargs)

    monkeypatch.setattr(maintenance.shutil, "rmtree", fail_one)
    result = await maintenance.sweep(dry_run=False)
    assert result["errors"] == 1 and result["deleted"] == 1
    assert list(path.iterdir()) == [] and blocked.exists()


async def test_cancellation_keeps_reservation_until_thread_finishes():
    entered, finish = threading.Event(), threading.Event()

    def operation():
        entered.set()
        finish.wait(timeout=5)

    task = asyncio.create_task(maintenance.run_blocking(operation))
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "key,value",
    [
        ("worktree_cleanup_enabled", "false"),
        ("worktree_cleanup_changed_enabled", 1),
        ("worktree_cleanup_clean_days", 0),
        ("worktree_cleanup_clean_days", True),
        ("worktree_cleanup_changed_days", 1.5),
        ("worktree_cleanup_interval_hours", 169),
        ("worktree_cleanup_batch_size", 0),
    ],
)
def test_settings_reject_invalid_retention_atomically(client, key, value):
    before = client.get("/api/settings").json()["settings"]
    assert (
        client.put("/api/settings", json={"worktree_cleanup_clean_days": 10, key: value}).status_code == 422
    )
    assert client.get("/api/settings").json()["settings"] == before


def test_settings_status_save_reset_and_authenticated_preview(client, anonymous):
    assert anonymous.get("/api/settings/worktrees").status_code == 401
    assert anonymous.post("/api/settings/worktrees/cleanup", json={}).status_code == 401
    assert client.get("/api/settings/worktrees").json()["policy"] == DEFAULTS
    response = client.put("/api/settings", json={"worktree_cleanup_clean_days": 7})
    assert response.json()["settings"]["worktree_cleanup_clean_days"] == 7
    response = client.put("/api/settings", json={"worktree_cleanup_clean_days": None})
    assert response.json()["settings"]["worktree_cleanup_clean_days"] == 3
    assert client.post("/api/settings/worktrees/cleanup", json={"dry_run": False}).status_code == 422
