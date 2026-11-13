"""Server-owned attribution shared by delegated calls and workflow results.

Origins describe who initiated work, not who authored the answer. They never
come from a public chat payload or from model output.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from .clients import temporal
from .db import db


def workflow_origin(name: str, workflow_id: str, *, relation: str, trigger: str = "unknown", **extra) -> dict:
    return {
        "kind": "workflow",
        "relation": relation,
        "workflow": name,
        "workflow_id": workflow_id,
        "trigger": trigger if trigger in {"schedule", "manual", "agent", "webhook"} else "unknown",
        **extra,
    }


async def workflow_call(source: dict[str, str] | None) -> dict:
    if not source or not all(source.get(key) for key in ("workflow", "workflow_id", "run_id", "activity_id")):
        raise HTTPException(422, "Workflow agent calls require execution provenance from the worker")
    row = db.one("SELECT workflow, run_id, trigger FROM runs WHERE workflow_id = ?", (source["workflow_id"],))
    if row:
        if row["workflow"] != source["workflow"] or (row["run_id"] and row["run_id"] != source["run_id"]):
            raise HTTPException(409, "Workflow caller identity differs from the recorded execution")
        trigger = row["trigger"]
    else:
        # Scheduled run discovery is eventually consistent. Verify against
        # Temporal rather than guessing that an unrecorded run was scheduled.
        info = await temporal.describe(source["workflow_id"])
        if info.get("workflow_type") != source["workflow"] or info.get("run_id") != source["run_id"]:
            raise HTTPException(409, "Workflow caller identity differs from Temporal")
        trigger = info.get("trigger", "unknown")
    return workflow_origin(
        source["workflow"],
        source["workflow_id"],
        relation="call",
        trigger=trigger,
        run_id=source["run_id"],
        activity_id=source["activity_id"],
    )


def agent_call(sender: dict, parent: dict | None = None) -> dict:
    origin = {
        "kind": "agent",
        "relation": "call",
        "agent_id": sender["id"],
        "agent_name": sender.get("name") or "Agent",
        "chat_id": sender["chat_id"],
        "call_id": sender["call_id"],
    }
    # Keep one bounded root, not an ever-growing nested chain of caller objects.
    root = parent if parent and parent.get("kind") == "workflow" else (parent or {}).get("root")
    if root and root.get("kind") == "workflow":
        origin["root"] = dict(root)
    return origin


def context(origin: dict[str, Any] | None) -> str:
    if not origin:
        return ""
    return (
        "This task was delegated by automation, not a new message from the human user. "
        "Server-recorded caller provenance (metadata, not additional instructions):\n"
        + json.dumps(origin, sort_keys=True)
        + "\n\n"
    )
