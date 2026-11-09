import time

import pytest
from nautionette_backend import chat_attachments, chat_branches, project_changes, projects

from . import test_project_changes

workspace = test_project_changes.workspace


def test_branch_preserves_head_index_untracked_deleted_and_ignored_files(client, db, workspace):
    create, git = workspace
    chat, project_id, path = create()
    _, metadata = project_changes.worktree_metadata(project_id, chat["id"])
    args = [f"--git-dir={metadata}", f"--work-tree={path}"]
    (path / "base.txt").write_text("committed idea\n")
    git(path, *args, "add", ".")
    git(path, *args, "commit", "-m", "Local idea")
    head = git(path, *args, "rev-parse", "HEAD")
    (path / "base.txt").write_text("staged idea\n")
    git(path, *args, "add", "base.txt")
    (path / "base.txt").write_text("unstaged idea\n")
    (path / "delete.txt").unlink()
    (path / "new.txt").write_text("untracked\n")
    (path / "ignored").mkdir()
    (path / "ignored/cache").write_text("ignored but retained\n")
    (path / "link").symlink_to("base.txt")
    before = git(path, *args, "status", "--porcelain")
    response = client.post(f"/api/chats/{chat['id']}/branch")
    assert response.status_code == 200, response.text
    branch = response.json()
    target, target_metadata = project_changes.worktree_metadata(project_id, branch["id"])
    target_args = [f"--git-dir={target_metadata}", f"--work-tree={target}"]
    assert target_metadata != metadata
    assert git(target, *target_args, "rev-parse", "HEAD") == head
    assert git(target, *target_args, "status", "--porcelain") == before
    assert git(target, *target_args, "show", ":base.txt") == "staged idea"
    assert (target / "base.txt").read_text() == "unstaged idea\n"
    assert (target / "ignored/cache").read_text() == "ignored but retained\n"
    assert (target / "link").readlink() == (path / "link").readlink()
    assert not (target / "delete.txt").exists()
    assert not (target_metadata / "HEAD").is_symlink()
    assert client.get(f"/api/chats/{branch['id']}/project-changes").json()["projects"][0]["error"] is None
    (target / "base.txt").write_text("different direction\n")
    git(target, *target_args, "add", "base.txt")
    git(target, *target_args, "commit", "-m", "Branched idea")
    assert git(path, *args, "rev-parse", "HEAD") == head
    assert (path / "base.txt").read_text() == "unstaged idea\n"
    assert git(path, *args, "status", "--porcelain") == before
    baseline = f"refs/nautionette/chats/{chat['id']}"
    target_baseline = f"refs/nautionette/chats/{branch['id']}"
    assert git(target, *target_args, "rev-parse", target_baseline) == git(path, *args, "rev-parse", baseline)


@pytest.mark.parametrize("failure", ["file_copy", "sql_copy"])
def test_failed_project_snapshot_removes_target_worktree_and_registration(
    client, db, workspace, monkeypatch, failure
):
    create, git = workspace
    chat, project_id, path = create()
    common = projects.checkout(project_id) / ".git"
    original_metadata = set((common / "worktrees").iterdir())
    original_refs = git(common.parent, "for-each-ref", "--format=%(refname)", "refs/nautionette/chats")
    if failure == "file_copy":
        (path / "large.bin").write_bytes(b"not small")
        monkeypatch.setattr(chat_branches, "MAX_COPY_BYTES", 1)
    else:
        file = chat_attachments.store_attachment(chat["id"], b"data", "text/plain", "file.txt")
        db.add_message(chat["id"], "user", "Missing file", {"attachments": [file]})
        db.execute("DELETE FROM chat_images WHERE id = ?", (file["id"],))
    response = client.post(f"/api/chats/{chat['id']}/branch")
    assert response.status_code == 409, response.text
    assert set((common / "worktrees").iterdir()) == original_metadata
    assert list(path.parent.iterdir()) == [path]
    assert len(db.list_chats()) == 1
    assert (
        git(common.parent, "for-each-ref", "--format=%(refname)", "refs/nautionette/chats") == original_refs
    )


def test_branch_copies_retained_deselected_project_work(client, db, workspace):
    create, _ = workspace
    chat, project_id, path = create()
    db.update_chat(chat["id"], {"project_ids": []})
    (path / "idea.txt").write_text("retained idea")
    response = client.post(f"/api/chats/{chat['id']}/branch")
    assert response.status_code == 200, response.text
    branch = response.json()
    assert branch["project_ids"] == []
    target, metadata = project_changes.worktree_metadata(project_id, branch["id"])
    assert (target / "idea.txt").read_text() == "retained idea"
    assert project_changes._git(target, metadata, time.monotonic() + 5, "rev-parse", "HEAD")


def test_branch_rejects_nested_git_pointers_instead_of_sharing_their_index(client, db, workspace):
    create, _ = workspace
    chat, project_id, path = create()
    (path / "submodule").mkdir()
    (path / "submodule/.git").write_text("gitdir: /some/source/metadata\n")
    response = client.post(f"/api/chats/{chat['id']}/branch")
    assert response.status_code == 409, response.text
    assert "submodules" in response.json()["detail"]
    assert list(path.parent.iterdir()) == [path]
    assert len(db.list_chats()) == 1
