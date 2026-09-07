"""Durable chat progress, independent of any client's connection."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from . import git_authorship, projects
from .agent import Timeline, build_history, stream_agent
from .background import spawn
from .clients import broker
from .db import db
from .events import bus, sse
from .runtime import history_budget, remember_agent_result


def launch_next(chat_id: str) -> None:
    turn = db.next_chat_turn(chat_id)
    if turn:
        spawn(run_turn(turn["id"], chat_id, json.loads(turn["job"])), name=f"chat-{turn['id']}")


async def control_turn(turn_id: str, chat_id: str, job: dict[str, Any], finished: asyncio.Event) -> None:
    sent = set()
    while not finished.is_set():
        turn = db.one("SELECT * FROM chat_turns WHERE id = ? AND state = 'running'", (turn_id,))
        if not turn:
            return
        try:
            if turn["stop_requested"]:
                await broker.control_agent(chat_id, turn_id, {"id": f"stop-{turn_id}", "type": "stop"})
            else:
                queued = db.query(
                    "SELECT * FROM chat_turns WHERE chat_id = ? AND state = 'queued' ORDER BY rowid",
                    (chat_id,),
                )
                for pending in queued:
                    if not pending["job"]:
                        break
                    candidate = json.loads(pending["job"])
                    if any(
                        candidate.get(key) != job.get(key)
                        for key in ("agent_set", "model", "tools", "project_ids")
                    ):
                        break
                    if pending["id"] in sent:
                        continue
                    if not await broker.control_agent(
                        chat_id,
                        turn_id,
                        {
                            "id": pending["id"],
                            "type": "steer",
                            "text": candidate["prompt"],
                        },
                    ):
                        break
                    sent.add(pending["id"])
        except Exception as exc:
            logging.getLogger("nautionette").warning("Chat control delivery failed: %s", exc)
        try:
            await asyncio.wait_for(finished.wait(), 0.2)
        except TimeoutError:
            pass


async def run_turn(turn_id: str, chat_id: str, job: dict[str, Any]) -> None:
    timeline = Timeline()
    received_text = False
    failure = None
    status = ""
    controller = None
    finished = asyncio.Event()
    interrupted = False
    shutdown = False
    try:
        turn = db.one("SELECT stop_requested FROM chat_turns WHERE id = ?", (turn_id,))
        if not turn or turn["stop_requested"]:
            interrupted = True
            return
        job["history"] = build_history(
            [
                message
                for message in db.list_messages(chat_id)
                if message["id"] != turn_id and not message["meta"].get("queued")
            ],
            max_chars=history_budget(job.get("model")),
        )
        chat = db.get_chat(chat_id)
        if chat:
            previous_status = job.get("internet_status", "blocked")
            job["internet_status"] = chat["internet_status"]
            job["internet_allowed"] = chat["internet_status"] == "allowed"
            job["system_prompt"] = job.get("system_prompt", "").replace(
                f"Direct internet access is {previous_status}",
                f"Direct internet access is {chat['internet_status']}",
            )
        controller = spawn(control_turn(turn_id, chat_id, job, finished), name=f"chat-control-{turn_id}")
        if job.get("project_ids"):
            # Resolve on execution, not enqueue: queued/new turns see the latest settings.
            job["git_authorship"] = git_authorship.for_job()
            job["system_prompt"] = job.get("system_prompt", "") + (
                "\nGit authorship is configured by Settings for this call. Use local Git so the configured "
                "author/committer environment and co-author hook are honored. Do not override identities "
                "or bypass hooks unless the user explicitly requests it. Verify the author, committer and "
                "Co-authored-by trailers with git log -1 --format=full before pushing. "
                "Do not amend or rewrite existing commits merely to apply authorship settings.\n"
            )
        job.update(projects.prepare_worktrees(chat_id, job.get("project_ids", [])))
        job["project_credentials"] = await projects.agent_credentials(job.get("project_ids", []))
        async for event in stream_agent(job):
            if not db.one("SELECT id FROM chat_turns WHERE id = ?", (turn_id,)):
                return
            kind = event.get("type")
            if kind == "input_consumed":
                if db.consume_chat_input(
                    turn_id,
                    event.get("id", ""),
                    timeline.text,
                    {"tools": timeline.tools, "steps": timeline.steps},
                ):
                    timeline = Timeline()
            elif kind == "interrupted":
                interrupted = True
            status = event.get("message", "") if kind == "status" else ""
            if kind == "delta":
                text = event.get("text", "")
                received_text = received_text or bool(text)
                timeline.add_text(text)
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
                # The result summarizes the entire container run, not this
                # segment. Do not replay an already-saved pre-steering answer.
                if not received_text and event.get("text"):
                    timeline.add_text(event["text"])
                    received_text = True
            db.record_chat_progress(turn_id, event, timeline.steps, status)
    except asyncio.CancelledError:
        shutdown = True
        failure = "The answer was interrupted by a backend shutdown."
        raise
    except Exception as exc:
        failure = str(exc)
        if db.one("SELECT id FROM chat_turns WHERE id = ?", (turn_id,)):
            db.record_chat_progress(turn_id, {"type": "error", "message": failure}, timeline.steps, "")
    finally:
        if controller is not None:
            finished.set()
            if shutdown:
                controller.cancel()
            await asyncio.gather(controller, return_exceptions=True)
        turn = db.one("SELECT stop_requested FROM chat_turns WHERE id = ?", (turn_id,))
        interrupted = interrupted or bool(turn and turn["stop_requested"])
        if interrupted:
            failure = "Stopped by you."
        db.execute(
            "UPDATE chats SET internet_status = 'blocked', internet_reason = '', internet_turn_id = '' "
            "WHERE id = ? AND internet_turn_id = ? AND internet_status = 'pending'",
            (chat_id, turn_id),
        )
        content = timeline.text or (f"The agent could not answer: {failure}" if failure else "(no answer)")
        db.finish_chat_turn(
            turn_id,
            content,
            {"tools": timeline.tools, "steps": timeline.steps, "error": failure, "interrupted": interrupted},
        )
        bus.publish("chat.answered", {"chat_id": chat_id, "ok": failure is None})
        await projects.revoke_credentials(job.pop("project_credentials", []))
        if not shutdown:
            launch_next(chat_id)


def recover_interrupted() -> None:
    db.execute(
        "UPDATE chats SET queue_paused = 1 WHERE id IN "
        "(SELECT chat_id FROM chat_turns WHERE state IN ('running', 'queued'))"
    )
    db.execute(
        "UPDATE projects SET status = 'failed', error = 'Download interrupted; retry' "
        "WHERE status = 'cloning'"
    )
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
