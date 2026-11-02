"""Payload-free, durable accounting of workflow agent and direct MCP attempts.

This ledger is independent of Temporal retention and chat delivery. It records
observations, not estimates: absent provider token usage stays unknown.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .db import db

TOKEN_FIELDS = ("input", "output", "cacheRead", "cacheWrite")


def token_counts(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    if not all(type(usage.get(key)) is int and usage[key] >= 0 for key in TOKEN_FIELDS):
        return None
    counts = {key: usage[key] for key in TOKEN_FIELDS}
    # Pi uses all-zero placeholders when the provider did not report usage.
    return counts if sum(counts.values()) else None


class AgentStatistics:
    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self._latest_tool: dict[str, dict[str, Any]] = {}
        self.requests = 0
        self.reported = 0
        self.tokens = dict.fromkeys(TOKEN_FIELDS, 0)
        self.legacy_usage = False

    def observe(self, event: dict[str, Any]) -> bool:
        kind = event.get("type")
        if kind in {"tool", "tool_done"}:
            call_id = event.get("id") or f"unidentified:{event.get('name') or ''}"
            tool = self._latest_tool.get(call_id) if kind == "tool_done" else None
            if tool is None:
                # A provider may reuse a call ID in a later model turn. Count
                # executions, not globally unique provider-generated IDs.
                tool = {
                    "name": str(event.get("name") or "Unknown")[:512],
                    "source": event.get("source") if event.get("source") in {"mcp", "local"} else "extension",
                    "status": "running",
                }
                self.tools.append(tool)
                self._latest_tool[call_id] = tool
            if kind == "tool_done":
                tool["status"] = "failed" if event.get("error") else "completed"
            return True
        if kind == "usage":
            self.requests += 1
            usage = event.get("usage")
            context = event.get("context")
            if usage is None and isinstance(context, dict) and context.get("source") == "provider":
                # Older supervisors emitted per-response context, whose input
                # already includes cache reads/writes. Do not count those twice.
                keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
                if all(type(context.get(key)) is int for key in keys):
                    usage = {
                        "input": context["input_tokens"]
                        - context["cache_read_tokens"]
                        - context["cache_write_tokens"],
                        "output": context["output_tokens"],
                        "cacheRead": context["cache_read_tokens"],
                        "cacheWrite": context["cache_write_tokens"],
                    }
            self._usage(usage)
            return True
        if kind == "result" and not self.reported and event.get("usage"):
            # Older images return only the LAST response. Never present that as
            # the full call's token consumption or invent a model request count.
            self.legacy_usage = True
            self._usage(event["usage"])
            return True
        return False

    def _usage(self, usage: Any) -> None:
        counts = token_counts(usage)
        if counts is not None:
            self.reported += 1
            self.tokens = {key: self.tokens[key] + counts[key] for key in TOKEN_FIELDS}

    def snapshot(self) -> dict[str, Any]:
        grouped: dict[tuple[str, str], Counter] = {}
        for tool in self.tools:
            counts = grouped.setdefault((tool["name"], tool["source"]), Counter())
            counts["calls"] += 1
            counts[tool["status"]] += 1
        return {
            "model_requests": self.requests,
            "usage_reports": self.reported,
            "tokens": self.tokens if self.reported else None,
            "usage_complete": bool(
                self.requests and self.reported == self.requests and not self.legacy_usage
            ),
            "tools": [
                {"name": name, "source": source, **counts}
                for (name, source), counts in sorted(grouped.items())
            ],
        }


def record_call(
    workflow_id: str,
    call_id: str,
    kind: str,
    name: str,
    status: str,
    *,
    model: str = "",
    statistics: dict[str, Any] | None = None,
) -> None:
    if not workflow_id:
        return
    db.execute(
        "INSERT INTO workflow_calls (workflow_id, call_id, kind, name, model, status, statistics) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(workflow_id, call_id) DO UPDATE SET "
        "status = excluded.status, statistics = excluded.statistics "
        "WHERE workflow_calls.status = 'running'",
        (workflow_id, call_id, kind, name, model, status, json.dumps(statistics or {})),
    )


def statistics(workflow_id: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = db.query("SELECT * FROM workflow_calls WHERE workflow_id = ?", (workflow_id,))
    tracked = bool((row or {}).get("statistics_version"))
    agents: dict[tuple[str, str], Counter] = {}
    tools: dict[tuple[str, str], Counter] = {}
    tokens = dict.fromkeys(TOKEN_FIELDS, 0)
    requests = reported = agent_calls = direct_mcp = 0
    usage_complete = True
    for call in rows:
        status = call["status"]
        if status == "running" and row and row["status"] not in {"running", "started"}:
            status = "interrupted"
        if call["kind"] == "mcp":
            counts = tools.setdefault((call["name"], "workflow"), Counter())
            counts["calls"] += 1
            counts[status] += 1
            direct_mcp += 1
            continue
        agent_calls += 1
        counts = agents.setdefault((call["name"], call["model"]), Counter())
        counts["calls"] += 1
        counts[status] += 1
        data = json.loads(call["statistics"])
        requests += data.get("model_requests", 0)
        reported += data.get("usage_reports", 0)
        usage_complete = (
            usage_complete and data.get("usage_complete", False) and status not in {"running", "interrupted"}
        )
        if data.get("tokens"):
            for key in TOKEN_FIELDS:
                tokens[key] += data["tokens"].get(key, 0)
        for tool in data.get("tools", []):
            source = "agent_mcp" if tool["source"] == "mcp" else tool["source"]
            counts = tools.setdefault((tool["name"], source), Counter())
            for key in ("calls", "completed", "failed", "running"):
                target = "interrupted" if key == "running" and status != "running" else key
                counts[target] += tool.get(key, 0)
    tool_rows = [
        {"name": name, "source": source, **counts} for (name, source), counts in sorted(tools.items())
    ]
    mcp_calls = sum(item["calls"] for item in tool_rows if item["source"] in {"workflow", "agent_mcp"})
    return {
        "available": tracked or bool(rows),
        "coverage": "recorded" if tracked else "partial" if rows else "unavailable",
        "agent_calls": agent_calls,
        "model_requests": requests,
        "tool_calls": sum(item["calls"] for item in tool_rows),
        "mcp_calls": mcp_calls,
        "direct_mcp_calls": direct_mcp,
        "usage_reports": reported,
        "tokens": {**tokens, "total": sum(tokens.values())} if reported else None,
        "usage_complete": bool(agent_calls and usage_complete),
        "agents": [
            {"agent_set": name, "model": model, **counts} for (name, model), counts in sorted(agents.items())
        ],
        "tools": tool_rows,
    }
