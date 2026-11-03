"""Authoring, authentication, revision recovery and app capability boundaries."""

import asyncio
import copy
import time
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from nautionette_backend import miniapp_capabilities as caps
from nautionette_backend import miniapps
from nautionette_backend.miniapp_models import HTTPGrant, MCPGrant


def publication(client, backend=False):
    sample = client.get("/api/miniapps/contract").json()["example"]
    if not backend:
        sample.pop("backend")
    return {"expected_revision": 0, "manifest": sample}


def rid():
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"


@pytest.fixture
def runtime(monkeypatch, live):
    state = {"revision": None, "count": 0}

    async def call(app_id, action="", payload=None):
        if action == "deploy":
            state["revision"] = payload["revision"]
        elif action == "request":
            if payload["request"]["method"] != "GET":
                state["count"] += 1
            return {"value": state["count"]}
        return copy.deepcopy(state)

    method = AsyncMock(side_effect=call)
    monkeypatch.setattr(live.broker, "miniapp", method)
    return method, state


def test_contract_and_authentication(client, anonymous):
    assert anonymous.get("/api/miniapps").status_code == 401
    assert anonymous.put("/api/miniapps/test", json={}).status_code == 401
    contract = client.get("/api/miniapps/contract").json()
    assert "manifest_schema" in contract
    tools = client.app.openapi()
    assert "/api/miniapps/{app_id}/request" not in tools["paths"]
    assert "/internal/miniapps/{app_id}/capability" not in tools["paths"]
    assert "/api/miniapps/{app_id}" in tools["paths"]


def test_frontend_publish_revisions_retention_and_stale_write(client):
    body = publication(client)
    for number in range(1, 8):
        body["expected_revision"] = number - 1
        reply = client.put("/api/miniapps/counter", json=body)
        assert reply.status_code == 200, reply.text
        assert reply.json()["active_revision"] == number
    result = reply.json()
    assert [r["revision"] for r in result["releases"]] == [7, 6, 5, 4, 3]
    assert client.put("/api/miniapps/counter", json=body).status_code == 409
    headers = client.get("/api/miniapps/counter").headers
    assert headers["content-type"].startswith("application/json")
    assert headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in headers["content-security-policy"]
    assert client.get("/api/miniapps/counter/assets/index.html").status_code == 404


def test_builder_activation_restore_archiving_and_cleanup(client, runtime):
    method, state = runtime
    body = publication(client, True)
    assert client.put("/api/miniapps/counter", json=body).status_code == 200
    assert method.call_args.args[1] == "deploy"
    body["expected_revision"] = 1
    body["manifest"]["title"] = "New title"
    assert client.put("/api/miniapps/counter", json=body).status_code == 200
    restored = client.post("/api/miniapps/counter/restore", json={"expected_revision": 2, "revision": 1})
    assert restored.status_code == 200, restored.text
    assert restored.json()["active_revision"] == state["revision"] == 3
    assert restored.json()["title"] == "Persistent counter"
    assert client.delete("/api/miniapps/counter?expected_revision=3").status_code == 409
    assert (
        client.patch("/api/miniapps/counter", json={"expected_revision": 3, "archived": True}).status_code
        == 200
    )
    assert method.call_args.args[2] == {"action": "suspend"}
    assert (
        client.post("/api/miniapps/counter/request", json={"revision": 3, "path": "/state"}).status_code
        == 409
    )
    assert client.delete("/api/miniapps/counter?expected_revision=3").status_code == 200
    assert client.get("/api/miniapps/counter").status_code == 404


def test_uncertain_publication_reconciles_without_redeploy(client, runtime, monkeypatch):
    method, state = runtime

    async def lost(app_id, action="", payload=None):
        if action == "deploy":
            state["revision"] = payload["revision"]
            raise TimeoutError("reply lost")
        return state

    method.side_effect = lost
    reply = client.put("/api/miniapps/counter", json=publication(client, True))
    assert reply.status_code == 503
    assert client.get("/api/miniapps/counter").json()["active_revision"] is None
    asyncio.run(miniapps.reconcile())
    assert client.get("/api/miniapps/counter").json()["active_revision"] == 1
    assert len([c for c in method.call_args_list if c.args[1:2] == ("deploy",)]) == 1


def test_failed_publication_retains_previous_and_fences_lifecycle(client, runtime, db):
    method, state = runtime
    body = publication(client, True)
    client.put("/api/miniapps/counter", json=body)

    async def failed(app_id, action="", payload=None):
        if action == "deploy":
            raise RuntimeError("health failed")
        return state

    method.side_effect = failed
    body["expected_revision"] = 1
    assert client.put("/api/miniapps/counter", json=body).status_code == 503
    assert client.get("/api/miniapps/counter").json()["active_revision"] == 1
    assert (
        client.patch("/api/miniapps/counter", json={"expected_revision": 2, "archived": True}).status_code
        == 409
    )
    body["expected_revision"] = 2
    assert client.put("/api/miniapps/counter", json=body).status_code == 409
    db.execute("UPDATE mini_app_releases SET created_at=0 WHERE revision=2")
    asyncio.run(miniapps.reconcile())
    result = client.get("/api/miniapps/counter").json()
    assert result["active_revision"] == 1
    assert result["releases"][0]["state"] == "failed"


def test_rpc_mutation_replay_and_unknown_outcome(client, runtime):
    method, state = runtime
    client.put("/api/miniapps/counter", json=publication(client, True))
    payload = {"revision": 1, "method": "POST", "path": "/increment", "request_id": rid()}
    assert client.post("/api/miniapps/counter/request", json=payload).json() == {"value": 1}
    assert client.post("/api/miniapps/counter/request", json=payload).json() == {"value": 1}
    assert state["count"] == 1
    assert client.post("/api/miniapps/counter/request", json={**payload, "path": "/other"}).status_code == 409

    async def lost(*args):
        state["count"] += 1
        raise TimeoutError("response lost after mutation")

    method.side_effect = lost
    payload["request_id"] = rid()
    assert client.post("/api/miniapps/counter/request", json=payload).status_code == 503
    assert client.post("/api/miniapps/counter/request", json=payload).status_code == 409
    assert state["count"] == 2


@pytest.mark.parametrize("bad", ["", "old-id", f"0000000000000-{uuid.uuid4().hex}"])
def test_mutations_require_fresh_ids(client, runtime, bad):
    client.put("/api/miniapps/counter", json=publication(client, True))
    assert (
        client.post(
            "/api/miniapps/counter/request",
            json={"revision": 1, "method": "POST", "path": "/increment", "request_id": bad},
        ).status_code
        == 422
    )


def test_scope_revision_path_and_payload_validation(client, runtime):
    body = publication(client, True)
    client.put("/api/miniapps/counter", json=body)
    for path in ["/../secret", "https://evil.test", "//evil?x", "/state?x=1"]:
        assert (
            client.post("/api/miniapps/counter/request", json={"revision": 1, "path": path}).status_code
            == 422
        )
    assert (
        client.post("/api/miniapps/counter/request", json={"revision": 2, "path": "/state"}).status_code
        == 409
    )
    assert (
        client.put(
            "/api/miniapps/other", json={**body, "manifest": {**body["manifest"], "dockerfile": "bad"}}
        ).status_code
        == 422
    )
    assert client.put("/api/miniapps/UPPER", json=body).status_code == 422
    body["manifest"]["agent_id"] = "unknown"
    assert client.put("/api/miniapps/unknown", json=body).status_code == 422


@pytest.mark.parametrize(
    "tool",
    [
        "backend_publish_miniapp",
        "backend/get_settings",
        "backend-get-settings",
        "workflows_run_workflow",
        "coolify_get_env_values",
        "coolify_manage_private_key",
        "coolify_cloud_list_tokens",
    ],
)
def test_host_and_credential_tools_denied(tool):
    with pytest.raises(ValueError):
        MCPGrant(tool=tool)


@pytest.mark.parametrize(
    "tool,key",
    [
        ("workflows_run_workflow", "name"),
        ("workflows_list_runs", "workflow"),
    ],
)
def test_workflow_grants_require_fixed_names(tool, key):
    for value in [None, "", "*", "ab", "../release", "release/other", True, {"name": "release"}]:
        with pytest.raises(ValueError):
            MCPGrant(tool=tool, arguments={key: value})
    grant = MCPGrant(tool=tool, arguments={key: "prepare_nautionette_release"})
    manifest = {"backend": {"permissions": {"mcp": [grant.model_dump()]}}}
    request = {"kind": "mcp", "tool": tool, "arguments": {key: "prepare_nautionette_release"}}
    caps.authorize(manifest, request)
    for args in [{}, {key: "another_workflow"}, {key: ""}, {key: True}]:
        with pytest.raises(HTTPException) as error:
            caps.authorize(manifest, {**request, "arguments": args})
        assert error.value.status_code == 403
    for other in [
        "workflows_read_run",
        "workflows_write_workflow",
        "backend_run_workflow",
        "workflows/run_workflow",
        "workflows-run-workflow",
        "WORKFLOWS_RUN_WORKFLOW",
    ]:
        with pytest.raises(ValueError):
            MCPGrant(tool=other, arguments={key: "prepare_nautionette_release"})


def test_workflow_start_is_deduplicated_and_unknown_outcomes_are_fenced(
    client, internal_headers, runtime, monkeypatch
):
    body = publication(client, True)
    body["manifest"]["backend"]["permissions"] = {
        "mcp": [
            {
                "tool": "workflows_run_workflow",
                "arguments": {"name": "release_test"},
            }
        ]
    }
    assert client.put("/api/miniapps/counter", json=body).status_code == 200
    monkeypatch.setattr(caps, "describe", AsyncMock(return_value={"annotations": {"readOnlyHint": False}}))
    invoke = AsyncMock(
        return_value={"content": [{"type": "text", "text": '{"workflow_id":"release-test-run"}'}]}
    )
    monkeypatch.setattr(caps, "mcp", invoke)
    payload = {
        "revision": 1,
        "request": {
            "kind": "mcp",
            "tool": "workflows_run_workflow",
            "arguments": {"name": "release_test", "input": {"version": "minor"}},
            "request_id": rid(),
        },
    }
    path = "/internal/miniapps/counter/capability"
    for _ in range(2):
        assert client.post(path, json=payload, headers=internal_headers).status_code == 200
    assert invoke.await_count == 1
    assert invoke.call_args.args[1]["arguments"]["input"] == {"version": "minor"}
    payload["request"]["arguments"]["name"] = "other_workflow"
    assert client.post(path, json=payload, headers=internal_headers).status_code == 403
    assert invoke.await_count == 1
    payload["request"]["arguments"]["name"] = "release_test"
    payload["request"]["request_id"] = rid()
    invoke.side_effect = HTTPException(502, "Gateway reply lost")
    assert client.post(path, json=payload, headers=internal_headers).status_code == 502
    assert client.post(path, json=payload, headers=internal_headers).status_code == 409
    assert invoke.await_count == 2


def test_argument_constraints_are_recursive_and_type_exact():
    manifest = {
        "backend": {
            "permissions": {
                "mcp": [
                    {
                        "tool": "github_list_commits",
                        "arguments": {
                            "owner": "StromFLIX",
                            "repo": "nautionette",
                            "nested": {"enabled": True},
                        },
                    }
                ]
            }
        }
    }
    request = {
        "kind": "mcp",
        "tool": "github_list_commits",
        "arguments": {"owner": "StromFLIX", "repo": "nautionette", "nested": {"enabled": True}, "perPage": 1},
    }
    caps.authorize(manifest, request)
    for bad in [{"owner": "other"}, {"nested": {"enabled": 1}}, {"repo": None}]:
        with pytest.raises(HTTPException) as error:
            caps.authorize(manifest, {**request, "arguments": {**request["arguments"], **bad}})
        assert error.value.status_code == 403


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "169.254.169.254", "10.0.0.1", "100.64.0.1", "::1", "ff02::1", "0.0.0.0"],  # noqa: S104
)
def test_ssrf_private_special_and_mixed_dns_denied(monkeypatch, address):
    monkeypatch.setattr(
        caps.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", (address, 443)), (2, 1, 6, "", ("8.8.8.8", 443))],
    )
    with pytest.raises(HTTPException):
        caps.public_addresses("attacker.test")


def test_https_path_boundaries_and_no_encoded_traversal():
    manifest = {
        "backend": {
            "permissions": {
                "http": [
                    HTTPGrant(origin="https://api.example.com", path_prefix="/repos/allowed").model_dump()
                ]
            }
        }
    }
    request = {
        "kind": "http",
        "url": "https://api.example.com/repos/allowed/commits?per_page=1",
        "method": "GET",
    }
    caps.authorize(manifest, request)
    for path in [
        "/repos/allowed-other",
        "/repos/allowed/../secret",
        "/repos/allowed/%2e%2e/secret",
        "/repos/allowed/..;param/secret",
        "/repos/allowed/．．/secret",
    ]:
        with pytest.raises(HTTPException):
            caps.authorize(manifest, {**request, "url": "https://api.example.com" + path})
    with pytest.raises(HTTPException):
        caps.authorize(manifest, {**request, "method": "DELETE"})


def test_capability_internal_auth_and_archived_revocation(client, internal_headers, runtime, monkeypatch):
    body = publication(client, True)
    body["manifest"]["backend"]["permissions"] = {
        "mcp": [{"tool": "github_list_commits", "arguments": {"repo": "nautionette"}}]
    }
    client.put("/api/miniapps/counter", json=body)
    describe = AsyncMock(return_value={"annotations": {"readOnlyHint": True}})
    invoke = AsyncMock(return_value={"content": [{"type": "text", "text": "safe"}]})
    monkeypatch.setattr(caps, "describe", describe)
    monkeypatch.setattr(caps, "mcp", invoke)
    payload = {
        "revision": 1,
        "request": {"kind": "mcp", "tool": "github_list_commits", "arguments": {"repo": "other"}},
    }
    path = "/internal/miniapps/counter/capability"
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, json=payload, headers=internal_headers).status_code == 403
    payload["request"]["arguments"]["repo"] = "nautionette"
    assert client.post(path, json=payload, headers=internal_headers).status_code == 200
    assert invoke.await_count == 1
    client.patch("/api/miniapps/counter", json={"expected_revision": 1, "archived": True})
    assert client.post(path, json=payload, headers=internal_headers).status_code == 409
    assert invoke.await_count == 1


def test_https_pins_ip_and_tls_name_without_credentials_or_redirect(monkeypatch):
    monkeypatch.setattr(caps, "public_addresses", lambda host: ["8.8.8.8"])
    requests = []

    async def upstream(request):
        requests.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/private", "set-cookie": "secret"}, json={"ok": True}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        caps.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs),
    )
    result = asyncio.run(caps.https({"url": "https://example.com/public", "method": "GET"}))
    assert result == {"status": 302, "body": {"ok": True}}
    assert len(requests) == 1


def test_mcp_session_reuses_initialization_and_calls_are_never_retried(monkeypatch):
    calls = []

    async def rpc(self, method, params=None, limit=caps.LIMIT):
        calls.append(method)
        if method == "initialize":
            return {"protocolVersion": "2025-06-18"}
        if method == "tools/list":
            return {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
        if method == "tools/call":
            return {"content": []}
        return {}

    monkeypatch.setattr(caps.Session, "rpc", rpc)

    async def run():
        caps.sessions.clear()
        for _ in range(2):
            await caps.describe("counter", {"tool": "read", "arguments": {}})
            await caps.mcp("counter", {"tool": "read", "arguments": {}})
        caps.sessions.clear()

    asyncio.run(run())
    assert calls.count("initialize") == calls.count("tools/list") == 1
    assert calls.count("tools/call") == 2


def test_known_build_rejection_is_failed_not_pending_and_keeps_diagnostics(client, runtime):
    method, _ = runtime
    request = httpx.Request("POST", "http://broker/miniapps/counter/deploy")
    response = httpx.Response(
        422, json={"detail": "Rust compilation failed: missing handle"}, request=request
    )
    method.side_effect = httpx.HTTPStatusError("rejected", request=request, response=response)
    reply = client.put("/api/miniapps/counter", json=publication(client, True))
    assert reply.status_code == 422 and "missing handle" in reply.json()["detail"]
    release = client.get("/api/miniapps/counter").json()["releases"][0]
    assert release["state"] == "failed" and "missing handle" in release["error"]


def test_deleted_owner_does_not_break_source_restore(client, db):
    db.execute("INSERT INTO agent_profiles(id,name,created_at,updated_at) VALUES('author','Author',0,0)")
    body = publication(client)
    body["manifest"]["agent_id"] = "author"
    assert client.put("/api/miniapps/counter", json=body).status_code == 200
    db.execute("DELETE FROM agent_profiles WHERE id='author'")
    assert client.get("/api/miniapps/counter").json()["agent_id"] is None
    reply = client.post("/api/miniapps/counter/restore", json={"expected_revision": 1, "revision": 1})
    assert reply.status_code == 200 and reply.json()["active_revision"] == 2
    assert reply.json()["active"]["manifest"]["agent_id"] is None


def test_staging_smoke_never_cleans_up_an_existing_app_after_create_conflict():
    from scripts import check_miniapps as smoke

    calls = []

    class API:
        def call(self, path, method="GET", body=None, expected=200):
            calls.append((method, path))
            if path == "/api/miniapps/contract":
                return {"example": {}}
            if path == "/api/miniapps/validate":
                return {"compiled": True, "artifact_bytes": 100}
            raise smoke.APIError("pre-existing app", 409)

    with pytest.raises(smoke.APIError):
        smoke.run(API(), "existing")
    assert calls == [
        ("GET", "/api/miniapps/contract"),
        ("POST", "/api/miniapps/validate"),
        ("PUT", "/api/miniapps/existing"),
    ]


@pytest.mark.parametrize("lost_reply", [False, True])
def test_staging_smoke_checks_real_api_and_never_repeats_publication(client, live, monkeypatch, lost_reply):
    from scripts import check_miniapps as smoke

    state = {"revision": None, "count": 0}
    deployed = []

    async def broker(app_id, action="", payload=None):
        if action == "check":
            return {"valid": True, "artifact_bytes": 123}
        if action == "deploy":
            revision = payload["revision"]
            deployed.append(revision)
            if revision == 2:
                request = httpx.Request("POST", "http://broker/deploy")
                raise httpx.HTTPStatusError(
                    "health failed",
                    request=request,
                    response=httpx.Response(422, json={"detail": "Health failed"}, request=request),
                )
            state["revision"] = revision
            if lost_reply and revision == 1:
                raise TimeoutError("reply lost")
        if action == "request":
            if payload["request"]["method"] != "GET":
                state["count"] += 1
            return {"value": state["count"]}
        return {**state, "status": "running", "memory_bytes": 1000, "image_bytes": 2000, "data_bytes": 3000}

    monkeypatch.setattr(live.broker, "miniapp", broker)
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)

    class API:
        def call(self, path, method="GET", body=None, expected=200):
            if lost_reply and path == "/api/miniapps/smoke" and method == "GET":
                asyncio.run(miniapps.reconcile())
            response = client.request(method, path, json=body)
            if response.status_code != expected:
                raise smoke.APIError("Unexpected status", response.status_code)
            return response.json() if response.status_code == 200 else None

    smoke.run(API(), "smoke")
    assert deployed == [1, 2, 3] and state["count"] == 1
    assert client.get("/api/miniapps/smoke").status_code == 404


def test_late_activation_cannot_revert_metadata_or_a_newer_release(client):
    body = publication(client)
    client.put("/api/miniapps/counter", json=body)
    manifest = client.get("/api/miniapps/counter").json()["active"]["manifest"]
    client.patch("/api/miniapps/counter", json={"expected_revision": 1, "pinned": False})
    miniapps.activate("counter", 1, manifest)
    assert client.get("/api/miniapps/counter").json()["pinned"] == 0
    body["expected_revision"] = 1
    body["manifest"]["title"] = "New code"
    client.put("/api/miniapps/counter", json=body)
    miniapps.activate("counter", 1, {**manifest, "title": "Stale code"})
    assert client.get("/api/miniapps/counter").json()["active_revision"] == 2
    assert client.get("/api/miniapps/counter").json()["title"] == "New code"


def test_concurrent_lifecycle_writes_fail_without_overlapping(client):
    body = publication(client)
    client.put("/api/miniapps/counter", json=body)
    with miniapps.exclusive("counter"):
        response = client.patch("/api/miniapps/counter", json={"expected_revision": 1, "archived": True})
        assert response.status_code == 409
    assert client.get("/api/miniapps/counter").json()["archived"] == 0
    assert (
        client.patch("/api/miniapps/counter", json={"expected_revision": 1, "archived": True}).status_code
        == 200
    )


@pytest.mark.parametrize("already_applied", [False, True])
def test_lost_archive_control_is_reconciled_without_repeating_applied_control(
    client, runtime, already_applied
):
    method, state = runtime
    client.put("/api/miniapps/counter", json=publication(client, True))
    state["disabled"] = False
    controls = []

    async def control(app_id, action="", payload=None):
        if action == "control":
            controls.append(payload)
            if len(controls) == 1:
                state["disabled"] = already_applied
                raise TimeoutError("reply lost")
            state["disabled"] = True
        return state.copy()

    method.side_effect = control
    assert (
        client.patch("/api/miniapps/counter", json={"expected_revision": 1, "archived": True}).status_code
        == 503
    )
    asyncio.run(miniapps.reconcile())
    asyncio.run(miniapps.reconcile())
    assert state["disabled"] and len(controls) == (1 if already_applied else 2)
