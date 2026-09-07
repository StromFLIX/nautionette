"""Durable chat progress, independent of any client's connection."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from . import projects
from .agent import Timeline, stream_agent
from .db import db
from .events import bus, sse
from .runtime import remember_agent_result


async def run_turn(turn_id: str, chat_id: str, job: dict[str, Any]) -> None:
    timeline = Timeline()
    failure = None
    status = ""
    try:
        job.update(projects.prepare_worktrees(chat_id, job.get("project_ids", [])))
        job["project_credentials"] = await projects.agent_credentials(job.get("project_ids", []))
        async for event in stream_agent(job):
            if not db.one("SELECT id FROM chat_turns WHERE id = ?", (turn_id,)):
                return
            kind = event.get("type")
            status = event.get("message", "") if kind == "status" else ""
            if kind == "delta":
                timeline.add_text(event.get("text", ""))
            elif kind == "tool":
                timeline.start_tool(event)
                if event.get("name") == "request_internet_access":
                    arguments = event.get("args") or {}
                    reason = str(arguments.get("reason") or "The agent needs internet access.")[:1000]
                    db.execute(
                        "UPDATE chats SET internet_status = 'pending', internet_reason = ?, "
                        "internet_turn_id = ? WHERE id = ? AND internet_status = 'blocked'",
                        (reason, turn_id, chat_id),
                    )
            elif kind == "tool_done":
                timeline.finish_tool(event)
            elif kind == "error":
                failure = event.get("message")
            elif kind == "result":
                remember_agent_result(bool(event.get("ok")))
                if not event.get("ok") and not failure:
                    failure = event.get("message") or "The agent did not complete successfully."
                if not timeline.text and event.get("text"):
                    timeline.add_text(event["text"])
            db.record_chat_progress(turn_id, event, timeline.steps, status)
    except asyncio.CancelledError:
        failure = "The answer was interrupted by a backend shutdown."
        raise
    except Exception as exc:
        failure = str(exc)
        if db.one("SELECT id FROM chat_turns WHERE id = ?", (turn_id,)):
            db.record_chat_progress(turn_id, {"type": "error", "message": failure}, timeline.steps, "")
    finally:
        db.execute(
            "UPDATE chats SET internet_status = 'blocked', internet_reason = '', internet_turn_id = '' "
            "WHERE id = ? AND internet_turn_id = ? AND internet_status = 'pending'",
            (chat_id, turn_id),
        )
        content = timeline.text or (f"The agent could not answer: {failure}" if failure else "(no answer)")
        db.finish_chat_turn(
            turn_id, content, {"tools": timeline.tools, "steps": timeline.steps, "error": failure}
        )
        bus.publish("chat.answered", {"chat_id": chat_id, "ok": failure is None})
        await projects.revoke_credentials(job.pop("project_credentials", []))


def recover_interrupted() -> None:
    db.execute("UPDATE projects SET status = 'failed', error = 'Download interrupted; retry' WHERE status = 'cloning'")
    db.execute(
        "UPDATE chats SET internet_status = 'blocked', internet_reason = '', internet_turn_id = '' "
        "WHERE internet_status IN ('pending', 'deciding')"
    )
    for turn in db.query("SELECT * FROM chat_turns WHERE state = 'running'"):
        steps = json.loads(turn["steps"])
        failure = "The answer was interrupted by a backend restart."
        content = "".join(step.get("text", "") for step in steps if step.get("kind") == "text")
        db.finish_chat_turn(turn["id"], content or failure, {"steps": steps, "error": failure})


async def turn_events(turn_id: str):
    cursor = 0
    while True:
        rows = db.query(
            "SELECT seq, payload FROM chat_turn_events WHERE turn_id = ? AND seq > ? ORDER BY seq",
            (turn_id, cursor),
        )
        for row in rows:
            event = json.loads(row["payload"])
            cursor = row["seq"]
            yield sse(event)
            if event["type"] == "done":
                return
        if not db.one("SELECT id FROM chat_turns WHERE id = ?", (turn_id,)):
            return
        await asyncio.sleep(0.1)


async def chat_snapshots(chat_id: str):
    previous = ""
    ticks = 0
    while True:
        snapshot = db.chat_snapshot(chat_id)
        frame = sse({"type": "snapshot", **snapshot})
        if frame != previous:
            yield frame
            previous = frame
        elif ticks % 80 == 0:
            yield ": keep-alive\n\n"
        if snapshot["chat"] is None:
            return
        ticks += 1
        await asyncio.sleep(0.25)