"""One writer and one volume subpath per chat, including project-free chats."""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager

from docker.types import Mount
from nautionette.chat_sessions import SESSIONS_DIR, chat_directory, session_lock

from . import chat_agents, daemon

MAX_SESSION_BYTES = int(os.environ.get("CHAT_SESSION_MAX_BYTES", str(256 * 1024 * 1024)))


@contextmanager
def claim(chat_id: str):
    if not chat_id:
        yield []
        return
    if tuple(map(int, daemon.client().api._version.split("."))) < (1, 45):
        raise RuntimeError("Chat session isolation requires Docker Engine 26+ with API 1.45+")
    directory = chat_directory(chat_id)
    with session_lock(chat_id):
        if chat_agents.containers(chat_id):
            raise RuntimeError("A previous chat agent still exists; clean it up before resuming")
        directory.mkdir(mode=0o700, exist_ok=True)
        # Same owner whether the current call has selected projects or not.
        os.chown(directory, 10001, 10001)
        os.chmod(directory, 0o700)
        total = 0
        for root, dirs, files in os.walk(directory, followlinks=False):
            for name in [*dirs, *files]:
                path = os.path.join(root, name)
                if os.path.islink(path):
                    raise ValueError("Symlinks are not allowed in durable chat state")
                os.chown(path, 10001, 10001)
                if os.path.isfile(path):
                    total += os.stat(path).st_size
        if total >= MAX_SESSION_BYTES:
            raise RuntimeError(
                "Chat session storage limit reached; export/delete the chat or raise CHAT_SESSION_MAX_BYTES"
            )
        broker = daemon.client().containers.get(socket.gethostname())
        mounted = [m for m in broker.attrs.get("Mounts", []) if m.get("Destination") == str(SESSIONS_DIR)]
        if len(mounted) != 1 or mounted[0].get("Type") != "volume" or not mounted[0].get("Name"):
            raise ValueError("The broker requires a named Docker volume at CHAT_SESSIONS_DIR")
        mount = Mount(target="/chat-session", source=mounted[0]["Name"], type="volume")
        mount["VolumeOptions"] = {"Subpath": chat_id}
        yield [mount]
