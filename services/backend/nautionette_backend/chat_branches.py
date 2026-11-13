"""Independent, idle-chat snapshots of native history, attachments and Git work.

The native session lock fences broker writers. The database transaction fences
message submission/deletion while taking the matching UI snapshot. Never rebuild
native history from the lossy UI projection, or share a worktree/index with a branch.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from nautionette.chat_sessions import SESSION_FILE, chat_directory, session_lock

from . import project_changes, projects
from .db import db

MAX_COPY_BYTES = 1024 * 1024 * 1024
MAX_COPY_FILES = 100_000


def _copy_tree(
    source: Path, target: Path, budget: list[int], *, symlinks: bool = False, skip_git: bool = False
) -> None:
    """Copy without following links or opening special files; bound snapshot work."""
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name == ".git":
            if skip_git:
                continue
            if symlinks:
                raise ValueError(
                    "Branching nested Git repositories or initialized submodules is not supported"
                )
        budget[1] -= 1
        if budget[1] < 0:
            raise ValueError("Too many files to branch this chat")
        info = item.lstat()
        destination = target / item.name
        if stat.S_ISLNK(info.st_mode):
            if not symlinks:
                raise ValueError("Symlinks are not allowed in durable chat state")
            destination.symlink_to(item.readlink())
        elif stat.S_ISDIR(info.st_mode):
            _copy_tree(item, destination, budget, symlinks=symlinks)
        elif stat.S_ISREG(info.st_mode):
            budget[0] -= info.st_size
            if budget[0] < 0:
                raise ValueError("This chat is too large to branch (1 GiB limit)")
            shutil.copy2(item, destination, follow_symlinks=False)
        else:
            raise ValueError("Cannot branch chat state containing special files")


def branch(chat_id: str) -> dict:
    target_id = uuid.uuid4().hex[:12]
    target = chat_directory(target_id)
    target_created = False
    worktrees: list[tuple[Path, Path, Path | None]] = []
    deadline = time.monotonic() + 120
    budget = [MAX_COPY_BYTES, MAX_COPY_FILES]
    try:
        with session_lock(chat_id), db.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            original = connection.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
            if original is None:
                raise HTTPException(404, "Chat not found")
            if connection.execute(
                "SELECT 1 FROM chat_turns WHERE chat_id = ? AND state IN ('running', 'queued')", (chat_id,)
            ).fetchone():
                raise HTTPException(
                    409, "Wait for the response and queued messages to finish before branching"
                )
            if connection.execute("SELECT id FROM chats WHERE id = ?", (target_id,)).fetchone():
                raise ValueError("Branch ID already exists; retry branching")
            target.mkdir(mode=0o700, parents=True)
            target_created = True
            source = dict(original)
            attachment_bytes = connection.execute(
                "SELECT COALESCE(SUM(size), 0) FROM chat_images WHERE chat_id = ? AND message_id IS NOT NULL",
                (chat_id,),
            ).fetchone()[0]
            budget[0] -= attachment_bytes
            if budget[0] < 0:
                raise ValueError("This chat is too large to branch (1 GiB limit)")
            session_id = None
            directory = chat_directory(chat_id)
            if directory.exists():
                _copy_tree(directory, target, budget)
            path = target / SESSION_FILE
            if path.exists():
                # Preserve native entries, compactions, tool results and their parent IDs.
                # Only the session identity changes; the new chat owns a different file.
                temporary = target / "branch-session.tmp"
                with path.open("rb") as reader, temporary.open("wb") as writer:
                    header = json.loads(reader.readline(65536))
                    if header.get("type") != "session" or not header.get("id"):
                        raise ValueError("Invalid native session header")
                    if source["pi_session_id"] and header["id"] != source["pi_session_id"]:
                        raise ValueError("Native Pi session identity mismatch")
                    session_id = str(uuid.uuid4())
                    header["id"] = session_id
                    writer.write((json.dumps(header) + "\n").encode())
                    shutil.copyfileobj(reader, writer)
                temporary.replace(path)
            elif source["pi_session_id"]:
                raise ValueError(
                    "Native history is missing; restore the chat-sessions backup before branching"
                )

            # Include retained work from previously selected projects, not just the
            # current selection. Each target has its own HEAD, index and working files.
            for row in connection.execute("SELECT id FROM projects").fetchall():
                project_id = row["id"]
                if not re.fullmatch(r"[a-f0-9]{32}", project_id):
                    raise ValueError("Invalid project ID")
                resolved = project_changes.worktree_metadata(project_id, chat_id)
                if resolved is None:
                    continue
                working, metadata = resolved
                projects.touch_worktree(project_id, chat_id, connection=connection)
                common = projects.checkout(project_id) / ".git"
                destination = working.parent / target_id
                if destination.exists() or destination.is_symlink():
                    raise ValueError("Branch workspace already exists; retry branching")
                worktrees.append((destination, common, None))
                head = project_changes._git(working, metadata, deadline, "rev-parse", "HEAD").decode().strip()
                project_changes._git(
                    working,
                    common,
                    deadline,
                    "worktree",
                    "add",
                    "--no-checkout",
                    "--detach",
                    "--lock",
                    "--",
                    str(destination),
                    head,
                )
                pointer = destination / ".git"
                new_metadata = Path(pointer.read_text().strip().removeprefix("gitdir: "))
                project_changes._inside(new_metadata, common)
                worktrees[-1] = (destination, common, new_metadata)
                # Copy working files without replacing the new Git registration.
                _copy_tree(working, destination, budget, symlinks=True, skip_git=True)
                pointer.write_text(
                    f"gitdir: /project-repositories/{project_id}/worktrees/{new_metadata.name}\n"
                )
                for index in [metadata / "index", *metadata.glob("sharedindex.*")]:
                    if index.exists():
                        project_changes._inside(index, common)
                        shutil.copy2(index, new_metadata / index.name)
                reference = f"refs/nautionette/chats/{chat_id}"
                baseline = (
                    project_changes._git(
                        working, metadata, deadline, "for-each-ref", "--format=%(objectname)", reference
                    )
                    .decode()
                    .strip()
                    or head
                )
                project_changes._git(
                    working, common, deadline, "update-ref", f"refs/nautionette/chats/{target_id}", baseline
                )

            now = time.time()
            cloned = {
                **source,
                "id": target_id,
                "title": f"Branch: {source['title']}"[:120],
                "parent_chat_id": chat_id,
                "parent_chat_title": source["title"],
                "pi_session_id": session_id,
                "created_at": now,
                "updated_at": now,
                "last_message_at": now,
                "last_user_message_at": now,
                "last_activity_at": now,
                "promoted_to": None,
                "queue_paused": 0,
                "marked_unread": 0,
                "last_read_message_id": None,
                "read_revision": 0,
                "title_state": "manual",
                "title_revision": 0,
            }
            # Column names come exclusively from SQLite's fixed chats schema.
            columns = ", ".join(cloned)
            placeholders = ", ".join("?" for _ in cloned)
            connection.execute(
                f"INSERT INTO chats ({columns}) VALUES ({placeholders})",  # noqa: S608
                tuple(cloned.values()),
            )
            messages = connection.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY rowid", (chat_id,)
            ).fetchall()
            for message in messages:
                message_id = uuid.uuid4().hex[:12]
                meta = json.loads(message["meta"] or "{}")
                for attachment in meta.get("attachments", []):
                    old_id = attachment["id"]
                    new_id = uuid.uuid4().hex
                    copied = connection.execute(
                        "INSERT INTO chat_images "
                        "(id, chat_id, name, mime_type, size, data, created_at) "
                        "SELECT ?, ?, name, mime_type, size, data, created_at "
                        "FROM chat_images WHERE id = ? AND chat_id = ?",
                        (new_id, target_id, old_id, chat_id),
                    )
                    if copied.rowcount != 1:
                        raise ValueError("An original attachment is missing; cannot branch this chat")
                    attachment["id"] = new_id
                # Published outputs remain owned by the original chat (linked in the UI).
                # Do not advertise mutable output IDs as belonging to this branch.
                meta.pop("outputs", None)
                connection.execute(
                    "INSERT INTO messages (id, chat_id, role, content, meta, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        message_id,
                        target_id,
                        message["role"],
                        message["content"],
                        json.dumps(meta),
                        message["created_at"],
                    ),
                )
                connection.execute(
                    "INSERT INTO chat_context_messages (message_id, source_id, imported) "
                    "SELECT ?, source_id, imported FROM chat_context_messages WHERE message_id = ?",
                    (message_id, message["id"]),
                )
                connection.execute(
                    "UPDATE chat_images SET message_id = ? WHERE chat_id = ? AND message_id IS NULL",
                    (message_id, target_id),
                )
                connection.execute(
                    "UPDATE chats SET last_read_message_id = ? WHERE id = ?", (message_id, target_id)
                )
        return db.get_chat(target_id)
    except BaseException:
        if target_created and target.exists():
            shutil.rmtree(target)
        for destination, common, metadata in reversed(worktrees):
            try:
                # Git's removal runs in the backend, not the agent mount namespace.
                if metadata is not None and destination.exists():
                    (destination / ".git").write_text(f"gitdir: {metadata}\n")
                project_changes._git(
                    common.parent,
                    common,
                    time.monotonic() + 5,
                    "worktree",
                    "remove",
                    "--force",
                    "--force",
                    str(destination),
                )
                project_changes._git(
                    common.parent,
                    common,
                    time.monotonic() + 5,
                    "update-ref",
                    "-d",
                    f"refs/nautionette/chats/{target_id}",
                )
            except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
                # Never remove the source or any other chat to recover a failed copy.
                logging.getLogger(__name__).exception("Branch cleanup failed for chat %s", target_id)
        raise
