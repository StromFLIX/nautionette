"""Legacy import and authenticated, bounded inspection of native Pi state.

Normal turns do not reconstruct history. Pi's JSONL is the conversation source;
SQL messages/steps remain a UI projection, not a second compaction engine.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any

from nautionette.chat_sessions import SESSION_FILE, chat_directory, session_exists, session_lock

from .db import db


def legacy_history(chat_id: str, turn_id: str) -> list[dict[str, Any]]:
    chat = db.get_chat(chat_id)
    expected = (chat or {}).get("pi_session_id")
    if session_exists(chat_id):
        if expected:
            with (chat_directory(chat_id) / SESSION_FILE).open() as handle:
                header = json.loads(handle.readline(65536))
            if header.get("id") != expected:
                raise ValueError("Native Pi session identity mismatch; refusing to replace history")
        return []
    if expected:
        raise ValueError(
            "This chat's native Pi session is missing. Restore the chat-sessions backup; "
            "UI text is not a replacement for native history."
        )
    messages = [m for m in db.list_messages(chat_id) if m["id"] != turn_id and not m["meta"].get("queued")]
    all_attachments = [a for m in messages for a in m["meta"].get("attachments", [])]
    attachment_ids = {a["id"] for a in all_attachments}
    hydrated_ids = {a["id"] for a in all_attachments[-4:]}
    # Originals migrate directly to the shared volume, not through an unbounded
    # base64 job. Every legacy reference remains usable, including old images.
    directory = chat_directory(chat_id)
    files = directory / "attachments"
    if attachment_ids:
        files.mkdir(parents=True, exist_ok=True, mode=0o700)
        if files.is_symlink():
            raise ValueError("Invalid chat attachment directory")
    out = []
    for message in messages:
        if message["role"] not in {"user", "assistant"}:
            continue
        content = message["content"] or ""
        references = []
        for attachment in message["meta"].get("attachments", []):
            row = db.one(
                "SELECT * FROM chat_images WHERE id = ? AND chat_id = ?", (attachment["id"], chat_id)
            )
            if not row:
                raise ValueError("A legacy chat attachment is missing; import was not completed")
            extension = re.search(r"\.[a-zA-Z0-9]{1,16}$", row["name"])
            name = row["id"] + (extension[0] if extension else "")
            target = files / name
            if target.is_symlink():
                raise ValueError("Invalid durable attachment path")
            if target.exists():
                if target.read_bytes() != row["data"]:
                    raise ValueError("Durable original attachment differs; import stopped")
            else:
                temp = files / (name + ".tmp")
                with temp.open("wb") as handle:
                    os.chmod(temp, 0o600)
                    handle.write(row["data"])
                    handle.flush()
                    os.fsync(handle.fileno())
                temp.replace(target)
            references.append({"name": row["name"], "path": f"/chat-session/attachments/{name}"})
        if references:
            content += "\nOriginal attachments (use tools to inspect):\n" + json.dumps(references)
        entry: dict[str, Any] = {"role": message["role"], "content": content}
        attachments = [a for a in message["meta"].get("attachments", []) if a["id"] in hydrated_ids]
        if attachments:
            entry["attachments"] = attachments
        steps = [s for s in message["meta"].get("steps", []) if s.get("kind") != "text"]
        if steps:
            entry["steps"] = steps
        out.append(entry)
    return out


def delete(chat_id: str) -> None:
    with session_lock(chat_id):
        directory = chat_directory(chat_id)
        if directory.exists():
            shutil.rmtree(directory)
    # Do NOT unlink the broker's lock inode: waiters must continue using the same
    # inode. Empty lock files hold no conversation data.


def entries(chat_id: str, after: int = 0, limit: int = 20) -> dict[str, Any]:
    path = chat_directory(chat_id) / SESSION_FILE
    if path.is_symlink():
        raise ValueError("Invalid native session path")
    if not path.exists():
        return {"format": "pi-jsonl", "entries": [], "next": None, "legacy": True}
    out = []
    size = 0
    cursor = after
    with path.open("rb") as handle:
        if after:
            handle.seek(after - 1)
            if handle.read(1) != b"\n":
                raise ValueError("Cursor must be at a native entry boundary")
        else:
            handle.seek(0)
        while len(out) < limit:
            # Bound a single line as well as total response bytes. Images and
            # large tool results stay in the native file, not unbounded HTTP.
            line = handle.readline(256_001)
            if not line:
                break
            if len(line) > 256_000:
                if size + 8000 > 480_000:
                    break
                preview = line[:8000].decode("utf-8", "replace")
                while line and not line.endswith(b"\n"):
                    line = handle.readline(256_001)
                if not line:
                    break  # Writer has not finalized this record yet.
                out.append({"type": "large_entry", "offset": cursor, "preview": preview, "truncated": True})
                size += len(preview.encode("utf-8"))
                cursor = handle.tell()
                continue
            if not line.endswith(b"\n") or size + len(line) > 480_000:
                break
            out.append(json.loads(line))
            size += len(line)
            cursor = handle.tell()
    return {
        "format": "pi-jsonl",
        "entries": out,
        "next": cursor if cursor < path.stat().st_size else None,
        "legacy": False,
    }
