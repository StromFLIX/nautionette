"""Typed workflow actions with persistent deduplication and stable Temporal IDs."""

from __future__ import annotations

import json
import time
from typing import Any

import jsonschema
from fastapi import HTTPException

from . import dashboards, runs
from .clients import authoring
from .dashboard_data import lock, public_data
from .dashboard_schema import Invoke, bounded, check_schema, pointer
from .db import db
from .events import bus

ACTIVE = {"starting", "uncertain", "running"}


def history(dashboard_id: str, workflow_id: str | None = None) -> list[dict[str, Any]]:
    # Release every terminal concurrency fence, not only the 40 newest history rows.
    db.execute(
        "UPDATE dashboard_action_runs SET state = (SELECT status FROM runs r "
        "WHERE r.workflow_id = dashboard_action_runs.workflow_id) WHERE dashboard_id = ? "
        "AND state IN ('starting','uncertain','running') AND EXISTS "
        "(SELECT 1 FROM runs r WHERE r.workflow_id = dashboard_action_runs.workflow_id "
        "AND r.status != 'running')",
        (dashboard_id,),
    )
    rows = db.query(
        "SELECT a.*, r.status AS run_status, r.result FROM dashboard_action_runs a "
        "LEFT JOIN runs r ON r.workflow_id = a.workflow_id "
        "WHERE dashboard_id = ? AND (? IS NULL OR a.workflow_id = ?) ORDER BY a.created_at DESC LIMIT 40",
        (dashboard_id, workflow_id, workflow_id),
    )
    output = []
    for row in rows:
        state = row["run_status"] or row["state"]
        if row["run_status"] and state != row["state"]:
            db.execute(
                "UPDATE dashboard_action_runs SET state = ? WHERE workflow_id = ?",
                (state, row["workflow_id"]),
            )
        try:
            result = public_data(json.loads(row["result"])) if row["result"] else None
            bounded(result, 32768)
        except ValueError:
            result = {"summary": "Result exceeds dashboard limit; inspect the run history"}
        output.append(
            {
                "action_id": row["action_id"],
                "request_id": row["request_id"],
                "workflow": row["workflow"],
                "workflow_id": row["workflow_id"],
                "status": state,
                "result": result,
                "created_at": row["created_at"],
                "request": public_data(json.loads(row["request"])),
                "url": f"/runs/{row['workflow_id']}",
            }
        )
    return output


def plan(dashboard: dict[str, Any], action: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
    inputs = dict(action["inputs"])
    for binding in [*action["bindings"].values(), *action["conditions"]]:
        state = sources.get(binding["source"], {})
        if state.get("state") not in {"fresh", "static"}:
            raise ValueError(f"Source {binding['source']} is not fresh")
    for key, binding in action["bindings"].items():
        inputs[key] = pointer(sources[binding["source"]]["data"], binding["path"])
    for condition in action["conditions"]:
        if pointer(sources[condition["source"]]["data"], condition["path"]) != condition["equals"]:
            raise ValueError(f"Source condition for {condition['source']} is not satisfied")
    return {
        "resolved_inputs": public_data(inputs),
        "context_hash": dashboards.digest(
            {
                "revision": dashboard["revision"],
                "workflow": action["workflow"],
                "inputs": inputs,
                "conditions": action["conditions"],
            }
        ),
    }


async def availability(dashboard: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
    definitions = dashboard["manifest"]["actions"]
    if not definitions:
        return {}
    try:
        deployed = {item["name"] for item in await authoring.list_workflows()}
    except Exception:  # noqa: BLE001 - definition outages are explicit unavailable actions
        deployed = set()
    output = {}
    for key, action in definitions.items():
        result = {**action, "enabled": False, "reason": "", "resolved_inputs": {}, "context_hash": ""}
        try:
            dashboards.permit(dashboard["manifest"]["agent_id"], "backend_run_workflow")
            if action["workflow"] not in deployed:
                raise ValueError("Workflow is unavailable")
            if db.workflow_settings(action["workflow"])["disabled"]:
                raise ValueError("Workflow is disabled")
            result.update(plan(dashboard, action, sources), enabled=True)
        except HTTPException:
            result["reason"] = "Owning agent cannot run workflows"
        except ValueError as exc:
            result["reason"] = str(exc)
        output[key] = result
    return output


async def invoke(dashboard_id: str, action_id: str, request: Invoke) -> dict[str, Any]:
    request_hash = dashboards.digest(request.model_dump())
    async with lock(f"action:{dashboard_id}:{action_id}"):
        current = dashboards.get(dashboard_id)
        dashboards.permit(current["manifest"]["agent_id"], "backend_run_workflow")
        reservation = db.one(
            "SELECT * FROM dashboard_action_requests "
            "WHERE dashboard_id = ? AND action_id = ? AND request_id = ?",
            (dashboard_id, action_id, request.request_id),
        )
        existing = (
            db.one("SELECT * FROM dashboard_action_runs WHERE workflow_id = ?", (reservation["workflow_id"],))
            if reservation
            else None
        )
        if reservation and reservation["request_hash"] != request_hash:
            raise HTTPException(
                409, "Request ID was already used with different inputs; inspect the existing run"
            )
        history(
            dashboard_id
        )  # reconcile terminal run states before checking the per-action concurrency fence
        if not existing:
            if current["revision"] != request.expected_revision:
                raise HTTPException(409, "Dashboard changed; review the action again")
            from .dashboard_data import snapshot

            view = await snapshot(dashboard_id, force=True)
            if view["revision"] != request.expected_revision:
                raise HTTPException(409, "Dashboard changed while preparing the action")
            action = view["actions"].get(action_id)
            if not action:
                raise HTTPException(404, "Dashboard action not found")
            if not action["enabled"]:
                raise HTTPException(409, action["reason"])
            if action["confirm"] and not request.confirmed:
                raise HTTPException(422, "Explicit workflow action confirmation is required")
            if request.context_hash != action["context_hash"]:
                raise HTTPException(409, "Source data or action changed; refresh and review the exact inputs")
            try:
                jsonschema.validate(request.inputs, action["input_schema"])
            except jsonschema.ValidationError as exc:
                raise HTTPException(422, f"Invalid workflow form input at {list(exc.path)}") from None
            merged = {**action["inputs"], **request.inputs}
            # Use original (not redacted) source values only after the plan has passed.
            for key, binding in action["bindings"].items():
                merged[key] = pointer(view["sources"][binding["source"]]["data"], binding["path"])
            try:
                bounded(merged, 32768)
            except ValueError:
                raise HTTPException(422, "Resolved workflow inputs exceed dashboard limits") from None
            active = db.one(
                "SELECT * FROM dashboard_action_runs WHERE dashboard_id = ? AND action_id = ? "
                "AND state IN ('starting','uncertain','running')",
                (dashboard_id, action_id),
            )
            if active:
                if json.loads(active["input"]) != merged or active["workflow"] != action["workflow"]:
                    raise HTTPException(409, "This action already has an active run with different inputs")
                # Persist aliases as well: a lost deduplicated response must not start a later replacement.
                db.execute(
                    "INSERT INTO dashboard_action_requests VALUES (?,?,?,?,?)",
                    (dashboard_id, action_id, request.request_id, request_hash, active["workflow_id"]),
                )
                existing = active
            else:
                definition = await authoring.get_workflow(action["workflow"])
                try:
                    schema = (definition.get("manifest") or {}).get("inputs") or {}
                    check_schema(schema, local_refs=True)
                    jsonschema.validate(merged, schema)
                except jsonschema.ValidationError as exc:
                    raise HTTPException(422, f"Workflow input mismatch at {list(exc.path)}") from None
                except ValueError:
                    raise HTTPException(422, "Workflow input schema changed or is unsupported") from None
                suffix = dashboards.digest([dashboard_id, action_id, request.request_id])[:32]
                workflow_id = f"{action['workflow']}-dashboard-{suffix}"
                # This fence survives process restarts and is committed BEFORE the external mutation.
                with db.transaction() as conn:
                    row = conn.execute(
                        "SELECT revision, archived FROM dashboards WHERE id = ?", (dashboard_id,)
                    ).fetchone()
                    if row["revision"] != request.expected_revision or row["archived"]:
                        raise HTTPException(409, "Dashboard changed before workflow submission")
                    conn.execute(
                        "INSERT INTO dashboard_action_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            dashboard_id,
                            action_id,
                            request.request_id,
                            request_hash,
                            dashboards.encoded(request.model_dump()),
                            action["workflow"],
                            workflow_id,
                            dashboards.encoded(merged),
                            "starting",
                            time.time(),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO dashboard_action_requests VALUES (?,?,?,?,?)",
                        (dashboard_id, action_id, request.request_id, request_hash, workflow_id),
                    )
                existing = db.one("SELECT * FROM dashboard_action_runs WHERE workflow_id = ?", (workflow_id,))
        row = runs.stored_run(existing["workflow_id"])
        if existing["state"] == "rejected":
            return history(dashboard_id, existing["workflow_id"])[0]
        if not row:
            if time.time() - existing["created_at"] > 3600:
                raise HTTPException(
                    409, "Submission is too old to retry safely; inspect the original run manually"
                )
            try:
                await runs.start(
                    existing["workflow"],
                    json.loads(existing["input"]),
                    trigger="dashboard",
                    stable_id=existing["workflow_id"],
                )
            except HTTPException:
                # runs.start's local preflight rejected this request before contacting Temporal.
                db.execute(
                    "UPDATE dashboard_action_runs SET state = 'rejected' WHERE workflow_id = ?",
                    (existing["workflow_id"],),
                )
                raise
            except Exception:  # noqa: BLE001 - a lost response must NEVER produce a new workflow ID
                db.execute(
                    "UPDATE dashboard_action_runs SET state = 'uncertain' WHERE workflow_id = ?",
                    (existing["workflow_id"],),
                )
                bus.publish("dashboard.action", {"dashboard_id": dashboard_id})
                raise HTTPException(
                    503,
                    {
                        "message": "Workflow start could not be confirmed. Retry the SAME request ID; "
                        "do not create a replacement action or request.",
                        "workflow_id": existing["workflow_id"],
                        "request_id": existing["request_id"],
                    },
                ) from None
        state = (runs.stored_run(existing["workflow_id"]) or {}).get("status", "running")
        db.execute(
            "UPDATE dashboard_action_runs SET state = ? WHERE workflow_id = ?",
            (state, existing["workflow_id"]),
        )
        bus.publish("dashboard.action", {"dashboard_id": dashboard_id})
        return history(dashboard_id, existing["workflow_id"])[0]
