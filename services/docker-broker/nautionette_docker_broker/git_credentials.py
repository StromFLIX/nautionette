"""Fixed, private credential delivery. No token travels in Docker env or argv."""

from __future__ import annotations

import io
import json
import re
import tarfile
from datetime import datetime
from typing import Any

DIRECTORY = "/tmp/nautionette-git-credentials"  # noqa: S108 - per-container, root-owned directory


def validate(credentials: Any, repositories: set[str] | None = None) -> None:
    if not isinstance(credentials, list) or not 1 <= len(credentials) <= 20:
        raise ValueError("Expected repository-scoped Git credentials")
    names = set()
    for item in credentials:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) for key in ("full_name", "token", "expires_at")
        ):
            raise ValueError("Invalid Git credential")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", item["full_name"])
            or not re.fullmatch(r"[A-Za-z0-9_\-]+", item["token"])
            or len(item["token"]) > 1024
            or item["full_name"] in names
        ):
            raise ValueError("Invalid Git credential")
        try:
            expiry = datetime.fromisoformat(item["expires_at"])
            if expiry.tzinfo is None:
                raise ValueError
        except ValueError:
            raise ValueError("Invalid Git credential expiry") from None
        names.add(item["full_name"])
    if repositories is not None and names != repositories:
        raise ValueError("Git credential repository scope does not match this turn")


def _archive(credentials: list[dict[str, Any]], initial: bool) -> bytes:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        if initial:
            directory = tarfile.TarInfo(DIRECTORY.rsplit("/", 1)[1])
            directory.type = tarfile.DIRTYPE
            # The unprivileged agent cannot replace the directory or its entries.
            directory.mode = 0o711
            tar.addfile(directory)
        name = "current.json" if initial else "pending.json"
        entry = tarfile.TarInfo(f"{directory.name}/{name}" if initial else name)
        raw = json.dumps(credentials).encode()
        entry.size = len(raw)
        entry.uid, entry.gid, entry.mode = 0, 10001, 0o440
        tar.addfile(entry, io.BytesIO(raw))
    return archive.getvalue()


def prepare(container: Any, credentials: list[dict[str, Any]], repositories: set[str]) -> None:
    validate(credentials, repositories)
    if not container.put_archive("/tmp", _archive(credentials, initial=True)):  # noqa: S108
        raise RuntimeError("Could not deliver Git credentials")


def replace(container: Any, credentials: list[dict[str, Any]]) -> None:
    if not container.put_archive(DIRECTORY, _archive(credentials, initial=False)):
        raise RuntimeError("Could not deliver renewed Git credentials")
    # A fixed privileged operation, never caller-controlled code or paths. The
    # helper sees either complete batch, including in already-running subprocesses.
    script = f"require('node:fs').renameSync('{DIRECTORY}/pending.json', '{DIRECTORY}/current.json')"
    result = container.exec_run(["node", "-e", script], user="0:0")
    if result.exit_code != 0:
        raise RuntimeError("Could not activate renewed Git credentials")
