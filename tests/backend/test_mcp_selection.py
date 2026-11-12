"""Selection scopes startup only; credentials, authorization and ownership are unchanged."""

import json
from copy import deepcopy

import httpx
import pytest
from nautionette_backend import mcp_selection as scope
from nautionette_backend import mcp_servers
from nautionette_backend.clients.agentgateway import GatewayClient


@pytest.mark.parametrize(
    "condition",
    [None, "false", 'request.headers["x-account"] == "one"', '"x-nautionette-mcp-targets" == "literal"'],
)
def test_scope_decoration_is_lossless_idempotent_and_preserves_external_denials(condition):
    value = {
        "name": "external",
        "stdio": {"cmd": "node", "env": {"TOKEN": "local-test-only"}},
        "policies": {"arbitrary": {"keep": True}},
    }
    if condition is not None:
        value["condition"] = condition
    original = deepcopy(value)
    scoped = scope.scoped_target(value)
    assert value == original
    assert scope.logical_target(scoped) == original
    assert scope.scoped_target(scoped) == scoped
    if condition is not None:
        assert scoped["condition"] == f"({condition}) && ({scope.CONDITION})"


def test_headers_keep_empty_distinct_from_unrestricted_and_reject_injection():
    assert scope.target_headers(None) == {}
    assert scope.target_headers([]) == {scope.HEADER: ""}
    assert scope.target_headers(["github", "backend", "github"]) == {scope.HEADER: "backend,github"}
    for value in [
        False,
        {},
        "github",
        ["github,backend"],
        ["bad\r\nheader"],
        [None],
        [""],
        ["x" * 129],
        [str(i) * 100 for i in range(100)],
    ]:
        with pytest.raises(ValueError):
            scope.target_headers(value)


def test_only_known_owners_are_scoped_unknown_and_singleton_names_fall_back():
    targets = [{"name": "backend"}, {"name": "github"}, {"name": "brave-search"}]
    local, offline = {"call_subagent"}, {"workiq_ask": "workiq"}
    assert scope.targets_for_tools(
        ["github_read", "call_subagent", "workiq_ask"], targets, local, offline
    ) == ["github", "workiq"]
    assert scope.targets_for_tools([], targets, local, offline) == []
    assert scope.targets_for_tools(["unknown_read"], targets, local, offline) is None
    assert scope.targets_for_tools(["raw_read"], [{"name": "single"}], local, offline) is None


async def test_job_scope_is_execution_time_and_config_outage_keeps_exact_selection(
    backend, live, monkeypatch
):
    backend.gateway.resources["mcp.target"] = {
        name: {"name": name, "mcp": {"host": f"https://{name}.test/mcp"}} for name in ["backend", "github"]
    }
    assert await scope.for_run(None) is None
    assert await scope.for_run([]) == []
    assert await scope.for_run(["github_read", "list_subagents"]) == ["github"]

    async def unavailable():
        raise httpx.ConnectError("private error omitted")

    monkeypatch.setattr(live.gateway, "config", unavailable)
    assert await scope.for_run(["github_read"]) is None
    assert await scope.for_run([]) == []


async def test_known_offline_owner_stays_in_scope_for_fresh_dispatch_after_login(backend):
    backend.gateway.file_targets = [{"name": "workflows", "host": "http://workflows.test/mcp"}]
    backend.gateway.resources["mcp.target"] = {
        "backend": {"name": "backend", "mcp": {"host": "http://backend.test/mcp"}}
    }
    backend.db.execute(
        "INSERT INTO mcp_http_oauth VALUES (?,?,?,?,?,?,?)",
        ("workiq", "{}", "", "reauth_required", 0, 0, "offline"),
    )
    backend.db.execute(
        "INSERT INTO mcp_oauth_tools VALUES (?,?,?)", ("workiq_ask", "workiq", '{"name":"workiq_ask"}')
    )
    assert await scope.for_run(["workiq_ask"]) == ["workiq"]


async def test_reconciliation_preserves_secrets_conditions_and_logical_oauth_snapshots(backend):
    backend.gateway.file_targets = [{"name": "workflows", "host": "http://workflow.test/mcp"}]
    original = {
        "name": "external",
        "mcp": {"host": "https://external.test/mcp"},
        "condition": "false",
        "policies": {"backendAuth": {"key": {"value": "local-test-only"}}},
    }
    backend.gateway.resources["mcp.target"] = {"external": deepcopy(original)}
    await mcp_servers.reconcile_selection()
    saved = backend.gateway.resources["mcp.target"]["external"]
    assert saved == scope.scoped_target(original)
    _, resources = await mcp_servers.fetch_resources()
    assert resources[0]["value"] == original
    writes = len(backend.gateway.writes)
    await mcp_servers.reconcile_selection()
    assert len(backend.gateway.writes) == writes


async def test_reconciliation_reads_latest_resource_not_a_stale_credential_snapshot(
    backend, live, monkeypatch
):
    backend.gateway.file_targets = [{"name": "workflows", "host": "http://workflow.test/mcp"}]
    backend.gateway.resources["mcp.target"] = {
        "external": {"name": "external", "mcp": {"host": "https://external.test/mcp"}}
    }
    original = live.gateway.config_resources
    reads = 0

    async def changed(kind):
        nonlocal reads
        reads += 1
        if reads == 2:
            backend.gateway.resources[kind]["external"]["condition"] = "false"
        return await original(kind)

    monkeypatch.setattr(live.gateway, "config_resources", changed)
    await mcp_servers.reconcile_selection()
    assert backend.gateway.resources["mcp.target"]["external"]["condition"].startswith("(false) && ")


async def test_reconciliation_does_not_duplicate_externally_identified_resources(backend):
    backend.gateway.file_targets = [{"name": "workflows", "host": "http://workflow.test/mcp"}]
    backend.gateway.resources["mcp.target"] = {
        "external-resource-id": {"name": "external", "mcp": {"host": "https://external.test/mcp"}}
    }
    await mcp_servers.reconcile_selection()
    assert list(backend.gateway.resources["mcp.target"]) == ["external-resource-id"]
    assert backend.gateway.writes == []


async def test_singleton_publication_remains_raw_and_multitarget_publication_is_scoped(http):
    base = "http://scope.test"
    targets = []
    writes = []
    http[base + "/api/config/effective"] = lambda _: httpx.Response(200, json={"mcp": {"targets": targets}})

    def write(request):
        writes.append(json.loads(request.content)["resources"][0]["value"])
        return httpx.Response(200, json={"resources": []})

    http[base + "/api/config/resources/mcp.target"] = write
    gateway = GatewayClient(base)
    target = {"name": "example", "mcp": {"host": "https://example.test/mcp"}}
    await gateway.put_config_resources("mcp.target", [target])
    assert writes[-1] == target
    targets.append({"name": "workflows", "mcp": {"host": "http://workflows.test/mcp"}})
    await gateway.put_config_resources("mcp.target", [target])
    assert writes[-1] == scope.scoped_target(target)


async def test_deletion_removes_our_condition_before_gateway_becomes_singleton(http):
    base = "http://scope.test"
    remaining = scope.scoped_target({"name": "remaining", "mcp": {"host": "https://remaining.test/mcp"}})
    deleting = {"name": "deleting", "mcp": {"host": "https://deleting.test/mcp"}}
    actions = []
    http[base + "/api/config/effective"] = lambda _: httpx.Response(
        200, json={"mcp": {"targets": [remaining, deleting]}}
    )

    def resources(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "resources": [
                        {"id": "remaining", "value": remaining},
                        {"id": "deleting", "value": deleting},
                    ]
                },
            )
        if request.method == "PUT":
            value = json.loads(request.content)["resources"][0]["value"]
            actions.append(("rewrite", value))
            return httpx.Response(200, json={"resources": []})
        actions.append(("delete", request.url.path))
        return httpx.Response(204)

    http[base + "/api/config/resources/mcp.target"] = resources
    await GatewayClient(base).delete_config_resource("mcp.target", "deleting")
    assert actions == [
        ("rewrite", scope.logical_target(remaining)),
        ("delete", "/api/config/resources/mcp.target/deleting"),
    ]
