"""Chats, the stream a chat answers with, and promoting one to a workflow."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import projects
from ..agent import (
    agent_job,
    build_history,
    promote_chat,
    summarise_for_title,
)
from ..background import spawn
from ..clients import broker
from ..conversations import chat_snapshots, run_turn, turn_events
from ..db import db
from ..events import bus
from ..runtime import history_budget, runtime
from ..security import require_user
from .system import SSE_HEADERS

router = APIRouter(dependencies=[Depends(require_user)])


def _chat_or_404(chat_id: str) -> dict[str, Any]:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


@router.get("/api/chats")
async def list_chats() -> dict[str, Any]:
    return {"chats": db.list_chats()}


@router.post("/api/chats")
async def create_chat(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    project_ids = projects.selection(payload.get("project_ids", []))
    chat = db.create_chat(
        title=(payload.get("title") or "New chat").strip()[:120],
        agent_set=payload.get("agent_set") or runtime("default_agent_set"),
        model=payload.get("model") or runtime("default_model"),
        tools=payload.get("tools"),
    )
    if project_ids:
        chat = db.update_chat(chat["id"], {"project_ids": project_ids}) or chat
    bus.publish("chat.created", {"chat_id": chat["id"], "title": chat["title"]})
    return chat


@router.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _chat_or_404(chat_id)
    fields: dict[str, Any] = {}
    if "title" in payload:
        fields["title"] = (payload.get("title") or "Untitled").strip()[:120]
    if "agent_set" in payload:
        fields["agent_set"] = payload.get("agent_set") or runtime("default_agent_set")
    if "model" in payload:
        fields["model"] = payload.get("model") or None
    if "tools" in payload:
        selected = payload.get("tools")
        fields["tools"] = [str(name) for name in selected] if isinstance(selected, list) else None
    if "project_ids" in payload:
        fields["project_ids"] = projects.selection(payload["project_ids"])
    chat = db.update_chat(chat_id, fields)
    bus.publish("chat.updated", {"chat_id": chat_id})
    return chat  # type: ignore[return-value]


@router.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str) -> dict[str, Any]:
    _chat_or_404(chat_id)
    return db.chat_snapshot(chat_id)


@router.get("/api/chats/{chat_id}/stream")
async def subscribe_chat(chat_id: str) -> StreamingResponse:
    _chat_or_404(chat_id)
    return StreamingResponse(chat_snapshots(chat_id), media_type="text/event-stream", headers=SSE_HEADERS)


@router.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str) -> dict[str, Any]:
    db.delete_chat(chat_id)
    bus.publish("chat.deleted", {"chat_id": chat_id})
    return {"ok": True}


@router.post("/api/chats/{chat_id}/messages", response_model=None)
async def send_message(chat_id: str, request: Request, payload: dict[str, Any] = Body(...)):
    chat = _chat_or_404(chat_id)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    history = build_history(db.list_messages(chat_id), max_chars=history_budget(chat.get("model")))
    message_id = payload.get("message_id") or uuid.uuid4().hex
    if not isinstance(message_id, str) or len(message_id) > 128:
        raise HTTPException(status_code=400, detail="message_id must be a string of at most 128 characters")
    existing = db.one("SELECT meta FROM messages WHERE id = ?", (message_id,))
    default_selection = json.loads(existing["meta"]).get("project_ids", []) if existing else chat["project_ids"]
    project_ids = payload.get("project_ids", default_selection)
    if not existing:
        project_ids = projects.selection(project_ids)
    try:
        user_message, created = db.accept_chat_message(chat_id, text, message_id, project_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="This chat is still answering; retry shortly.") from exc
    if created and chat["title"] in {"New chat", ""} and not history:
        db.execute("UPDATE chats SET title = ? WHERE id = ?", (summarise_for_title(text), chat_id))

    job = agent_job(
        prompt=text,
        mode="interactive",
        history=history,
        agent_set=chat["agent_set"],
        model=chat.get("model"),
        tools=chat.get("tools"),
        run_id=f"chat-{chat_id}",
    )
    job.update(
        chat_id=chat_id,
        turn_id=message_id,
        internet_allowed=chat["internet_status"] == "allowed",
        internet_status=chat["internet_status"],
        project_ids=project_ids,
    )
    if created and project_ids:
        selected_projects = [db.one("SELECT * FROM projects WHERE id = ?", (project_id,)) for project_id in project_ids]
        job["system_prompt"] += (
            "\nSelected writable per-chat Git worktrees (all other projects are unavailable):\n"
            + "\n".join(f"- {project['full_name']}: /projects/{project['id']}" for project in selected_projects if project)
            + "\nThese persistent worktrees belong to this chat and start with detached HEAD, without creating branches. "
            "Other chats have separate working files and HEADs. Preserve uncommitted work and local commits; "
            "never reset or overwrite them by default. Stay detached unless the user asks for a branch. "
            "Worktrees start from the local clone. Request internet access before contacting GitHub, "
            "including fetch/pull/push. After approval, use Git directly with the configured HTTPS origin. "
            "The credential helper supplies repository-scoped GitHub App tokens for this turn; "
            "they expire within one hour and refresh on the next message. "
            "Commit and push when the user's task calls for it. From detached HEAD use "
            "git push origin HEAD:refs/heads/<target-branch>, choosing the target from the user's request. "
            "Ask if the target is unclear. Never force-push to resolve concurrent changes; "
            "fetch and reconcile them. Respect branch protection and report push failures. "
            "Do not print or persist credentials."
        )
    job["system_prompt"] += (
        "\nDirect internet access is " + chat["internet_status"] + " for this chat. "
        "Before first accessing the internet, call request_internet_access with a reason and wait "
        "for approval. Once allowed, use the internet freely for this chat without asking again. "
        "If denied, continue offline; never bypass the decision via MCP tools or workflows. "
        "This required internet approval is an exception to routine permission-free operation."
    )

    if created:
        spawn(run_turn(message_id, chat_id, job), name=f"chat-{message_id}")
        bus.publish("chat.message", {"chat_id": chat_id})
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"message": user_message, "turn_id": message_id}, status_code=202)
    return StreamingResponse(turn_events(message_id), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/chats/{chat_id}/promote")
async def promote(chat_id: str) -> dict[str, Any]:
    chat = _chat_or_404(chat_id)
    messages = db.list_messages(chat_id)
    if not messages:
        raise HTTPException(status_code=400, detail="nothing to promote yet")
    bus.publish("promote.start", {"chat_id": chat_id})
    published = await promote_chat(chat, messages)
    db.execute("UPDATE chats SET promoted_to = ? WHERE id = ?", (published["name"], chat_id))
    bus.publish("promote.completed", {"chat_id": chat_id, "workflow": published["name"]})
    return published


@router.post("/api/chats/{chat_id}/internet")
async def decide_chat_internet(chat_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    chat = _chat_or_404(chat_id)
    allowed, turn_id = payload.get("allowed"), payload.get("turn_id")
    if type(allowed) is not bool or not isinstance(turn_id, str) or not turn_id:
        raise HTTPException(status_code=422, detail="allowed must be a boolean and turn_id is required")
    decision = "allowed" if allowed else "denied"
    if chat["internet_turn_id"] == turn_id and chat["internet_status"] == decision:
        return chat
    claimed = db.execute(
        "UPDATE chats SET internet_status = 'deciding' WHERE id = ? "
        "AND internet_status = 'pending' AND internet_turn_id = ? "
        "AND EXISTS (SELECT 1 FROM chat_turns WHERE id = ? AND chat_id = ? AND state = 'running')",
        (chat_id, turn_id, turn_id, chat_id),
    ).rowcount
    if not claimed:
        raise HTTPException(status_code=409, detail="This internet request is no longer pending")
    try:
        await broker.decide_internet(chat_id, turn_id, allowed)
    except Exception as exc:
        db.execute(
            "UPDATE chats SET internet_status = CASE WHEN EXISTS "
            "(SELECT 1 FROM chat_turns WHERE id = ? AND state = 'running') "
            "THEN 'pending' ELSE 'blocked' END WHERE id = ? "
            "AND internet_turn_id = ? AND internet_status = 'deciding'", (turn_id, chat_id, turn_id),
        )
        raise HTTPException(status_code=502, detail="Could not deliver the decision; retry shortly") from exc
    db.execute(
        "UPDATE chats SET internet_status = ? WHERE id = ? AND internet_turn_id = ?",
        (decision, chat_id, turn_id),
    )
    bus.publish("chat.updated", {"chat_id": chat_id})
    return _chat_or_404(chat_id)
