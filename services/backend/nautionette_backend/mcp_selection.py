"""Request-scoped discovery optimization, never an authorization boundary.

Old clients omit the header and keep all targets. Backend capability checks and
Pi's exact tool allowlist still govern calls. Only our exact CEL wrapper is removed
from logical ownership snapshots; external policies/conditions are preserved.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

HEADER = "x-nautionette-mcp-targets"
CONDITION = (
    'mcp.target.name == "backend" || '
    f'!("{HEADER}" in request.headers) || '
    f'mcp.target.name in request.headers["{HEADER}"].split(",")'
)
_SUFFIX = f") && ({CONDITION})"


def target_headers(targets: list[str] | None) -> dict[str, str]:
    if targets is None:
        return {}
    if not isinstance(targets, list) or any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
        for name in targets
    ):
        raise ValueError("Invalid MCP target selection")
    value = ",".join(sorted(set(targets)))
    if len(value) > 8000:
        raise ValueError("Invalid MCP target selection")
    return {HEADER: value}


def has_scope(target: dict[str, Any]) -> bool:
    condition = target.get("condition")
    return condition == CONDITION or (
        isinstance(condition, str) and condition.startswith("(") and condition.endswith(_SUFFIX)
    )


def logical_target(target: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(target)
    condition = value.get("condition")
    if condition == CONDITION:
        value.pop("condition")
    elif isinstance(condition, str) and condition.startswith("(") and condition.endswith(_SUFFIX):
        value["condition"] = condition[1 : -len(_SUFFIX)]
    return value


def scoped_target(target: dict[str, Any]) -> dict[str, Any]:
    value = logical_target(target)
    condition = value.get("condition")
    value["condition"] = f"({condition}) && ({CONDITION})" if condition else CONDITION
    return value


def targets_for_tools(
    tools: list[str], targets: list[dict[str, Any]], local: set[str], offline: dict[str, str]
) -> list[str] | None:
    # Truly singleton gateways use raw names; never guess their ownership or rename them.
    if len(targets) < 2:
        return None
    selected = set()
    for name in tools:
        if name in local:
            continue
        if name in offline:
            # Keep the owner in the immutable scope: a fresh authenticated
            # session must include it after demand-driven login publishes it.
            selected.add(offline[name])
            continue
        owners = [target["name"] for target in targets if name.startswith(target["name"] + "_")]
        if len(owners) != 1:
            return None  # Unknown/raw pinned names fall back to ordinary full discovery.
        selected.add(owners[0])
    return sorted(selected)


async def for_run(tools: list[str] | None) -> list[str] | None:
    if tools is None:
        return None
    if not tools:
        return []
    from nautionette.artcraft import TOOL_NAMES as CRAFT_TOOL_NAMES

    from .chat_outputs import RESERVED_TOOL_NAMES
    from .clients import gateway
    from .db import db

    try:
        config = await gateway.config()
    except Exception:  # noqa: BLE001 - unavailable optimization never expands Pi's exact tool allowlist
        return None
    targets = config.get("targets") or []
    live = {target["name"] for target in targets}
    offline = {
        row["name"]: row["server"]
        for row in db.query("SELECT name,server FROM mcp_oauth_tools")
        if row["server"] not in live
    }
    local = (
        set(RESERVED_TOOL_NAMES)
        | set(CRAFT_TOOL_NAMES)
        | {"list_subagents", "call_subagent", "wait_for_subagent"}
    )
    selected = targets_for_tools(tools, targets, local, offline)
    try:
        target_headers(selected)
    except ValueError:
        return None  # Custom naming outside the header contract keeps legacy discovery.
    return selected
