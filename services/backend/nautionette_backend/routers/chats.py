"""Chats, the stream a chat answers with, and promoting one to a workflow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..agent import (
    Timeline,
    agent_job,
    build_history,
    promote_chat,
    stream_agent,
    summarise_for_title,
)
from ..db import db
from ..events import bus, sse
from ..runtime import history_budget, remember_agent_result, runtime
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
    chat = db.create_chat(
        title=(payload.get("title") or "New chat").strip()[:120],
        agent_set=payload.get("agent_set") or runtime("default_agent_set"),
        model=payload.get("model") or runtime("default_model"),
        tools=payload.get("tools"),
    )
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
    return db.update_chat(chat_id, fields)  # type: ignore[return-value]


@router.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str) -> dict[str, Any]:
    return {"chat": _chat_or_404(chat_id), "messages": db.list_messages(chat_id)}


@router.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str) -> dict[str, Any]:
    db.delete_chat(chat_id)
    bus.publish("chat.deleted", {"chat_id": chat_id})
    return {"ok": True}


@router.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: str, payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    chat = _chat_or_404(chat_id)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    history = build_history(db.list_messages(chat_id), max_chars=history_budget(chat.get("model")))
    user_message = db.add_message(chat_id, "user", text)
    if chat["title"] in {"New chat", ""} and not history:
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

    async def generator():
        yield sse({"type": "user_message", "message": user_message})
        timeline = Timeline()
        failure: str | None = None
        try:
            async for event in stream_agent(job):
                kind = event.get("type")
                if kind == "delta":
                    timeline.add_text(event.get("text", ""))
                elif kind == "tool":
                    timeline.start_tool(event)
                elif kind == "tool_done":
                    timeline.finish_tool(event)
                elif kind == "error":
                    failure = event.get("message")
                elif kind == "result":
                    remember_agent_result(bool(event.get("ok")))
                    if not timeline.text and event.get("text"):
                        timeline.add_text(event["text"])
                yield sse(event)
        except Exception as exc:  # noqa: BLE001 - always close the stream cleanly
            failure = str(exc)
            yield sse({"type": "error", "message": failure})

        content = timeline.text
        if not content and failure:
            content = f"The agent could not answer: {failure}"
        assistant = db.add_message(
            chat_id,
            "assistant",
            content or "(no answer)",
            {"tools": timeline.tools, "steps": timeline.steps, "error": failure},
        )
        bus.publish("chat.answered", {"chat_id": chat_id, "ok": failure is None})
        yield sse({"type": "done", "message": assistant})

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/chats/{chat_id}/promote")
async def promote(chat_id: str) -> dict[str, Any]:
    chat = _chat_or_404(chat_id)
    messages = db.list_messages(chat_id)
    if not messages:
        raise HTTPException(status_code=400, detail="nothing to promote yet")
    bus.publish("promote.start", {"chat_id": chat_id})
    draft = await promote_chat(chat, messages)
    db.execute("UPDATE chats SET promoted_to = ? WHERE id = ?", (draft["name"], chat_id))
    bus.publish("promote.draft", {"chat_id": chat_id, "workflow": draft["name"]})
    return draft
