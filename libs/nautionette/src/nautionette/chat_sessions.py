"""Fixed paths for chat-scoped native Pi state, never a caller-selected mount."""

from __future__ import annotations

import fcntl
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

SESSIONS_DIR = Path(os.environ.get("CHAT_SESSIONS_DIR", "/chat-sessions"))
SESSION_FILE = "session.jsonl"


def chat_directory(chat_id: str) -> Path:
    if not isinstance(chat_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", chat_id):
        raise ValueError("Invalid chat session ID")
    directory = SESSIONS_DIR / chat_id
    if SESSIONS_DIR.is_symlink() or directory.is_symlink():
        raise ValueError("Chat session directory must not be a symlink")
    return directory


@contextmanager
def session_lock(chat_id: str):
    """Keep the lock inode outside the agent's subpath, including after deletion."""
    chat_directory(chat_id)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(SESSIONS_DIR / f".{chat_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        # The broker runs as root; the backend runs as 10001. Both must open the
        # same lock inode to synchronize deletion with container startup.
        if os.geteuid() == 0:
            os.fchown(fd, 10001, 10001)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("This chat's native session is already in use") from exc
        yield
    finally:
        os.close(fd)


@contextmanager
def worktree_lock(chat_id: str, *, wait_seconds: float = 0):
    """Fence worktree preparation, agent startup and retention across processes.

    This is separate from the native-session lock: preparation precedes native
    session ownership. Keep the inode outside any directory retention removes.
    """
    chat_directory(chat_id)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(SESSIONS_DIR / f".worktrees-{chat_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.geteuid() == 0:
            os.fchown(fd, 10001, 10001)
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError("This chat's project worktrees are in use; retry shortly") from exc
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def session_exists(chat_id: str) -> bool:
    path = chat_directory(chat_id) / SESSION_FILE
    if path.is_symlink():
        raise ValueError("Chat session file must not be a symlink")
    return path.exists()
