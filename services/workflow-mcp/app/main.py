"""workflow-mcp: the narrow door an agent writes code through.

Two faces on the same store:
* MCP at /mcp   - what agents call, through agentgateway.
* REST at /api  - what the backend calls, so approval stays a human action.

A write never lands on a live workflow. It lands in `.drafts`, with a diff.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import store
from .validate import run_checks

logging.basicConfig(level=logging.INFO, format="%(asctime)s workflow-mcp %(levelname)s %(message)s")
log = logging.getLogger("workflow-mcp")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8080").rstrip("/")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "").strip()

server: Any = None
mcp_app: Any = None

try:  # MCP is how agents reach these tools; the REST side works without it.
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    server = MCPServer(
        name="nautionette-workflows",
        instructions=(
            "Author Nautionette workflows. Read before you write, validate before you write. "
            "When a workflow misbehaves, read its runs first: list_runs finds it, read_run shows "
            "every step it took. Every write lands as a draft with a diff that a human approves; "
            "you never deploy anything yourself."
        ),
    )
except Exception as exc:  # noqa: BLE001 - never let the REST API die with the MCP layer
    log.warning("MCP layer unavailable: %s", exc)


def _error(detail: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}


async def backend_get(path: str, **params: Any) -> dict[str, Any]:
    """Run history lives with the backend, which is the only service that holds Temporal."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            f"{BACKEND_URL}{path}",
            headers=_internal_headers(),
            params={key: value for key, value in params.items() if value not in (None, "")},
        )
        response.raise_for_status()
        return response.json()


async def notify_backend_restart(reason: str) -> dict[str, Any]:
    """A published workflow is useless until a worker has it loaded."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{BACKEND_URL}/internal/worker/restart",
                headers=_internal_headers(),
                json={"reason": reason},
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001 - surface it, do not fail the publish
        log.warning("worker restart request failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


# ------------------------------------------------------------------ MCP tools

if server is not None:

    @server.tool(description="List every workflow with its manifest, plus the drafts waiting for approval.")
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
            return json.dumps(await backend_get("/internal/runs", workflow=workflow, limit=limit))
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
            return json.dumps(await backend_get(f"/internal/runs/{workflow_id}", limit=limit))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return json.dumps({"error": f"no run with id {workflow_id}"})
            return json.dumps({"error": f"could not read run {workflow_id}: {exc}"})
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"could not read run {workflow_id}: {exc}"})

    # Stateless JSON over HTTP: agentgateway fronts this, so no sessions, and no
    # host pinning on an internal network.
    mcp_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


# ----------------------------------------------------------------------- REST


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.ensure_dirs()
    if mcp_app is not None:
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
    else:
        yield


app = FastAPI(
    title="nautionette workflow-mcp",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "mcp": mcp_app is not None,
        "workflows": len(store.list_workflows()),
        "drafts": len(store.list_drafts()),
        "workflows_dir": str(store.WORKFLOWS_DIR),
    }


@app.get("/api/schema")
async def api_schema() -> dict[str, Any]:
    from nautionette.manifest import MANIFEST_SCHEMA, SCHEMA_VERSION

    return {"schema_version": SCHEMA_VERSION, "manifest_schema": MANIFEST_SCHEMA}


@app.get("/api/workflows")
async def api_list_workflows() -> dict[str, Any]:
    return {"workflows": store.list_workflows()}


@app.get("/api/workflows/{name}")
async def api_read_workflow(name: str) -> dict[str, Any]:
    try:
        return store.read_workflow(name)
    except store.StoreError as exc:
        raise _error(str(exc), 404) from exc


@app.delete("/api/workflows/{name}")
async def api_delete_workflow(name: str) -> dict[str, Any]:
    try:
        result = store.delete_workflow(name)
    except store.StoreError as exc:
        raise _error(str(exc), 404) from exc
    result["worker_restart"] = await notify_backend_restart(f"deleted {name}")
    return result


@app.post("/api/validate")
async def api_validate(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = payload.get("name", "")
    try:
        store.check_name(name)
    except store.StoreError as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": [], "manifest": None, "steps": []}
    return run_checks(name, payload.get("code", ""))


@app.get("/api/drafts")
async def api_list_drafts() -> dict[str, Any]:
    return {"drafts": store.list_drafts()}


@app.get("/api/drafts/{name}")
async def api_read_draft(name: str) -> dict[str, Any]:
    try:
        return store.read_draft(name)
    except store.StoreError as exc:
        raise _error(str(exc), 404) from exc


@app.post("/api/drafts")
async def api_write_draft(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    name, code = payload.get("name", ""), payload.get("code", "")
    try:
        store.check_name(name)
    except store.StoreError as exc:
        raise _error(str(exc)) from exc
    report = run_checks(name, code)
    draft = store.write_draft(name, code, payload.get("message", ""))
    draft["validation"] = report
    return JSONResponse(draft)


@app.post("/api/drafts/{name}/publish")
async def api_publish(name: str) -> dict[str, Any]:
    try:
        draft = store.read_draft(name)
    except store.StoreError as exc:
        raise _error(str(exc), 404) from exc
    report = run_checks(name, draft["code"])
    if not report["valid"]:
        raise _error(f"draft does not validate: {'; '.join(report['errors'])}")
    result = store.publish_draft(name)
    result["validation"] = report
    return result


@app.delete("/api/drafts/{name}")
async def api_discard(name: str) -> dict[str, Any]:
    return store.discard_draft(name)


if mcp_app is not None:
    # Mounted last so the REST routes above win; MCP itself answers on /mcp.
    app.mount("/", mcp_app)
