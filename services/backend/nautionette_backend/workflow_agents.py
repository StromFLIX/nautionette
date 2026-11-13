"""Saved-profile workflow tasks use normal durable chats, not generic activity agents.

A stable activity key reconnects to the same turn after worker/backend restarts.
Credentials, worktrees, profile snapshots and deployment draining therefore use
exactly the interactive chat pipeline. Never replay a mutating agent on retry.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from fastapi import HTTPException

from . import agent_profiles
from .db import db
from .routers.chats import new_chat, stop_chat, submit_message


def task_ids(key: str) -> tuple[str, str]:
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        raise HTTPException(422, "task key must be a SHA-256 activity identity")
    digest = hashlib.sha256(f"workflow-agent:{key}".encode()).hexdigest()
    return digest[:12], f"workflow-agent-{digest}"


async def start(key: str, agent_id: str, prompt: str, *, validate_only: bool = False) -> dict:
    chat_id, turn_id = task_ids(key)
    if not isinstance(agent_id, str) or not agent_id:
        raise HTTPException(422, "agent_id is required")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(422, "prompt is required")
    if validate_only:
        selected = agent_profiles.for_chat(agent_id)
        agent_profiles.normalize({key: selected[key] for key in agent_profiles.CONFIG_KEYS})
        return {
            "ok": True,
            "validated": True,
            "agent_id": selected["agent_id"],
            "agent_name": selected["agent_name"],
            "model": selected["model"],
            "project_ids": selected["project_ids"],
        }
    prompt_hash = hashlib.sha256(prompt.strip().encode()).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO workflow_agent_tasks (key, chat_id, agent_id, prompt_hash) VALUES (?,?,?,?)",
        (key, chat_id, agent_id, prompt_hash),
    )
    task = db.one("SELECT * FROM workflow_agent_tasks WHERE key = ?", (key,))
    if task["agent_id"] != agent_id or task["prompt_hash"] != prompt_hash:
        raise HTTPException(409, "Activity key already belongs to a different agent request")
    chat = db.get_chat(chat_id)
    if not chat:
        if task["admitted"]:
            raise HTTPException(410, "Task chat was deleted; refusing to replay an agent's side effects")
        try:
            chat = await new_chat(
                {"agent_id": agent_id, "title": "Workflow agent task", "timeout_exempt": True},
                chat_id=chat_id,
            )
        except sqlite3.IntegrityError:
            # Concurrent retries may race while resolving model capabilities.
            chat = db.get_chat(chat_id)
            if not chat:
                raise
    db.execute("UPDATE workflow_agent_tasks SET admitted = 1 WHERE key = ?", (key,))
    existing = db.one("SELECT content FROM messages WHERE id = ? AND chat_id = ?", (turn_id, chat_id))
    if chat["agent_id"] != agent_id or (existing and existing["content"] != prompt.strip()):
        raise HTTPException(409, "Activity key already belongs to a different agent request")
    turn = db.one("SELECT state, job FROM chat_turns WHERE id = ? AND chat_id = ?", (turn_id, chat_id))
    if turn and (turn["job"] or turn["state"] not in {"running", "queued"}):
        return status(key)
    await submit_message(chat_id, {"text": prompt, "message_id": turn_id})
    return status(key)


async def stop(key: str) -> dict:
    result = status(key)
    if "ok" not in result:
        await stop_chat(result["chat_id"], {"turn_id": result["turn_id"]})
    return {"ok": True}


def status(key: str) -> dict:
    chat_id, turn_id = task_ids(key)
    task = db.one("SELECT admitted FROM workflow_agent_tasks WHERE key = ?", (key,))
    if task and task["admitted"] and not db.get_chat(chat_id):
        raise HTTPException(410, "Task chat was deleted; refusing to replay an agent's side effects")
    turn = db.one("SELECT state FROM chat_turns WHERE id = ? AND chat_id = ?", (turn_id, chat_id))
    if not turn:
        raise HTTPException(404, "Workflow agent task not found")
    result = {"chat_id": chat_id, "turn_id": turn_id, "state": turn["state"]}
    if turn["state"] in {"running", "queued"}:
        return result
    # Read only the final done event, not an unbounded transcript of tool calls.
    row = db.one(
        "SELECT payload FROM chat_turn_events WHERE turn_id = ? "
        "AND json_extract(payload, '$.type') = 'done' ORDER BY seq DESC LIMIT 1",
        (turn_id,),
    )
    if not row:
        return {**result, "ok": False, "error": "Agent task ended without a final result"}
    message = json.loads(row["payload"]).get("message") or {}
    meta = message.get("meta") or {}
    interrupted = meta.get("interrupted") or meta.get("stopped")
    error = meta.get("error") or ("Agent task was stopped" if interrupted else None)
    return {**result, "ok": not bool(error), "text": message.get("content", ""), "error": error}
