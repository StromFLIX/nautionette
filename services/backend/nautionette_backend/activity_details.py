"""Readable built-in activity metadata from source expressions or observed payloads."""

from __future__ import annotations

import ast
import json
from typing import Any

_BUILTINS = {
    "agent_call": ("Agent", "Run agent"),
    "mcp_call": ("MCP tool", "Call MCP tool"),
    "http_fetch": ("HTTP request", "Fetch URL"),
    "emit_event": ("Event", "Emit event"),
    "save_artifact": ("Artifact", "Save artifact"),
    "read_artifact": ("Artifact", "Read artifact"),
}


def activity_metadata(
    name: str, payload: Any, *, options: dict[str, Any] | None = None, result: Any = None
) -> dict[str, Any]:
    if name not in _BUILTINS:
        return {}
    category, title = _BUILTINS[name]
    if isinstance(payload, ast.Dict) and all(
        isinstance(key, ast.Constant) and isinstance(key.value, str) for key in payload.keys
    ):
        params = {key.value: value for key, value in zip(payload.keys, payload.values, strict=True)}
    else:
        params = payload if isinstance(payload, dict) else None
    details: list[dict[str, Any]] = []

    def detail(label: str, value: Any) -> dict[str, Any]:
        dynamic = False
        if isinstance(value, ast.AST):
            try:
                value = ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError, RecursionError):
                value = ast.unparse(value)
                dynamic = True
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, default=str)
        item = {"label": label, "value": text[:1200], "dynamic": dynamic}
        details.append(item)
        return item

    def field(label: str, key: str, default: Any = None) -> dict[str, Any] | None:
        if params is None:
            item = detail(label, "From activity input")
            item["dynamic"] = True
            return item
        value = params.get(key, default)
        if isinstance(value, ast.Constant):
            value = value.value
        if key in {"agent_set", "method", "kind"} and not isinstance(value, ast.AST):
            value = value or default
        if value is None:
            return None
        return detail(label, value)

    if name == "agent_call":
        agent = field("Agent set", "agent_set", "Runtime default")
        field("Prompt", "prompt", "")
        tools = result.get("tools") if isinstance(result, dict) else None
        if isinstance(tools, list):
            detail("Tools used", ", ".join(dict.fromkeys(str(tool) for tool in tools)) or "None")
        else:
            detail("Tools", "Selected by the agent at runtime")
        field("Output schema", "output_schema")
        field("System prompt", "system_prompt")
        field("Agent timeout (s)", "timeout_seconds")
        if agent and not agent["dynamic"]:
            title = f"Agent: {agent['value']}"
    elif name == "mcp_call":
        tool = field("Tool", "tool")
        if tool and not tool["dynamic"]:
            title = tool["value"]
            if "_" in tool["value"]:
                detail("Server", tool["value"].split("_", 1)[0])
        field("Arguments", "arguments", {})
    elif name == "http_fetch":
        method = field("Method", "method", "GET")
        field("URL", "url")
        field("Query", "params")
        field("JSON body", "json")
        if method and not method["dynamic"]:
            title = f"{method['value'].upper()} request"
        if isinstance(result, dict) and result.get("status") is not None:
            detail("HTTP status", result["status"])
    elif name == "emit_event":
        kind = field("Event", "kind", "workflow.event")
        field("Payload", "payload", {})
        if kind and not kind["dynamic"]:
            title = kind["value"]
    else:
        field("File", "name", "artifact.txt" if name == "save_artifact" else "")
        if name == "save_artifact":
            field("Content", "content", "")
        if isinstance(result, dict):
            for key in ("path", "bytes"):
                if key in result:
                    detail(key.title(), result[key])
    if isinstance(payload, ast.AST) and params is None:
        detail("Input expression", payload)
    for key, label in (
        ("task_queue", "Task queue"),
        ("start_to_close_timeout", "Execution timeout"),
        ("schedule_to_close_timeout", "Total timeout"),
        ("heartbeat_timeout", "Heartbeat timeout"),
        ("retry_policy", "Retry policy"),
    ):
        if key in (options or {}):
            detail(label, options[key])
    return {"activity_type": name, "category": category, "title": title, "details": details}
