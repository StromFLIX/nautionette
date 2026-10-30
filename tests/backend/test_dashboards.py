"""General-purpose dashboards; no production-agent dashboard is seeded."""

import asyncio
import copy
import json
import time

import pytest
from nautionette_backend import dashboard_actions, dashboard_data, dashboards, main, runs
from nautionette_backend.dashboard_schema import Invoke, Manifest

BASE = {
    "title": "Test interface",
    "sources": {"counter": {"kind": "literal", "data": {"count": 7}}},
    "widgets": [
        {
            "id": "total",
            "component": "metric",
            "title": "Count",
            "source": "counter",
            "bindings": {"value": "/count"},
            "span": 4,
        }
    ],
}


def create(client, manifest=None, dashboard_id="test-interface", revision=0):
    return client.put(
        f"/api/dashboards/{dashboard_id}",
        json={
            "expected_revision": revision,
            "manifest": copy.deepcopy(manifest or BASE),
        },
    )


def snapshot(client, dashboard_id="test-interface"):
    response = client.get(f"/api/dashboards/{dashboard_id}/snapshot")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def action_manifest(backend, monkeypatch):
    monkeypatch.setattr(runs, "follow", lambda *_: None)
    backend.authoring.add_workflow(
        "dashboard_test",
        manifest={
            "name": "dashboard_test",
            "inputs": {
                "type": "object",
                "properties": {"commit": {"type": "string"}, "count": {"type": "integer", "minimum": 1}},
                "required": ["commit", "count"],
                "additionalProperties": False,
            },
        },
    )
    return {
        "title": "Workflow interface",
        "sources": {"candidate": {"kind": "snapshot", "stale_seconds": 120}},
        "actions": {
            "check": {
                "label": "Check candidate",
                "workflow": "dashboard_test",
                "bindings": {"commit": {"source": "candidate", "path": "/sha"}},
                "conditions": [{"source": "candidate", "path": "/ready", "equals": True}],
                "input_schema": {
                    "type": "object",
                    "properties": {"count": {"type": "integer", "minimum": 1}},
                    "required": ["count"],
                    "additionalProperties": False,
                },
            }
        },
        "widgets": [{"id": "check", "component": "action", "props": {"action": "check"}}],
    }


def ready(client, manifest):
    assert create(client, manifest).status_code == 200
    response = client.post(
        "/api/dashboards/test-interface/sources/candidate",
        json={
            "expected_revision": 1,
            "data": {"sha": "abc123", "ready": True},
        },
    )
    assert response.status_code == 200, response.text
    view = snapshot(client)
    return {
        "expected_revision": 1,
        "request_id": "request-dashboard-test-001",
        "inputs": {"count": 2},
        "confirmed": True,
        "context_hash": view["actions"]["check"]["context_hash"],
    }


def test_no_seed_dashboard_and_discovery_is_authenticated(client, anonymous):
    assert client.get("/api/dashboards?all_agents=true").json()["dashboards"] == []
    assert anonymous.get("/api/dashboards").status_code == 401
    assert anonymous.get("/api/dashboards/registry").status_code == 401
    registry = client.get("/api/dashboards/registry").json()
    assert {item["component"] for item in registry["components"]} == {
        "metric",
        "status",
        "text",
        "list",
        "table",
        "key-value",
        "action",
        "workflow",
    }
    assert registry["manifest_schema"]["properties"]["schema_version"]["const"] == 1
    assert registry["recipes"] == []


def test_revisioned_create_preview_restore_and_optimistic_conflict(client):
    assert client.post("/api/dashboards/validate", json=BASE).json()["valid"]
    assert client.get("/api/dashboards").json()["dashboards"] == []
    created = create(client).json()
    assert created["revision"] == 1
    view = snapshot(client)
    assert view["widgets"][0]["parts"][0]["props"]["value"] == 7
    assert view["sources"]["counter"]["state"] == "static"
    changed = {**BASE, "title": "Rearranged", "widgets": [{**BASE["widgets"][0], "span": 12}]}
    assert create(client, changed, revision=1).json()["revision"] == 2
    assert create(client, revision=1).status_code == 409
    assert (
        client.get("/api/dashboards/test-interface?revision=1").json()["manifest"]["title"] == BASE["title"]
    )
    restored = client.post(
        "/api/dashboards/test-interface/restore", json={"expected_revision": 2, "revision": 1}
    )
    assert restored.status_code == 200
    assert restored.json()["revision"] == 3
    assert restored.json()["manifest"]["title"] == BASE["title"]
    history = client.get("/api/dashboards/test-interface/history?limit=2").json()
    assert [item["revision"] for item in history["revisions"]] == [3, 2]
    assert history["next_offset"] == 2
    pinned = client.patch(
        "/api/dashboards/test-interface/pin", json={"expected_revision": 3, "pinned": False}
    )
    assert pinned.json()["manifest"]["pinned"] is False
    assert client.get("/api/dashboards").json()["dashboards"][0]["pinned"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"widgets": [{"id": "script", "component": "html", "props": {"html": "<script>bad()</script>"}}]},
        {"widgets": [{"id": "text", "component": "text", "props": {"text": "ok", "style": "bad"}}]},
        {"widgets": [{"id": "bad", "component": "metric", "source": "missing", "bindings": {"value": "/n"}}]},
        {"widgets": [{"id": "bad", "component": "metric", "props": {"value": 1}, "span": 13}]},
        {"sources": {"a": {"kind": "http", "url": "http://private/"}}},
        {"sources": {"a": {"kind": "literal", "output_schema": {"$ref": "https://private/schema"}}}},
        {"sources": {"a": {"kind": "literal", "output_schema": {"type": "nonsense"}}}},
        {"sources": {"a": {"kind": "literal", "data": "wrong", "output_schema": {"type": "integer"}}}},
        {"widgets": [BASE["widgets"][0], BASE["widgets"][0]]},
    ],
)
def test_invalid_changes_never_replace_last_working_revision(client, change):
    assert create(client).status_code == 200
    response = create(client, {**BASE, **change}, revision=1)
    assert response.status_code == 422, response.text
    assert client.get("/api/dashboards/test-interface").json()["revision"] == 1


def test_recipe_registration_is_immediate_immutable_and_agent_scoped(client):
    agent = client.post("/api/agents", json={"name": "Builder"}).json()["id"]
    definition = {
        "agent_id": agent,
        "title": "Health card",
        "parts": [
            {"component": "metric", "bindings": {"value": "/count"}},
            {"component": "text", "props": {"text": "Built from registered primitives"}},
        ],
    }
    first = client.put(
        "/api/dashboards/components/health-card", json={"expected_revision": 0, "recipe": definition}
    )
    assert first.status_code == 200, first.text
    assert first.json()["component"] == "recipe:health-card@1"
    manifest = {
        **BASE,
        "agent_id": agent,
        "widgets": [{"id": "health", "component": "recipe:health-card@1", "source": "counter"}],
    }
    assert create(client, manifest).status_code == 200
    updated = {**definition, "parts": [{"component": "text", "props": {"text": "New version"}}]}
    assert (
        client.put(
            "/api/dashboards/components/health-card", json={"expected_revision": 1, "recipe": updated}
        ).status_code
        == 200
    )
    assert snapshot(client)["widgets"][0]["parts"][0]["component"] == "metric"
    assert len(client.get(f"/api/dashboards/registry?agent_id={agent}").json()["recipes"]) == 2
    assert client.get("/api/dashboards/registry").json()["recipes"] == []
    assert create(client, {**manifest, "agent_id": None}, dashboard_id="wrong-owner").status_code == 403
    assert create(client, {**manifest, "agent_id": None}, revision=1).status_code in {403, 422}


def test_snapshot_validation_redaction_staleness_and_revision_fencing(client, backend):
    manifest = {
        "title": "Snapshots",
        "sources": {
            "feed": {
                "kind": "snapshot",
                "stale_seconds": 60,
                "output_schema": {"type": "object", "required": ["count"]},
            }
        },
        "widgets": [
            {"id": "feed", "component": "metric", "source": "feed", "bindings": {"value": "/count"}},
        ],
    }
    assert create(client, manifest).status_code == 200
    empty = snapshot(client)
    assert empty["sources"]["feed"]["state"] == "empty"
    assert empty["widgets"][0]["error"]
    path = "/api/dashboards/test-interface/sources/feed"
    assert client.post(path, json={"expected_revision": 1, "data": {}}).status_code == 422
    assert client.post(path, json={"expected_revision": 2, "data": {"count": 1}}).status_code == 409
    response = client.post(
        path, json={"expected_revision": 1, "data": {"count": 9, "api_key": "never-render"}}
    )
    assert response.status_code == 200
    view = snapshot(client)
    assert view["sources"]["feed"]["state"] == "fresh"
    assert view["sources"]["feed"]["data"]["api_key"] == "[redacted]"
    assert "never-render" not in backend.db.one("SELECT data FROM dashboard_sources")["data"]
    backend.db.execute("UPDATE dashboard_sources SET observed_at = ?", (time.time() - 70,))
    assert snapshot(client)["sources"]["feed"]["state"] == "stale"
    assert client.post(path, json={"expected_revision": 1, "data": {"count": "x" * 70000}}).status_code == 422


def test_authorized_read_tool_is_cached_and_failure_retains_last_good_data(
    client, backend, live, monkeypatch
):
    tool = {
        "name": "example_read",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": ["limit"],
            "additionalProperties": False,
        },
    }
    backend.gateway.tools[""] = [tool, {"name": "example_mutation", "inputSchema": {}}]
    calls = []

    async def read(name, arguments):
        calls.append((name, arguments))
        return {"payload": {"value": 11, "access_token": "secret"}}

    monkeypatch.setattr(live.gateway, "call_read_tool", read)
    manifest = {
        "title": "Read tools",
        "sources": {
            "read": {"kind": "tool", "tool": "example_read", "arguments": {"limit": 2}, "select": "/payload"}
        },
        "widgets": [
            {"id": "value", "component": "metric", "source": "read", "bindings": {"value": "/value"}}
        ],
    }
    assert [item["name"] for item in client.get("/api/dashboards/read-tools").json()["tools"]] == [
        "example_read"
    ]
    assert create(client, manifest).status_code == 200
    view = snapshot(client)
    assert view["sources"]["read"]["state"] == "fresh"
    assert view["sources"]["read"]["data"]["value"] == 11
    assert len(calls) == 1
    snapshot(client)
    assert len(calls) == 1

    async def fail(*_):
        raise RuntimeError("credential=do-not-expose")

    monkeypatch.setattr(live.gateway, "call_read_tool", fail)
    backend.db.execute("UPDATE dashboard_sources SET attempted_at = 0")
    failed = snapshot(client)
    assert failed["sources"]["read"]["state"] == "error"
    assert failed["sources"]["read"]["data"]["value"] == 11
    assert "do-not-expose" not in json.dumps(failed)
    invalid = copy.deepcopy(manifest)
    invalid["sources"]["read"]["tool"] = "example_mutation"
    assert create(client, invalid, revision=1).status_code == 422
    invalid["sources"]["read"]["tool"] = "example_read"
    invalid["sources"]["read"]["arguments"] = {"limit": "bad"}
    assert create(client, invalid, revision=1).status_code == 422
    monkeypatch.setattr(live.gateway, "call_read_tool", read)
    agent = client.post(
        "/api/agents", json={"name": "Limited", "config": {"tools": ["example_read"]}}
    ).json()["id"]
    assert create(client, {**manifest, "agent_id": agent}, dashboard_id="owned-read").status_code == 200
    assert snapshot(client, "owned-read")["sources"]["read"]["data"]
    client.patch(f"/api/agents/{agent}", json={"config": {"tools": []}})
    denied = snapshot(client, "owned-read")["sources"]["read"]
    assert denied["state"] == "denied" and denied["data"] is None


def test_workflow_result_source_uses_real_run_result(client, backend):
    backend.db.record_run("example", "example-001", "run-1", "manual", {})
    backend.db.update_run("example-001", "completed", {"count": 12})
    manifest = {
        "title": "Result",
        "sources": {"result": {"kind": "workflow-result", "workflow": "example", "select": "/result"}},
        "widgets": [],
    }
    assert create(client, manifest).status_code == 200
    assert snapshot(client)["sources"]["result"]["data"] == {"count": 12}


def test_action_validates_inputs_confirmation_freshness_and_disabled_state(client, backend, action_manifest):
    payload = ready(client, action_manifest)
    path = "/api/dashboards/test-interface/actions/check"
    assert client.post(path, json={**payload, "confirmed": False}).status_code == 422
    assert client.post(path, json={**payload, "inputs": {"count": "bad"}}).status_code == 422
    assert (
        client.post(path, json={**payload, "inputs": {"count": 1, "commit": "tampered"}}).status_code == 422
    )
    assert client.post(path, json={**payload, "expected_revision": 2}).status_code == 409
    assert client.post(path, json={**payload, "context_hash": "old"}).status_code == 409
    backend.db.set_workflow_settings("dashboard_test", {"disabled": True})
    assert client.post(path, json=payload).status_code == 409
    backend.db.set_workflow_settings("dashboard_test", {"disabled": False})
    backend.db.execute("UPDATE dashboard_sources SET observed_at = ?", (time.time() - 200,))
    assert snapshot(client)["actions"]["check"]["enabled"] is False
    assert client.post(path, json=payload).status_code == 409
    assert backend.temporal.started == []


def test_action_exact_binding_stable_retry_and_durable_outcome(client, backend, action_manifest):
    payload = ready(client, action_manifest)
    path = "/api/dashboards/test-interface/actions/check"
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "running"
    assert backend.temporal.started[0]["input"] == {"commit": "abc123", "count": 2}
    assert client.post(path, json=payload).json()["workflow_id"] == run["workflow_id"]
    assert (
        client.post(path, json={**payload, "request_id": "another-dashboard-request"}).json()["workflow_id"]
        == run["workflow_id"]
    )
    assert len(backend.temporal.started) == 1
    assert client.post(path, json={**payload, "inputs": {"count": 3}}).status_code == 409
    assert client.delete("/api/dashboards/test-interface?expected_revision=1").status_code == 409
    backend.db.update_run(run["workflow_id"], "completed", {"verified": True})
    assert snapshot(client)["runs"][0]["status"] == "completed"
    assert client.post(path, json=payload).json()["result"] == {"verified": True}
    assert len(backend.temporal.started) == 1
    assert client.delete("/api/dashboards/test-interface?expected_revision=1").status_code == 200
    assert client.get("/api/dashboards/test-interface").status_code == 404
    assert backend.db.one("SELECT COUNT(*) AS n FROM dashboard_revisions")["n"] == 1


def test_changed_source_rejects_reviewed_action(client, backend, action_manifest):
    payload = ready(client, action_manifest)
    client.post(
        "/api/dashboards/test-interface/sources/candidate",
        json={"expected_revision": 1, "data": {"sha": "new-commit", "ready": True}},
    )
    assert client.post("/api/dashboards/test-interface/actions/check", json=payload).status_code == 409
    assert backend.temporal.started == []


def test_lost_start_response_retries_same_temporal_id_without_a_second_execution(
    client, backend, live, action_manifest, monkeypatch
):
    payload = ready(client, action_manifest)
    path = "/api/dashboards/test-interface/actions/check"
    original = backend.temporal.start_once

    async def lost_response(*args, **kwargs):
        await original(*args, **kwargs)
        raise TimeoutError("response was lost")

    monkeypatch.setattr(live.temporal, "start_once", lost_response)
    response = client.post(path, json=payload)
    assert response.status_code == 503
    assert snapshot(client)["runs"][0]["status"] == "uncertain"
    assert len(backend.temporal.started) == 1
    monkeypatch.setattr(live.temporal, "start_once", original)
    repaired = client.post(path, json=payload)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["workflow_id"] == response.json()["detail"]["workflow_id"]
    assert len(backend.temporal.started) == 1


async def test_concurrent_revisions_and_action_clicks(backend, action_manifest):
    result = await dashboards.save("concurrent", Manifest.model_validate(action_manifest), 0)
    dashboard_data.publish(result, "candidate", {"sha": "exact", "ready": True})
    view = await dashboard_data.snapshot("concurrent")
    payload = Invoke(
        expected_revision=1,
        request_id="concurrent-request-001",
        confirmed=True,
        inputs={"count": 1},
        context_hash=view["actions"]["check"]["context_hash"],
    )
    first, second = await asyncio.gather(
        dashboard_actions.invoke("concurrent", "check", payload),
        dashboard_actions.invoke("concurrent", "check", payload),
    )
    assert first["workflow_id"] == second["workflow_id"]
    assert len(backend.temporal.started) == 1
    results = await asyncio.gather(
        dashboards.save("concurrent", Manifest.model_validate(action_manifest), 1),
        dashboards.save("concurrent", Manifest.model_validate(action_manifest), 1),
        return_exceptions=True,
    )
    assert sum(isinstance(item, dict) for item in results) == 1
    assert next(item for item in results if not isinstance(item, dict)).status_code == 409


async def test_dashboard_mcp_tools_execute_without_review_and_streams_are_not_tools(backend):
    tools = await main.backend_mcp.list_tools(None, None)
    names = {tool.name for tool in tools.tools}
    assert {
        "list_dashboards",
        "dashboard_registry",
        "put_dashboard",
        "get_dashboard",
        "validate_dashboard",
        "dashboard_snapshot",
        "register_dashboard_component",
        "publish_dashboard_data",
        "run_dashboard_action",
        "restore_dashboard",
    } <= names
    assert "dashboard_stream" not in names and "dashboard_stream_probe" not in names
    result = await main.backend_mcp.call(
        "put_dashboard", {"dashboard_id": "from-agent", "body": {"expected_revision": 0, "manifest": BASE}}
    )
    assert not result.is_error
    listing = await main.backend_mcp.call("list_dashboards", {})
    assert json.loads(listing.content[0].text)["dashboards"][0]["id"] == "from-agent"
    assert "from-agent" in dashboards.awareness(None)
    assert "owner_agent_id" in dashboards.awareness(None)


async def test_stream_yields_snapshot_and_cleans_up_subscription(backend):
    await dashboards.save("streamed", Manifest.model_validate(BASE), 0)
    subscribers = len(dashboard_data.bus._subscribers)
    stream = dashboard_data.stream("streamed")
    first = await anext(stream)
    assert '"type": "snapshot"' in first
    assert len(dashboard_data.bus._subscribers) == subscribers + 1
    await stream.aclose()
    assert len(dashboard_data.bus._subscribers) == subscribers


def test_resumed_chat_receives_fresh_owner_dashboard_awareness(client, backend):
    agent = client.post("/api/agents", json={"name": "Builder"}).json()["id"]
    chat = client.post("/api/chats", json={"agent_id": agent}).json()
    client.post(f"/api/chats/{chat['id']}/messages", json={"text": "First turn"})
    assert '"dashboards":[]' in backend.broker.jobs[-1]["system_prompt"]
    assert create(client, {**BASE, "agent_id": agent}, dashboard_id="my-interface").status_code == 200
    assert create(client, dashboard_id="other-agent-interface").status_code == 200
    client.post(f"/api/chats/{chat['id']}/messages", json={"text": "Resume"})
    prompt = backend.broker.jobs[-1]["system_prompt"]
    assert "my-interface" in prompt and "other-agent-interface" not in prompt
    assert agent in prompt and '"revision":1' in prompt


def test_archived_dashboard_can_be_discovered_and_recovered_without_history_loss(client):
    assert create(client).status_code == 200
    assert client.delete("/api/dashboards/test-interface?expected_revision=1").status_code == 200
    assert client.get("/api/dashboards").json()["dashboards"] == []
    archived = client.get("/api/dashboards?archived=true").json()["dashboards"]
    assert archived[0]["id"] == "test-interface" and archived[0]["archived"]
    assert client.get("/api/dashboards/test-interface?include_archived=true").json()["archived"]
    assert client.post("/api/dashboards/test-interface/unarchive?expected_revision=2").status_code == 409
    recovered = client.post("/api/dashboards/test-interface/unarchive?expected_revision=1")
    assert recovered.status_code == 200 and recovered.json()["revision"] == 2
    assert recovered.json()["archived"] is False
    assert len(client.get("/api/dashboards/test-interface/history").json()["revisions"]) == 2


def test_deduplicated_request_alias_is_durable_after_original_run_completes(client, backend, action_manifest):
    payload = ready(client, action_manifest)
    path = "/api/dashboards/test-interface/actions/check"
    original = client.post(path, json=payload).json()
    alias = {**payload, "request_id": "deduplicated-alias-001"}
    assert client.post(path, json=alias).json()["workflow_id"] == original["workflow_id"]
    backend.db.update_run(original["workflow_id"], "completed", {"verified": True})
    # The alias response could have been lost. The SAME alias still resolves to its completed run.
    assert client.post(path, json=alias).json()["workflow_id"] == original["workflow_id"]
    assert len(backend.temporal.started) == 1
    assert client.post(path, json={**alias, "inputs": {"count": 99}}).status_code == 409


async def test_mcp_definitions_are_reachable_only(backend):
    tools = (await main.backend_mcp.list_tools(None, None)).tools
    system = next(tool for tool in tools if tool.name == "system_status")
    assert system.input_schema["$defs"] == {}
    dashboard = next(tool for tool in tools if tool.name == "put_dashboard")
    assert {"DashboardWrite", "Manifest", "Widget", "Source", "Action"} <= dashboard.input_schema[
        "$defs"
    ].keys()
    assert "RecipeWrite" not in dashboard.input_schema["$defs"]
    assert len(json.dumps([tool.input_schema for tool in tools])) < 100000


def test_recipe_rejects_credentials_and_deep_data_before_publication(client):
    body = {
        "expected_revision": 0,
        "recipe": {
            "title": "Unsafe",
            "parts": [
                {
                    "component": "key-value",
                    "props": {"items": {"password": "never-persist-this"}},
                }
            ],
        },
    }
    assert client.put("/api/dashboards/components/unsafe", json=body).status_code == 422
    nested = {}
    for _ in range(20):
        nested = {"nested": nested}
    body["recipe"]["parts"][0]["props"]["items"] = nested
    assert client.put("/api/dashboards/components/unsafe", json=body).status_code == 422
    assert client.get("/api/dashboards/registry").json()["recipes"] == []


def test_workflow_feed_without_runs_is_empty_not_a_failed_integration(client):
    manifest = {**BASE, "sources": {"counter": {"kind": "workflow-result", "workflow": "test"}}}
    assert create(client, manifest).status_code == 200
    source = client.get("/api/dashboards/test-interface/snapshot").json()["sources"]["counter"]
    assert source["state"] == "empty" and source["data"] is None
    assert source["error"] == "Waiting for source data"


def test_workflow_schema_change_is_rechecked_before_invocation(
    client, backend, live, action_manifest, monkeypatch
):
    payload = ready(client, action_manifest)
    original = live.authoring.get_workflow

    async def changed(name):
        definition = await original(name)
        return {
            **definition,
            "manifest": {**definition["manifest"], "inputs": {"$ref": "https://invalid/schema"}},
        }

    monkeypatch.setattr(live.authoring, "get_workflow", changed)
    response = client.post("/api/dashboards/test-interface/actions/check", json=payload)
    assert response.status_code == 422
    assert "schema" in response.json()["detail"]
    assert backend.temporal.started == []
