"""The MCP face: the tools an agent calls, through agentgateway.

The REST side has to work without this, so a missing or broken MCP layer is
reported and skipped rather than allowed to take the service down.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from . import backend, store
from .validate import run_checks

log = logging.getLogger("workflow-mcp")

INSTRUCTIONS = (
    "Author Nautionette workflows. Read before you write, validate before you write. "
    "When a workflow misbehaves, read its runs first: list_runs finds it, read_run shows "
    "every step it took. Every write lands as a draft with a diff that a human approves; "
    "you never deploy anything yourself."
)


def _register(server: Any) -> None:
    @server.tool(
        description="List every workflow with its manifest, plus the drafts waiting for approval."
    )
    def list_workflows() -> str:
        return json.dumps(
            {"workflows": store.list_workflows(), "drafts": store.list_drafts()}, default=str
        )

    @server.tool(description="Read one workflow file and its manifest.")
    def read_workflow(name: str) -> str:
        try:
            return json.dumps(store.read_workflow(name), default=str)
        except store.StoreError as exc:
            return json.dumps({"error": str(exc)})

    @server.tool(
        description=(
            "Check a workflow file without writing anything. Returns the ordered checks, any "
            "errors and the parsed manifest."
        )
    )
    def validate_workflow(name: str, code: str) -> str:
        try:
            store.check_name(name)
        except store.StoreError as exc:
            return json.dumps({"valid": False, "errors": [str(exc)]})
        return json.dumps(run_checks(name, code), default=str)

    @server.tool(
        description=(
            "Validate a workflow file and save it as a draft. Never touches a live workflow: a "
            "human approves the returned diff before it runs."
        )
    )
    def write_workflow(name: str, code: str, message: str = "") -> str:
        try:
            store.check_name(name)
        except store.StoreError as exc:
            return json.dumps({"written": False, "errors": [str(exc)]})
        report = run_checks(name, code)
        if not report["valid"]:
            return json.dumps({"written": False, **report}, default=str)
        draft = store.write_draft(name, code, message)
        return json.dumps(
            {"written": True, "draft": draft["name"], "diff": draft["diff"], "validation": report},
            default=str,
        )

    @server.tool(description="Delete a live workflow file. Run history stays in Temporal.")
    def delete_workflow(name: str) -> str:
        try:
            return json.dumps(store.delete_workflow(name))
        except store.StoreError as exc:
            return json.dumps({"error": str(exc)})

    @server.tool(
        description=(
            "List recent workflow runs, newest first, with how each one ended. Pass a workflow "
            "name to see only its runs. Use it to find the workflow_id that read_run needs."
        )
    )
    async def list_runs(workflow: str = "", limit: int = 20) -> str:
        try:
            return json.dumps(await backend.get("/internal/runs", workflow=workflow, limit=limit))
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"could not read run history: {exc}"})

    @server.tool(
        description=(
            "Read one run in full: the input it was given, every activity step with its result "
            "or failure, and how the run ended. Read this before changing a workflow that failed."
        )
    )
    async def read_run(workflow_id: str, limit: int = 200) -> str:
        try:
            return json.dumps(await backend.get(f"/internal/runs/{workflow_id}", limit=limit))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return json.dumps({"error": f"no run with id {workflow_id}"})
            return json.dumps({"error": f"could not read run {workflow_id}: {exc}"})
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"could not read run {workflow_id}: {exc}"})


def build_app() -> Any | None:
    """The MCP ASGI app, or None when the MCP layer is unavailable."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.transport_security import TransportSecuritySettings
    except Exception as exc:  # noqa: BLE001 - never let the REST API die with the MCP layer
        log.warning("MCP layer unavailable: %s", exc)
        return None

    server = MCPServer(name="nautionette-workflows", instructions=INSTRUCTIONS)
    _register(server)
    # Stateless JSON over HTTP: agentgateway fronts this, so no sessions, and no
    # host pinning on an internal network.
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
