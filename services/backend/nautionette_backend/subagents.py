"""Turn-scoped delegation. SQL owns ancestry; callers never supply it.

Admission is fail-fast, not queued: a parent waiting on children still occupies
its slot. Queueing nested calls at capacity would deadlock. Width counts all
accepted calls (including continuations), not just currently running siblings.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException

from . import agent_profiles, chat_outputs, provenance
from .db import db
from .runtime import runtime

# The backend has one event loop/one ASGI worker. This serializes the asynchronous
# model-discovery/acceptance boundary; no model execution happens under the lock.
_admission = asyncio.Lock()
LIMIT_KEYS = ("subagent_max_depth", "subagent_max_width", "subagent_max_parallel")


def caller(grant: str, tool: str | None = None) -> tuple[str, str, dict[str, Any]]:
    chat_id, turn_id = chat_outputs.grant_scope(grant)
    turn = db.one(
        "SELECT job FROM chat_turns WHERE id=? AND chat_id=? AND state='running' AND stop_requested=0",
        (turn_id, chat_id),
    )
    if not turn or not turn["job"]:
        raise HTTPException(409, "This agent turn is no longer active")
    job = json.loads(turn["job"])
    if tool and job.get("tools") is not None and tool not in job["tools"]:
        raise HTTPException(403, "This delegation tool is disabled in the turn's tool selection")
    if ancestor_stopped(turn_id):
        raise HTTPException(409, "An ancestor was stopped or failed")
    return chat_id, turn_id, job


def available(job: dict[str, Any]) -> list[dict[str, Any]]:
    if not job.get("agent_id"):
        return []
    row = db.one("SELECT allowed_subagents FROM agent_profiles WHERE id=?", (job["agent_id"],))
    allowed = json.loads(row["allowed_subagents"]) if row else []
    return [
        {"id": agent["id"], "name": agent["name"], "description": agent["description"]}
        for agent in agent_profiles.list_agents()
        if agent["id"] in allowed
    ]


def status(call: dict[str, Any]) -> dict[str, Any]:
    result = {key: call[key] for key in ("id", "child_chat_id", "child_turn_id", "target_agent_id", "depth")}
    result["chat_id"] = result.pop("child_chat_id")
    result["turn_id"] = result.pop("child_turn_id")
    turn = db.one(
        "SELECT state, stop_requested, chat_id FROM chat_turns WHERE id=?", (call["child_turn_id"],)
    )
    if call["error"]:
        return {**result, "state": "failed", "error": call["error"]}
    if not turn:
        return {
            **result,
            "state": "unavailable",
            "error": "Child turn was removed or admission was interrupted; not replayed",
        }
    result["state"] = turn["state"]
    if turn["state"] == "completed":
        answers = db.query(
            "SELECT content, meta FROM messages WHERE chat_id=? AND json_extract(meta, '$.turn_id')=? "
            "AND role='assistant' ORDER BY created_at",
            (turn["chat_id"], call["child_turn_id"]),
        )
        result["text"] = "\n\n".join(answer["content"] for answer in answers)[-32000:]
        errors = [json.loads(answer["meta"]).get("error") for answer in answers]
        result["error"] = (
            next((error for error in errors if error), None) if answers else "Child answer is unavailable"
        )
        result["ok"] = not result["error"] and not turn["stop_requested"]
    return result


def owned_call(parent_turn: str, call_id: str) -> dict[str, Any]:
    call = db.one("SELECT * FROM agent_calls WHERE id=? AND parent_turn_id=?", (call_id, parent_turn))
    if not call:
        raise HTTPException(404, "Call not found in this turn's children")
    return call


async def wait(grant: str, call_id: str, timeout_seconds: int) -> dict[str, Any]:
    _, parent, _ = caller(grant, "wait_for_subagent")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        caller(grant)  # revocation/stop also interrupts a long poll
        result = status(owned_call(parent, call_id))
        if result["state"] not in {"running", "queued"} or asyncio.get_running_loop().time() >= deadline:
            return result
        await asyncio.sleep(0.2)


async def start(grant: str, request_id: str, agent_id: str, text: str, chat_id: str | None) -> dict[str, Any]:
    from .routers.chats import new_chat, submit_message

    request_hash = hashlib.sha256(
        json.dumps({"agent_id": agent_id, "text": text, "chat_id": chat_id}, sort_keys=True).encode()
    ).hexdigest()
    async with _admission:
        parent_chat, parent_turn, job = caller(grant, "call_subagent")
        existing = db.one(
            "SELECT * FROM agent_calls WHERE parent_turn_id=? AND request_id=?", (parent_turn, request_id)
        )
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "request_id was used with different arguments")
            return status(existing)
        if agent_id not in {agent["id"] for agent in available(job)}:
            raise HTTPException(403, "This agent is not in the caller's allowed_subagents")
        parent = db.one("SELECT * FROM agent_calls WHERE child_turn_id=?", (parent_turn,))
        root = parent["root_turn_id"] if parent else parent_turn
        policy = db.one("SELECT limits FROM agent_calls WHERE root_turn_id=? LIMIT 1", (root,))
        limits = json.loads(policy["limits"]) if policy else {key: runtime(key) for key in LIMIT_KEYS}
        effective = {key: min(limits[key], runtime(key)) for key in LIMIT_KEYS}
        depth = parent["depth"] + 1 if parent else 1
        if depth > effective["subagent_max_depth"]:
            raise HTTPException(409, "Subagent maximum depth reached")
        width = db.one("SELECT COUNT(*) AS n FROM agent_calls WHERE parent_turn_id=?", (parent_turn,))["n"]
        if width >= effective["subagent_max_width"]:
            raise HTTPException(409, "Subagent maximum width reached (all calls in this turn count)")
        active = db.one(
            "SELECT COUNT(*) AS n FROM agent_calls c JOIN chat_turns t ON t.id=c.child_turn_id "
            "WHERE c.root_turn_id=? AND t.state IN ('running','queued')",
            (root,),
        )["n"]
        if active >= effective["subagent_max_parallel"]:
            raise HTTPException(
                409,
                "Subagent parallel limit reached. Wait for an existing child; "
                "nested parents retain their slots.",
            )
        if chat_id:
            # Never enqueue into an ancestor or unrelated human conversation. A
            # continuation belongs to the same initiating chat and target agent.
            previous = db.one(
                "SELECT id FROM agent_calls WHERE parent_chat_id=? AND child_chat_id=? AND target_agent_id=?",
                (parent_chat, chat_id, agent_id),
            )
            chat = db.get_chat(chat_id)
            if not previous or not chat or chat.get("agent_id") != agent_id or chat.get("agent_customized"):
                raise HTTPException(
                    403, "Only this caller chat's unmodified delegated conversations can be continued"
                )
            if chat.get("queue_paused") or db.one(
                "SELECT id FROM chat_turns WHERE chat_id=? AND state IN ('running','queued')", (chat_id,)
            ):
                raise HTTPException(409, "Target chat is busy or paused; wait or use a new chat")
        call_id, child_turn = uuid.uuid4().hex, uuid.uuid4().hex
        # Reserve durably BEFORE any await/mutation. An interrupted admission is
        # reported as uncertain, never silently resubmitted with the same ID.
        db.execute(
            "INSERT INTO agent_calls (id,request_id,parent_turn_id,root_turn_id,parent_chat_id,child_chat_id,"
            "child_turn_id,target_agent_id,depth,limits,request_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                call_id,
                request_id,
                parent_turn,
                root,
                parent_chat,
                chat_id,
                child_turn,
                agent_id,
                depth,
                json.dumps(limits),
                request_hash,
            ),
        )
        try:
            if not chat_id:
                # Leave the title provisional: normal message admission generates
                # a task title from the delegated prompt, just like a human chat.
                chat = await new_chat({"agent_id": agent_id})
                chat_id = chat["id"]
                db.execute("UPDATE chats SET delegated_parent_chat_id=? WHERE id=?", (parent_chat, chat_id))
                db.execute("UPDATE agent_calls SET child_chat_id=? WHERE id=?", (chat_id, call_id))

            def before_accept() -> None:
                caller(grant, "call_subagent")
                # Discovery yielded; recheck revocations/settings before mutation.
                if agent_id not in {agent["id"] for agent in available(job)}:
                    raise HTTPException(403, "Subagent access was revoked")
                if depth > min(limits["subagent_max_depth"], runtime("subagent_max_depth")):
                    raise HTTPException(409, "Subagent maximum depth reached")
                if width + 1 > min(limits["subagent_max_width"], runtime("subagent_max_width")):
                    raise HTTPException(409, "Subagent maximum width reached")
                if active >= min(limits["subagent_max_parallel"], runtime("subagent_max_parallel")):
                    raise HTTPException(409, "Subagent parallel limit reached")
                current = db.get_chat(chat_id)
                if not current or current.get("agent_id") != agent_id or current.get("agent_customized"):
                    raise HTTPException(409, "Target chat configuration changed during admission")

            sender = {
                "id": job["agent_id"],
                "name": job.get("agent_name") or "Agent",
                "chat_id": parent_chat,
                "call_id": call_id,
            }
            await submit_message(
                chat_id,
                {"text": text, "message_id": child_turn},
                sender=sender,
                origin=provenance.agent_call(sender, job.get("origin")),
                delegation=call_id,
                before_accept=before_accept,
            )
        except Exception as exc:
            # A turn already accepted by submit_message is not replayed on retry.
            if not db.one("SELECT id FROM chat_turns WHERE id=?", (child_turn,)):
                db.execute(
                    "UPDATE agent_calls SET error=? WHERE id=?",
                    (
                        str(exc.detail) if isinstance(exc, HTTPException) else "Child admission failed",
                        call_id,
                    ),
                )
            raise
        return status(owned_call(parent_turn, call_id))


def ancestor_stopped(turn_id: str) -> bool:
    """Stop/failure propagates down; normal async parent completion does not."""
    seen = set()
    while turn_id not in seen:
        seen.add(turn_id)
        call = db.one("SELECT parent_turn_id FROM agent_calls WHERE child_turn_id=?", (turn_id,))
        if not call:
            return False
        turn_id = call["parent_turn_id"]
        turn = db.one("SELECT state, stop_requested, chat_id FROM chat_turns WHERE id=?", (turn_id,))
        if not turn or turn["stop_requested"]:
            return True
        if turn["state"] == "completed" and db.one(
            "SELECT id FROM messages WHERE chat_id=? AND json_extract(meta, '$.turn_id')=? "
            "AND json_extract(meta, '$.error') IS NOT NULL",
            (turn["chat_id"], turn_id),
        ):
            return True
    return True
