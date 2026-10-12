"""Bounded, read-only chat diagnostics. Never return persisted jobs or credentials."""

# SQL fragments below are fixed local clauses; all external values use bound parameters.
# ruff: noqa: S608

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import db
from ..security import require_user

router = APIRouter(prefix="/api/chat-inspection", dependencies=[Depends(require_user)])


def _chat(chat_id: str) -> dict[str, Any]:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "chat not found")
    # Deliberate projection: no package configuration, secrets, or job snapshots.
    return {
        key: chat.get(key)
        for key in (
            "id",
            "title",
            "model",
            "agent_set",
            "agent_id",
            "agent_name",
            "created_at",
            "updated_at",
            "last_activity_at",
            "project_ids",
            "timeout_exempt",
            "queue_paused",
        )
    }


def _page(rows: list[dict], offset: int, limit: int, key: str) -> dict:
    return {key: rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}


def _tool_stats(where: str, params: list[Any]) -> dict:
    rows = db.query(
        "SELECT json_extract(e.payload, '$.name') AS name, COUNT(*) AS calls "
        "FROM chat_turn_events e JOIN chat_turns t ON t.id = e.turn_id "
        f"WHERE {where} AND json_extract(e.payload, '$.type') = 'tool' "
        "GROUP BY name ORDER BY calls DESC, name",
        tuple(params),
    )
    errors = db.one(
        "SELECT COUNT(*) AS count FROM chat_turn_events e JOIN chat_turns t ON t.id = e.turn_id "
        f"WHERE {where} AND json_extract(e.payload, '$.type') = 'tool_done' "
        "AND json_extract(e.payload, '$.error') = 1",
        tuple(params),
    )
    return {
        "tool_call_count": sum(row["calls"] for row in rows),
        "tools_used": rows[:20],
        "distinct_tool_count": len(rows),
        "tools_used_truncated": len(rows) > 20,
        "tool_error_count": errors["count"],
    }


_ERROR = (
    "(json_extract(e.payload, '$.type') = 'error' OR "
    "(json_extract(e.payload, '$.type') = 'result' AND json_extract(e.payload, '$.ok') = 0) OR "
    "(json_extract(e.payload, '$.type') = 'done' AND "
    "COALESCE(json_extract(e.payload, '$.message.meta.error'), '') != ''))"
)


@router.get("/chats")
def search_chats(
    query: str | None = Query(None, max_length=200),
    model: str | None = None,
    updated_after: float | None = Query(None, ge=0, description="Unix timestamp, seconds"),
    tool: str | None = None,
    has_error: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    """Find every chat with paging. Search ID/title/message text, model, tool used, or failures.

    Returns compact summaries and tool counts, not transcripts. Use chat IDs with
    inspect_chat, list_chat_sessions and inspect_chat_messages to drill down.
    """
    clauses, params = [], []
    if query:
        clauses.append(
            "(instr(c.id, ?) > 0 OR instr(lower(c.title), lower(?)) > 0 OR EXISTS "
            "(SELECT 1 FROM messages m WHERE m.chat_id = c.id AND instr(lower(m.content), lower(?)) > 0))"
        )
        params.extend([query] * 3)
    if model:
        clauses.append("c.model = ?")
        params.append(model)
    if updated_after is not None:
        clauses.append("c.updated_at >= ?")
        params.append(updated_after)
    if tool:
        clauses.append(
            "EXISTS (SELECT 1 FROM chat_turns t JOIN chat_turn_events e ON e.turn_id = t.id "
            "WHERE t.chat_id = c.id AND json_extract(e.payload, '$.type') = 'tool' "
            "AND json_extract(e.payload, '$.name') = ?)"
        )
        params.append(tool)
    if has_error is not None:
        clauses.append(
            ("" if has_error else "NOT ") + "EXISTS (SELECT 1 FROM chat_turns t "
            f"JOIN chat_turn_events e ON e.turn_id = t.id WHERE t.chat_id = c.id AND {_ERROR})"
        )
    where = " AND ".join(clauses) or "1"
    rows = db.query(
        f"SELECT c.id FROM chats c WHERE {where} ORDER BY c.updated_at DESC, c.id LIMIT ? OFFSET ?",
        (*params, limit + 1, offset),
    )
    page = _page(rows, offset, limit, "chats")
    page["chats"] = [inspect_chat(row["id"]) for row in page["chats"]]
    return page


@router.get("/chats/{chat_id}")
def inspect_chat(chat_id: str) -> dict:
    """Inspect one chat's configuration, session/message counts and tools used without its transcript."""
    chat = _chat(chat_id)
    counts = db.one("SELECT COUNT(*) AS message_count FROM messages WHERE chat_id = ?", (chat_id,))
    states = db.query(
        "SELECT state, COUNT(*) AS count FROM chat_turns WHERE chat_id = ? GROUP BY state", (chat_id,)
    )
    failures = db.one(
        "SELECT COUNT(DISTINCT t.id) AS failed_session_count FROM chat_turns t "
        f"JOIN chat_turn_events e ON e.turn_id = t.id WHERE t.chat_id = ? AND {_ERROR}",
        (chat_id,),
    )
    return {
        **chat,
        **counts,
        **failures,
        "session_count": sum(row["count"] for row in states),
        "session_states": states,
        **_tool_stats("t.chat_id = ?", [chat_id]),
    }


@router.get("/sessions")
def list_chat_sessions(
    chat_id: str | None = None,
    state: Literal["running", "queued", "steered", "completed"] | None = None,
    model: str | None = None,
    created_after: float | None = Query(None, ge=0, description="Unix timestamp, seconds"),
    created_before: float | None = Query(None, ge=0, description="Unix timestamp, seconds"),
    has_error: bool | None = None,
    tool: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    """List all durable chat sessions (turns), globally or by chat ID, state, error or tool.

    Includes session IDs usable with inspect_chat_session_events, timestamps,
    tool-call counts and tools used. Never includes prompts or private job data.
    """
    clauses, params = [], []
    if chat_id:
        _chat(chat_id)
        clauses.append("t.chat_id = ?")
        params.append(chat_id)
    if state:
        clauses.append("t.state = ?")
        params.append(state)
    if model:
        clauses.append("json_extract(t.job, '$.model') = ?")
        params.append(model)
    if created_after is not None:
        clauses.append("m.created_at >= ?")
        params.append(created_after)
    if created_before is not None:
        clauses.append("m.created_at <= ?")
        params.append(created_before)
    if has_error is not None:
        clauses.append(
            ("" if has_error else "NOT ")
            + f"EXISTS (SELECT 1 FROM chat_turn_events e WHERE e.turn_id = t.id AND {_ERROR})"
        )
    if tool:
        clauses.append(
            "EXISTS (SELECT 1 FROM chat_turn_events e WHERE e.turn_id = t.id "
            "AND json_extract(e.payload, '$.type') = 'tool' AND json_extract(e.payload, '$.name') = ?)"
        )
        params.append(tool)
    where = " AND ".join(clauses) or "1"
    rows = db.query(
        "SELECT t.id, t.chat_id, t.state, t.stop_requested, m.created_at, c.title, "
        "json_extract(t.job, '$.model') AS model, json_extract(t.job, '$.agent_set') AS agent_set, "
        "json_extract(t.job, '$.reasoning_effort') AS reasoning_effort "
        "FROM chat_turns t JOIN messages m ON m.id = t.user_id JOIN chats c ON c.id = t.chat_id "
        f"WHERE {where} ORDER BY m.created_at DESC, t.id LIMIT ? OFFSET ?",
        (*params, limit + 1, offset),
    )
    page = _page(rows, offset, limit, "sessions")
    for row in page["sessions"]:
        row.update(_tool_stats("t.id = ?", [row["id"]]))
        failure = db.one(
            f"SELECT seq FROM chat_turn_events e WHERE e.turn_id = ? AND {_ERROR} ORDER BY seq DESC LIMIT 1",
            (row["id"],),
        )
        row["has_error"] = failure is not None
        row["last_error_seq"] = failure["seq"] if failure else None
    return page


@router.get("/tools")
def list_chat_tools(
    chat_id: str | None = None,
    session_id: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Page through tools used and their call counts, globally or for a chat/session.

    Chat/session summaries include only the top 20 tools; this tool enumerates the
    complete set without transcripts, arguments or results.
    """
    clauses, params = [], []
    if chat_id:
        _chat(chat_id)
        clauses.append("t.chat_id = ?")
        params.append(chat_id)
    if session_id:
        turn = db.one("SELECT chat_id FROM chat_turns WHERE id = ?", (session_id,))
        if not turn or (chat_id and turn["chat_id"] != chat_id):
            raise HTTPException(404, "chat session not found")
        clauses.append("t.id = ?")
        params.append(session_id)
    where = " AND ".join(clauses) or "1"
    rows = db.query(
        "SELECT json_extract(e.payload, '$.name') AS name, COUNT(*) AS calls "
        "FROM chat_turn_events e JOIN chat_turns t ON t.id = e.turn_id "
        f"WHERE {where} AND json_extract(e.payload, '$.type') = 'tool' "
        "GROUP BY name ORDER BY calls DESC, name LIMIT ? OFFSET ?",
        (*params, limit + 1, offset),
    )
    return _page(rows, offset, limit, "tools")


def _bounded(value: Any, max_chars: int, offset: int = 0) -> dict:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    excerpt = text[offset : offset + max_chars]
    return {
        "text": excerpt,
        "truncated": len(text) > len(excerpt),
        "total_chars": len(text),
        "offset": offset,
        "next_offset": offset + len(excerpt) if max_chars and offset + len(excerpt) < len(text) else None,
    }


@router.get("/chats/{chat_id}/messages")
def inspect_chat_messages(
    chat_id: str,
    role: Literal["user", "assistant", "system"] | None = None,
    query: str | None = Query(None, max_length=200),
    has_error: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    max_chars: int = Query(500, ge=0, le=4000),
    content_offset: int = Query(0, ge=0),
) -> dict:
    """Read filtered, paginated message excerpts. Raise max_chars to inspect a specific answer.

    Metadata is deliberately projected; tool args/results are available separately
    through session events. Images, attachments and private jobs are never returned.
    """
    _chat(chat_id)
    clauses, params = ["chat_id = ?"], [chat_id]
    if role:
        clauses.append("role = ?")
        params.append(role)
    if query:
        clauses.append("instr(lower(content), lower(?)) > 0")
        params.append(query)
    if has_error is not None:
        clauses.append("COALESCE(json_extract(meta, '$.error'), '') " + ("!= ''" if has_error else "= ''"))
    rows = db.query(
        "SELECT id, role, content, created_at, json_extract(meta, '$.error') AS error "
        f"FROM messages WHERE {' AND '.join(clauses)} ORDER BY rowid LIMIT ? OFFSET ?",
        (*params, min(limit, max(1, 20000 // max(1, max_chars))) + 1, offset),
    )
    effective_limit = min(limit, max(1, 20000 // max(1, max_chars)))
    page = _page(rows, offset, effective_limit, "messages")
    for row in page["messages"]:
        row["content"] = _bounded(row["content"], max_chars, content_offset)
        row["error"] = _bounded(row["error"], max_chars) if row["error"] else None
    return page


@router.get("/sessions/{session_id}/events")
def inspect_chat_session_events(
    session_id: str,
    after_seq: int = Query(0, ge=0),
    event_types: str = Query("tool,tool_done,error,result,recovery,done", max_length=200),
    tool: str | None = None,
    include_data: bool = False,
    data_offset: int = Query(0, ge=0),
    max_chars: int = Query(500, ge=0, le=4000),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Inspect a session's events by sequence cursor, event types and tool name.

    Default: compact tool/failure summaries. Set include_data=true for bounded JSON
    payload excerpts (arguments, results, provider errors); event_types='all' includes
    streaming events. next_after_seq composes with this tool's after_seq argument.
    """
    turn = db.one("SELECT id, chat_id, state FROM chat_turns WHERE id = ?", (session_id,))
    if not turn:
        raise HTTPException(404, "chat session not found")
    clauses, params = ["turn_id = ?", "seq > ?"], [session_id, after_seq]
    if event_types != "all":
        kinds = list(dict.fromkeys(kind.strip() for kind in event_types.split(",") if kind.strip()))
        if not kinds or len(kinds) > 20:
            raise HTTPException(422, "Provide 1–20 comma-separated event types, or 'all'")
        clauses.append(f"json_extract(payload, '$.type') IN ({','.join('?' for _ in kinds)})")
        params.extend(kinds)
    if tool:
        clauses.append("json_extract(payload, '$.name') = ?")
        params.append(tool)
    effective_limit = min(limit, max(1, 20000 // max(1, max_chars)))
    rows = db.query(
        f"SELECT seq, payload FROM chat_turn_events WHERE {' AND '.join(clauses)} ORDER BY seq LIMIT ?",
        (*params, effective_limit + 1),
    )
    events = []
    for row in rows[:effective_limit]:
        payload = json.loads(row["payload"])
        summary = {
            key: payload[key]
            for key in ("type", "id", "name", "ok", "error", "reason", "exit_code", "signal", "attempt")
            if key in payload
        }
        if payload.get("type") == "done":
            error = payload.get("message", {}).get("meta", {}).get("error")
            if error:
                summary["error"] = error
        if isinstance(summary.get("error"), str):
            summary["error"] = _bounded(summary["error"], max_chars)
        if payload.get("message") and isinstance(payload["message"], str):
            summary["message"] = _bounded(payload["message"], max_chars)
        events.append(
            {
                "seq": row["seq"],
                **summary,
                **({"data": _bounded(payload, max_chars, data_offset)} if include_data else {}),
            }
        )
    return {
        "session": turn,
        "events": events,
        "next_after_seq": events[-1]["seq"] if len(rows) > effective_limit else None,
    }
