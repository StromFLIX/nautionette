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
    "every step it took. write_workflow validates and deploys directly, without approval. "
    "Check ready and worker_restart, then use run_workflow and read_run to verify real results. "
    "Repair failures and redeploy as needed. Do not ask for routine permissions or confirmations. "
    "Use the backend MCP tools for system diagnostics and configuration repairs."
)


def _register(server: Any) -> None:
    @server.tool(description="List deployed workflows and any legacy drafts that remain on the volume.")
    def list_workflows() -> str:
        return json.dumps({"workflows": store.list_workflows(), "drafts": store.list_drafts()}, default=str)

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
            "Validate and deploy a complete workflow file immediately, then reload workers. "
            "Returns the diff, validation report, worker_restart and ready. No approval is needed. "
            "When ready is true, use run_workflow to test it and read_run to inspect results."
        )
    )
    async def write_workflow(name: str, code: str, message: str = "") -> str:
        try:
            name = store.check_name(name)
        except store.StoreError as exc:
            return json.dumps({"published": False, "errors": [str(exc)]})
        try:
            result = await backend.post(
                f"/internal/workflows/{name}/deploy", {"code": code, "message": message}
            )
            return json.dumps(result, default=str)
        except httpx.HTTPError as exc:
            return _backend_error(exc)

    @server.tool(
        description=(
            "Start a deployed workflow with input matching its manifest. Returns workflow_id. "
            "Use read_run to inspect progress, failures and the final result; do not assume it succeeded."
        )
    )
    async def run_workflow(name: str, input: dict[str, Any] | None = None) -> str:
        try:
            name = store.check_name(name)
        except store.StoreError as exc:
            return json.dumps({"error": str(exc)})
        try:
            return json.dumps(await backend.post(f"/internal/workflows/{name}/run", {"input": input or {}}))
        except httpx.HTTPError as exc:
            return _backend_error(exc)

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


def _backend_error(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = error.response.json().get("detail", "Backend operation failed")
        except ValueError:
            detail = "Backend operation failed"
        return json.dumps({"ok": False, "status": error.response.status_code, "error": detail}, default=str)
    return json.dumps({"ok": False, "error": "The backend could not be reached"})


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
