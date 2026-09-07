"""Observed Temporal operations, correlated by event IDs rather than activity names."""

from __future__ import annotations

from typing import Any

from .activity_details import activity_metadata


def execution_graph(
    info: dict[str, Any], history: list[dict[str, Any]], truncated: bool = False
) -> dict[str, Any]:
    status = info.get("status", "unknown").lower()
    root = {
        "id": "workflow",
        "kind": "workflow",
        "label": info.get("workflow_type", info["workflow_id"]),
        "status": status,
        "started_at": info.get("start_time"),
        "finished_at": info.get("close_time"),
        "workflow_id": info["workflow_id"],
        "run_id": info.get("run_id"),
    }
    nodes: list[dict[str, Any]] = [root]
    edges: list[dict[str, str]] = []
    operations: dict[int, dict[str, Any]] = {}
    terminal = None
    for event in history:
        family, _, action = event["event"].partition(".")
        if family == "workflow":
            if action == "started":
                root.update({key: event[key] for key in ("input", "task_queue") if key in event})
            elif action in {"completed", "failed", "timed_out", "terminated", "canceled", "continued_as_new"}:
                terminal = event
            continue
        if (
            (family in {"activity", "child"} and action == "scheduled")
            or (family == "timer" and action == "started")
            or family == "signal"
        ):
            label = event.get("activity") or event.get("workflow_type") or event.get("signal_name")
            if family == "timer":
                label = f"Wait {event.get('duration_seconds', '?')}s"
            node = {
                **event,
                "id": f"event-{event['id']}",
                "event_id": event["id"],
                "kind": family,
                "label": label or family.title(),
                "status": "waiting"
                if family == "timer"
                else "received"
                if family == "signal"
                else "scheduled",
                "scheduled_at": event["at"],
            }
            if family in {"timer", "signal"}:
                node["started_at"] = event["at"]
            if family == "signal":
                node["finished_event_id"] = event["id"]
            operations[event["id"]] = node
            nodes.append(node)
            continue
        reference = (
            event.get("scheduled_event_id")
            or event.get("initiated_event_id")
            or event.get("started_event_id")
        )
        node = operations.get(reference)
        if node is None:
            continue
        node.update(
            {
                key: event[key]
                for key in ("input", "result", "error", "attempt", "workflow_id", "run_id")
                if key in event
            }
        )
        if action == "started":
            node["status"] = "running"
            node["started_at"] = event["at"]
        else:
            node["status"] = "completed" if action == "fired" else action
            node["finished_at"] = event["at"]
            node["finished_event_id"] = event["id"]

    if terminal:
        status = terminal["event"].partition(".")[2]
        root["status"] = status
        root["finished_at"] = terminal["at"]
    pending = {item["activity_id"]: item for item in info.get("pending_activities", [])}
    for node in nodes[1:]:
        if node["kind"] == "activity":
            payload = node.get("input")
            if isinstance(payload, list) and len(payload) == 1:
                payload = payload[0]
            node.update(activity_metadata(node["label"], payload, result=node.get("result")))
        activity = pending.get(node.get("activity_id"))
        if activity and status == "running" and not node.get("finished_event_id"):
            state = activity["state"]
            node["status"] = "running" if state == "started" else state
            if state == "scheduled" and activity.get("attempt", 0) > 1:
                node["status"] = "retrying"
            for key in ("attempt", "started_at", "error"):
                if activity.get(key) is not None:
                    node[key] = activity[key]
        elif status not in {"running", "unknown"} and not node.get("finished_event_id"):
            node["status"] = "unknown" if truncated or node["kind"] == "child" else "interrupted"

    frontier: list[dict[str, Any]] = []
    groups: dict[int, list[str]] = {}
    for node in nodes[1:]:
        command = node.get("workflow_task_completed_event_id") or node["event_id"]
        if command not in groups:
            finished = [item for item in frontier if item.get("finished_event_id", float("inf")) < command]
            groups[command] = [item["id"] for item in finished] or ["workflow"]
            frontier = [item for item in frontier if item not in finished]
        for source in groups[command]:
            edges.append({"id": f"edge-{len(edges)}", "source": source, "target": node["id"], "label": ""})
        frontier.append(node)
    if terminal:
        ending = {
            **terminal,
            "id": "outcome",
            "kind": "return",
            "label": status.replace("_", " ").title(),
            "status": status,
            "finished_at": terminal["at"],
        }
        nodes.append(ending)
        for source in [item["id"] for item in frontier] or ["workflow"]:
            edges.append({"id": f"edge-{len(edges)}", "source": source, "target": "outcome", "label": ""})
    warnings = []
    if truncated:
        warnings.append("Only the first 2,000 history events are shown. Later steps may be missing.")
    if not history:
        warnings.append("No execution history is available yet.")
    return {"mode": "execution", "nodes": nodes, "edges": edges, "warnings": warnings, "status": status}
