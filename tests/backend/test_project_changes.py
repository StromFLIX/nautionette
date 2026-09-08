import os
import subprocess

import pytest
from nautionette_backend import project_changes, projects


@pytest.fixture
def workspace(db, tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.test",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.test",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )

    def git(path, *args):
        return (
            subprocess.check_output(  # noqa: S603, S607
                ["/usr/bin/git", "-C", str(path), *args],
                env=environment,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

    def create(repository_id=1, chat=None):
        project_id = f"{repository_id:032x}"
        checkout = projects.checkout(project_id)
        if not checkout.exists():
            checkout.mkdir()
            git(checkout, "init", "-b", "main")
            (checkout / "base.txt").write_text("before\nkeep\n")
            (checkout / "delete.txt").write_text("remove\n")
            (checkout / ".gitignore").write_text("ignored/\n")
            git(checkout, "add", ".")
            git(checkout, "commit", "-m", "Initial")
            db.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?,?)",
                (project_id, repository_id, f"owner/repo-{repository_id}", "main", "ready", ""),
            )
        chat = chat or db.create_chat("Changes", "default")
        db.update_chat(chat["id"], {"project_ids": [project_id]})
        path = tmp_path / ".sessions" / project_id / chat["id"]
        path.parent.mkdir(parents=True, exist_ok=True)
        git(checkout, "worktree", "add", "--detach", str(path), "HEAD")
        git(checkout, "update-ref", f"refs/nautionette/chats/{chat['id']}", "HEAD")
        # Simulate the agent's metadata mount, which is absent in the backend container.
        pointer = path / ".git"
        pointer.write_text(
            pointer.read_text().replace(str(checkout / ".git"), f"/project-repositories/{project_id}")
        )
        return db.get_chat(chat["id"]), project_id, path

    return create, git


def test_changes_include_commits_index_working_tree_untracked_and_binary(client, workspace):
    create, git = workspace
    chat, project_id, path = create()
    (path / "base.txt").write_text("after\nkeep\nextra\n")
    (path / "delete.txt").unlink()
    (path / "src").mkdir()
    (path / "src/new\tfile.txt").write_text("one\ntwo")
    (path / "asset.png").write_bytes(b"\x00binary")
    (path / "ignored").mkdir()
    (path / "ignored/secret").write_text("not visible")
    endpoint = f"/api/chats/{chat['id']}/project-changes"
    project = client.get(endpoint).json()["projects"][0]
    assert project["error"] is None
    assert (project["additions"], project["deletions"], project["file_count"]) == (4, 2, 4)
    files = {file["path"]: file for file in project["files"]}
    assert files["asset.png"]["binary"] is True
    assert files["src/new\tfile.txt"]["additions"] == 2
    assert files["delete.txt"]["status"] == "deleted"
    assert files["base.txt"]["status"] == "modified"
    # Work directly against the mapped gitdir, just as the backend does.
    metadata = projects.checkout(project_id) / ".git"
    name = (path / ".git").read_text().strip().split("/")[-1]
    args = [f"--git-dir={metadata / 'worktrees' / name}", f"--work-tree={path}"]
    git(path, *args, "add", ".")
    staged = client.get(endpoint).json()["projects"][0]
    assert (staged["additions"], staged["deletions"], staged["file_count"]) == (4, 2, 4)
    git(path, *args, "commit", "-m", "Changes")
    assert client.get(endpoint).json()["projects"][0] == staged
    assert not (path / "index.lock").exists()


def test_changes_are_scoped_to_selected_projects_and_chat(client, anonymous, workspace, db):
    create, _ = workspace
    first, first_id, first_path = create()
    second, _, second_path = create()
    (first_path / "first.txt").write_text("first\n")
    (second_path / "second.txt").write_text("second\n")
    _, second_id, other_path = create(2, first)
    (other_path / "other.txt").write_text("other\n")
    endpoint = f"/api/chats/{first['id']}/project-changes"
    assert anonymous.get(endpoint).status_code == 401
    assert client.get("/api/chats/missing/project-changes").status_code == 404
    db.update_chat(first["id"], {"project_ids": [first_id, second_id]})
    results = client.get(endpoint).json()["projects"]
    assert [item["id"] for item in results] == [first_id, second_id]
    assert [file["path"] for file in results[0]["files"]] == ["first.txt"]
    db.update_chat(first["id"], {"project_ids": []})
    assert client.get(endpoint).json() == {"projects": []}


def test_clean_missing_and_legacy_workspaces(client, workspace):
    create, git = workspace
    chat, project_id, path = create()
    endpoint = f"/api/chats/{chat['id']}/project-changes"
    assert client.get(endpoint).json()["projects"][0]["file_count"] == 0
    git(projects.checkout(project_id), "update-ref", "-d", f"refs/nautionette/chats/{chat['id']}")
    assert client.get(endpoint).json()["projects"][0]["scope"] == "uncommitted"
    (path / ".git").unlink()
    assert client.get(endpoint).json()["projects"][0]["file_count"] == 0


def test_unsafe_worktree_metadata_is_rejected(client, workspace, tmp_path):
    create, _ = workspace
    chat, _, path = create()
    endpoint = f"/api/chats/{chat['id']}/project-changes"
    pointer = path / ".git"
    pointer.write_text(f"gitdir: {tmp_path}/outside")
    result = client.get(endpoint).json()["projects"][0]
    assert result["error"]
    assert "file_count" not in result
    pointer.unlink()
    pointer.symlink_to(tmp_path / "absent")
    assert client.get(endpoint).json()["projects"][0]["error"]


def test_untracked_symlinks_large_files_and_mode_only_changes(client, workspace, tmp_path):
    create, _ = workspace
    chat, _, path = create()
    secret = tmp_path / "secret"
    secret.write_text("secret\n" * 100)
    (path / "link").symlink_to(secret)
    os.mkfifo(path / "pipe")
    (path / "base.txt").chmod(0o755)
    (path / "large").write_bytes(b"x" * (project_changes.MAX_FILE_BYTES + 1))
    result = client.get(f"/api/chats/{chat['id']}/project-changes").json()["projects"][0]
    assert result["error"] is None
    files = {item["path"]: item for item in result["files"]}
    assert files["link"]["additions"] == 1
    assert files["large"]["additions"] is None
    assert files["base.txt"]["additions"] == 0


def test_renamed_files_count_once_with_relative_paths(client, workspace):
    create, git = workspace
    chat, project_id, path = create()
    metadata = projects.checkout(project_id) / ".git"
    name = (path / ".git").read_text().strip().split("/")[-1]
    git(
        path,
        f"--git-dir={metadata / 'worktrees' / name}",
        f"--work-tree={path}",
        "mv",
        "base.txt",
        "renamed\tfile.txt",
    )
    result = client.get(f"/api/chats/{chat['id']}/project-changes").json()["projects"][0]
    assert result["error"] is None
    assert result["file_count"] == 1
    assert result["additions"] == result["deletions"] == 0
    assert result["files"][0]["path"] == "renamed\tfile.txt"
    assert result["files"][0]["previous_path"] == "base.txt"
    assert result["files"][0]["status"] == "renamed"


@pytest.fixture
def published_workspace(workspace, tmp_path):
    create, git = workspace
    chat, project_id, path = create()
    checkout = projects.checkout(project_id)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "push", "origin", "HEAD:refs/heads/main")
    name = (path / ".git").read_text().strip().split("/")[-1]

    def run(*args):
        return git(path, f"--git-dir={checkout / '.git/worktrees' / name}", f"--work-tree={path}", *args)

    return chat, path, run, git, checkout


@pytest.mark.parametrize("branch", ["main", "feature/chat-changes"])
def test_push_clears_committed_changes_but_keeps_dirty_files(client, published_workspace, branch):
    chat, path, git, _, _ = published_workspace
    endpoint = f"/api/chats/{chat['id']}/project-changes"
    (path / "base.txt").write_text("published\n")
    git("add", ".")
    git("commit", "-m", "Publish this")
    assert client.get(endpoint).json()["projects"][0]["file_count"] == 1
    git("push", "origin", f"HEAD:refs/heads/{branch}")
    clean = client.get(endpoint).json()["projects"][0]
    assert clean["error"] is None
    assert clean["file_count"] == clean["additions"] == clean["deletions"] == 0
    (path / "base.txt").write_text("published\nnot yet staged\n")
    (path / "staged.txt").write_text("staged\n")
    git("add", "staged.txt")
    (path / "new.txt").write_text("untracked\n")
    pending = client.get(endpoint).json()["projects"][0]
    assert (pending["file_count"], pending["additions"], pending["deletions"]) == (3, 3, 0)


def test_partial_push_only_removes_published_changes(client, published_workspace):
    chat, path, git, _, _ = published_workspace
    endpoint = f"/api/chats/{chat['id']}/project-changes"
    (path / "published.txt").write_text("published\n")
    git("add", ".")
    git("commit", "-m", "First")
    (path / "pending.txt").write_text("pending\n")
    git("add", ".")
    git("commit", "-m", "Second")
    git("push", "origin", "HEAD~1:refs/heads/main")
    pending = client.get(endpoint).json()["projects"][0]
    assert pending["error"] is None
    assert [file["path"] for file in pending["files"]] == ["pending.txt"]
    git("push", "origin", "HEAD:refs/heads/main")
    assert client.get(endpoint).json()["projects"][0]["file_count"] == 0


@pytest.mark.parametrize("diverged", [False, True])
def test_remote_ahead_or_diverged_does_not_appear_as_chat_changes(client, published_workspace, diverged):
    chat, path, git, repository_git, checkout = published_workspace
    endpoint = f"/api/chats/{chat['id']}/project-changes"
    (checkout / "other-chat.txt").write_text("unrelated\n")
    repository_git(checkout, "add", ".")
    repository_git(checkout, "commit", "-m", "Other work")
    repository_git(checkout, "push", "origin", "HEAD:refs/heads/main")
    if diverged:
        (path / "my-change.txt").write_text("pending\n")
        git("add", ".")
        git("commit", "-m", "My work")
    pending = client.get(endpoint).json()["projects"][0]
    assert pending["error"] is None
    assert [file["path"] for file in pending["files"]] == (["my-change.txt"] if diverged else [])


def test_git_failure_is_not_reported_as_clean(client, workspace, monkeypatch):
    create, _ = workspace
    chat, _, _ = create()

    def fail(*args):
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(project_changes, "_git", fail)
    result = client.get(f"/api/chats/{chat['id']}/project-changes").json()["projects"][0]
    assert result["error"]
    assert "file_count" not in result
