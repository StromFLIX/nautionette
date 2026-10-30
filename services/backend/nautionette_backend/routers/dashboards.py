"""Authenticated dashboard discovery, composition, data and workflow execution."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response, StreamingResponse

from .. import dashboard_actions, dashboard_data, dashboards
from ..dashboard_schema import DashboardWrite, Invoke, Manifest, Pin, PublishData, RecipeWrite, Restore
from ..db import db
from ..events import bus
from ..security import require_user

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"], dependencies=[Depends(require_user)])
Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


@router.get("", operation_id="list_dashboards")
async def list_dashboards(
    agent_id: str | None = None,
    all_agents: bool = False,
    archived: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    """Discover dashboards. Omit agent_id for defaults; all_agents lists navigation across agents."""
    return dashboards.listing(agent_id, all_agents=all_agents, archived=archived, offset=offset, limit=limit)


@router.get("/registry", operation_id="dashboard_registry")
async def dashboard_registry(agent_id: str | None = None):
    """Discover component contracts, reusable recipes, and the complete v1 manifest schema.

    Routine edits publish without approval. Use registered components and JSON pointers, never HTML/JS/CSS.
    """
    return dashboards.registry(agent_id)


@router.get("/read-tools", operation_id="dashboard_read_tools")
async def dashboard_read_tools(
    agent_id: str | None = None,
    search: str = Query("", max_length=120),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    """Discover authorized tools explicitly annotated read-only, with their input schemas.

    Undeclared/sensitive tools cannot be auto-refreshed. Use workflow-published snapshot sources instead;
    preserve permission boundaries and never include credentials or full private configuration in card data.
    """
    try:
        tools = [
            tool
            for tool in await dashboards.read_tools(agent_id)
            if search.lower() in (tool["name"] + " " + tool.get("description", "")).lower()
        ]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Read-tool discovery unavailable") from None
    return {
        "tools": tools[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(tools) else None,
    }


@router.put("/components/{component_id}", operation_id="register_dashboard_component")
async def register_dashboard_component(component_id: Slug, body: RecipeWrite):
    """Register a reusable declarative component recipe. expected_revision=0 creates it.

    Registration validates immediately, with no review queue. Existing dashboards reference immutable
    recipe:id@revision, so a new recipe revision cannot break an existing layout.
    """
    return dashboards.register(component_id, body.recipe, body.expected_revision)


@router.post("/validate", operation_id="validate_dashboard")
async def validate_dashboard(body: Manifest):
    """Validate/preview a complete manifest without saving, returning the resolved component composition."""
    return await dashboards.validate(body)


@router.get("/{dashboard_id}", operation_id="get_dashboard")
async def get_dashboard(
    dashboard_id: Slug, revision: int | None = Query(None, ge=1), include_archived: bool = False
):
    """Read an editable manifest/revision, optionally historical or archived for recovery."""
    return dashboards.get(dashboard_id, revision, include_archived=include_archived)


@router.put("/{dashboard_id}", operation_id="put_dashboard")
async def put_dashboard(dashboard_id: Slug, body: DashboardWrite):
    """Create/update and publish a complete agent-owned dashboard immediately, without human approval.

    expected_revision=0 creates; read current revision before editing. The widget array order is mobile order;
    spans use a 12-column desktop grid. Source IDs/actions must be registered in this manifest. Tool sources
    must be authorized and declared read-only; snapshot feeds are published explicitly by tools/workflows.
    Actions run deployed workflows, validate fresh bound inputs/conditions, and track durable run history.
    """
    return await dashboards.save(dashboard_id, body.manifest, body.expected_revision)


@router.get("/{dashboard_id}/history", operation_id="dashboard_history")
async def dashboard_history(
    dashboard_id: Slug, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)
):
    """Inspect revision metadata, then read a revision or restore it as a new revision."""
    dashboards.get(dashboard_id)
    rows = db.query(
        "SELECT revision, created_at FROM dashboard_revisions WHERE dashboard_id = ? "
        "ORDER BY revision DESC LIMIT ? OFFSET ?",
        (dashboard_id, limit + 1, offset),
    )
    return {"revisions": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}


@router.post("/{dashboard_id}/restore", operation_id="restore_dashboard")
async def restore_dashboard(dashboard_id: Slug, body: Restore):
    """Revalidate and restore an earlier manifest as a new revision, without rewriting revision history."""
    old = dashboards.get(dashboard_id, body.revision)
    return await dashboards.save(
        dashboard_id, Manifest.model_validate(old["manifest"]), body.expected_revision
    )


@router.patch("/{dashboard_id}/pin", operation_id="pin_dashboard")
async def pin_dashboard(dashboard_id: Slug, body: Pin):
    """Set sidebar pinning with revision protection. Respect the user's existing navigation choices."""
    current = dashboards.get(dashboard_id)
    return await dashboards.save(
        dashboard_id,
        Manifest.model_validate({**current["manifest"], "pinned": body.pinned}),
        body.expected_revision,
    )


@router.delete("/{dashboard_id}", operation_id="archive_dashboard")
async def archive_dashboard(dashboard_id: Slug, expected_revision: int = Query(..., ge=1)):
    """Archive a dashboard, retaining history. Active/uncertain workflow runs block archiving."""
    dashboard_actions.history(dashboard_id)
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT revision, archived FROM dashboards WHERE id = ?", (dashboard_id,)
        ).fetchone()
        if not row or row["archived"]:
            raise HTTPException(404, "Dashboard not found")
        if row["revision"] != expected_revision:
            raise HTTPException(409, "Dashboard changed; re-read before archiving")
        active = conn.execute(
            "SELECT 1 FROM dashboard_action_runs WHERE dashboard_id = ? "
            "AND state IN ('starting','uncertain','running') LIMIT 1",
            (dashboard_id,),
        ).fetchone()
        if active:
            raise HTTPException(
                409, "Dashboard has an active or uncertain workflow action; inspect its run first"
            )
        conn.execute("UPDATE dashboards SET archived = 1 WHERE id = ?", (dashboard_id,))
    bus.publish("dashboard.changed", {"dashboard_id": dashboard_id, "archived": True})
    return {"archived": True}


@router.post("/{dashboard_id}/unarchive", operation_id="unarchive_dashboard")
async def unarchive_dashboard(dashboard_id: Slug, expected_revision: int = Query(..., ge=1)):
    """Revalidate and recover an archived dashboard as a new revision, without deleting history."""
    current = dashboards.get(dashboard_id, include_archived=True)
    if not current["archived"]:
        raise HTTPException(409, "Dashboard is not archived")
    return await dashboards.save(
        dashboard_id, Manifest.model_validate(current["manifest"]), expected_revision, revive=True
    )


@router.head("/{dashboard_id}/stream", include_in_schema=False)
async def dashboard_stream_probe(dashboard_id: Slug):
    dashboards.get(dashboard_id)
    return Response(status_code=200)


@router.get("/{dashboard_id}/stream", include_in_schema=False)
async def dashboard_stream(dashboard_id: Slug):
    dashboards.get(dashboard_id)
    return StreamingResponse(
        dashboard_data.stream(dashboard_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{dashboard_id}/snapshot", operation_id="dashboard_snapshot")
async def dashboard_snapshot(dashboard_id: Slug, refresh: bool = False):
    """Inspect the actual rendered composition, source freshness/errors, action readiness and durable runs.

    Refresh calls only deterministic read adapters, never an LLM. Forced reads are coalesced/rate-limited.
    Literal data is illustrative/static, not live. Source failures are explicit and preserve last good data.
    """
    return await dashboard_data.snapshot(dashboard_id, refresh)


@router.post("/{dashboard_id}/sources/{source_id}", operation_id="publish_dashboard_data")
async def publish_dashboard_data(dashboard_id: Slug, source_id: Slug, body: PublishData):
    """Publish bounded structured data to a registered snapshot feed, e.g. from a scheduled workflow.

    Match the dashboard revision and output schema. Secrets must stay in integrations, never UI data.
    Publishes an SSE update without an agent loop or chat message.
    """
    current = dashboards.get(dashboard_id)
    if body.expected_revision != current["revision"]:
        raise HTTPException(409, "Dashboard changed; re-read before publishing data")
    return dashboard_data.publish(current, source_id, body.data)


@router.post("/{dashboard_id}/actions/{action_id}", operation_id="run_dashboard_action")
async def run_dashboard_action(dashboard_id: Slug, action_id: Slug, body: Invoke):
    """Run a registered workflow action after fresh server-side input, condition and permission validation.

    Read dashboard_snapshot, review exact inputs/context_hash, and pass explicit confirmation if required.
    Generate a request_id ONCE and reuse that ID and exact request after a timeout/uncertain response;
    never create a new request to retry. One active run per action. Inspect returned status/history: a
    started run is NOT a successful outcome. Workflow-specific release/health policy belongs in the workflow.
    """
    return await dashboard_actions.invoke(dashboard_id, action_id, body)
