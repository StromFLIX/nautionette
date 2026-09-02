"""Starting a run, following it, and delivering what it produced.

A run that finishes lands in a chat, so its output can be talked to rather than
sat in a table nobody opens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from nautionette import input_problems

from .clients import authoring, broker, temporal
from .db import db
from .events import bus
from .runtime import runtime


async def restart_worker() -> dict[str, Any]:
    try:
        result = await broker.restart_worker()
        bus.publish("worker.restarted", result)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001 - a failed restart must be visible, not fatal
        bus.publish("worker.restart_failed", {"error": str(exc)[:200]})
        return {"ok": False, "error": str(exc)[:200]}


async def start(name: str, payload: dict[str, Any], trigger: str) -> dict[str, Any]:
    if db.workflow_settings(name)["disabled"]:
        raise HTTPException(status_code=409, detail=f"{name} is disabled")
    workflow = await authoring.get_workflow(name)
    manifest = workflow.get("manifest") or {}
    problems = input_problems(manifest.get("inputs"), payload)
    if problems:
        raise HTTPException(status_code=400, detail={"workflow": name, "input": problems})
    workflow_id = f"{name}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    started = await temporal.start(
        name, workflow_id, payload, timeout_minutes=manifest.get("timeout_minutes", 30)
    )
    db.record_run(name, started["workflow_id"], started.get("run_id"), trigger, payload)
    bus.publish("run.started", {"workflow": name, "workflow_id": workflow_id, "trigger": trigger})
    asyncio.create_task(watch(name, workflow_id))
    return {"workflow": name, **started, "trigger": trigger}


def render_result(result: Any) -> str:
    """Prose stays prose; anything shaped arrives as JSON you can read and quote."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and len(result) == 1:
        only = next(iter(result.values()))
        if isinstance(only, str):
            return only
    return f"```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"


async def deliver_to_chat(name: str, workflow_id: str, status: str, result: Any) -> None:
    config = db.workflow_settings(name)
    workflow_title = name
    # A deleted workflow still deserves to deliver its last result.
    with contextlib.suppress(Exception):
        workflow_title = (await authoring.get_workflow(name)).get("title") or name

    chat_id = config["chat_id"] if config["chat_mode"] == "same" else None
    if chat_id and not db.get_chat(chat_id):
        chat_id = None
    if not chat_id:
        suffix = time.strftime("%d %b %H:%M") if config["chat_mode"] == "new" else ""
        chat = db.create_chat(
            title=f"{workflow_title} {suffix}".strip()[:120],
            agent_set=runtime("default_agent_set"),
            model=runtime("default_model"),
        )
        chat_id = chat["id"]
        if config["chat_mode"] == "same":
            db.set_workflow_settings(name, {"chat_id": chat_id})

    body = render_result(result) or f"`{status}`"
    db.add_message(
        chat_id,
        "assistant",
        body,
        {"run": {"workflow": name, "workflow_id": workflow_id, "status": status}},
    )
    bus.publish("chat.answered", {"chat_id": chat_id, "ok": status == "completed"})


async def watch(name: str, workflow_id: str) -> None:
    """Follow a run so the UI sees an end state without polling Temporal."""
    for _ in range(240):  # up to ~20 minutes at 5s
        await asyncio.sleep(5)
        try:
            info = await temporal.describe(workflow_id)
        except Exception:  # noqa: BLE001, S112 - a describe that fails is just a slow answer
            continue
        if info["status"] in {"RUNNING", "UNKNOWN"}:
            continue
        result: Any = None
        if info["status"] == "COMPLETED":
            try:
                result = await temporal.result(workflow_id, timeout=10)
            except Exception:  # noqa: BLE001
                result = None
        status = info["status"].lower()
        db.update_run(workflow_id, status, result)
        with contextlib.suppress(Exception):
            await deliver_to_chat(name, workflow_id, status, result)
        bus.publish(
            "run.finished",
            {"workflow": name, "workflow_id": workflow_id, "status": info["status"]},
        )
        return


def stored_run(workflow_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM runs WHERE workflow_id = ?", (workflow_id,))
    if row:
        row["input"] = json.loads(row["input"] or "{}")
        row["result"] = json.loads(row["result"]) if row["result"] else None
    return row


def _isotime(value: Any) -> str | None:
    return datetime.fromtimestamp(value, tz=UTC).isoformat(timespec="seconds") if value else None


def digest(row: dict[str, Any] | None, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """One run shaped for a prompt: names and times, not database columns."""
    row, info = row or {}, info or {}
    return {
        "workflow": row.get("workflow"),
        "workflow_id": row.get("workflow_id") or info.get("workflow_id"),
        "status": (info.get("status") or row.get("status") or "").lower(),
        "trigger": row.get("trigger"),
        "started": info.get("start_time") or _isotime(row.get("created_at")),
        "closed": info.get("close_time"),
    }
