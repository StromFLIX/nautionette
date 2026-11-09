"""OAuth prompts exist only for live tool demands, before any remote dispatch."""

import json
import time

import pytest
from fastapi import HTTPException
from nautionette_backend import chat_outputs, mcp_auth_wait, mcp_oauth


@pytest.fixture
def demand(db):
    chat = db.create_chat("OAuth", "default")
    message, _ = db.accept_chat_message(chat["id"], "Use the tool", "auth-turn")
    db.execute(
        "INSERT INTO mcp_http_oauth VALUES (?,?,?,?,?,?,?)",
        ("example", json.dumps({"mode": "https"}), "", "reauth_required", 0, 0, "expired"),
    )
    tool = {
        "name": "example_write",
        "server": "example",
        "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
    }
    mcp_auth_wait.remember_tools([tool])
    with chat_outputs.publication_grant(chat["id"], message["id"]) as grant:
        yield {**grant, "tool": tool["name"], "tool_call_id": "tool-1"}, tool


def test_expiry_alone_has_no_requests_but_preserves_tool_schema(db, demand):
    payload, tool = demand
    assert mcp_oauth.auth_status()["requests"] == []
    assert mcp_auth_wait.unavailable_tools() == [tool]
    assert mcp_auth_wait.gate(payload) == {"status": "waiting", "server": "example"}
    waits = mcp_oauth.auth_status()["requests"]
    assert len(waits) == 1 and waits[0]["server"] == "example"
    assert payload["grant"] not in json.dumps(waits)
    assert mcp_auth_wait.gate(payload)["status"] == "waiting"
    assert len(mcp_oauth.auth_status()["requests"]) == 1


def test_login_resumes_only_after_gateway_connection_is_active(db, demand):
    payload, _ = demand
    mcp_auth_wait.gate(payload)
    for status in ("authorizing", "gateway_pending", "refreshing"):
        db.execute("UPDATE mcp_http_oauth SET status=?", (status,))
        assert mcp_auth_wait.gate(payload)["status"] == "waiting"
    db.execute("UPDATE mcp_http_oauth SET status='connected',expires_at=?", (time.time() + 300,))
    assert mcp_auth_wait.gate(payload)["status"] == "ready"
    assert mcp_oauth.auth_status()["requests"] == []
    assert mcp_auth_wait.unavailable_tools() == []


def test_skip_or_snooze_fails_all_selected_calls_even_if_login_finishes_later(client, db, demand):
    payload, _ = demand
    mcp_auth_wait.gate(payload)
    second = {**payload, "tool_call_id": "tool-2"}
    mcp_auth_wait.gate(second)
    ids = [row["id"] for row in mcp_oauth.auth_status()["requests"]]
    assert client.post("/api/mcp-oauth/dismiss", json={"requests": ids}).status_code == 200
    db.execute("UPDATE mcp_http_oauth SET status='connected',expires_at=?", (time.time() + 300,))
    assert mcp_auth_wait.gate(payload)["status"] == "failed"
    assert mcp_auth_wait.gate(second)["status"] == "failed"
    assert mcp_oauth.auth_status()["requests"] == []
    assert mcp_auth_wait.gate({**payload, "tool_call_id": "new-call"})["status"] == "ready"


def test_deadline_is_not_extended_by_polling_or_late_login(db, demand):
    payload, _ = demand
    mcp_auth_wait.gate(payload)
    deadline = mcp_oauth.auth_status()["requests"][0]["deadline"]
    mcp_auth_wait.gate(payload)
    assert mcp_oauth.auth_status()["requests"][0]["deadline"] == deadline
    db.execute("UPDATE mcp_oauth_waits SET deadline=0")
    db.execute("UPDATE mcp_http_oauth SET status='connected',expires_at=?", (time.time() + 300,))
    assert mcp_auth_wait.gate(payload)["status"] == "failed"
    assert mcp_oauth.auth_status()["requests"] == []


def test_stopped_turns_and_expired_grants_cannot_prompt_or_resume(client, db, demand):
    payload, _ = demand
    mcp_auth_wait.gate(payload)
    db.execute("UPDATE chat_turns SET stop_requested=1")
    assert mcp_oauth.auth_status()["requests"] == []
    with pytest.raises(HTTPException):
        mcp_auth_wait.gate(payload)
    response = client.post("/api/mcp-oauth/gate", json={**payload, "grant": "invalid"})
    assert response.status_code == 401


def test_unrelated_tools_do_not_wait_and_removed_connections_fail(db, demand):
    payload, _ = demand
    assert mcp_auth_wait.gate({**payload, "tool": "backend_system_status"})["status"] == "ready"
    mcp_auth_wait.gate(payload)
    db.execute("DELETE FROM mcp_http_oauth")
    assert mcp_auth_wait.unavailable_tools() == []
    assert mcp_auth_wait.gate(payload)["status"] == "failed"


def test_bridge_discovery_caches_full_schemas_and_lists_disconnected_tools(client, backend, db, demand):
    _, tool = demand
    db.execute("DELETE FROM mcp_oauth_tools")
    db.execute("UPDATE mcp_http_oauth SET status='connected',expires_at=?", (time.time() + 300,))
    backend.gateway.resources["mcp.target"] = {
        "example": {"name": "example", "mcp": {"host": "https://example.test/mcp"}}
    }
    backend.gateway.tools = {"": [{key: value for key, value in tool.items() if key != "server"}]}
    response = client.post("/api/mcp-oauth/tools", json={})
    assert response.status_code == 200, response.text
    assert response.json()["oauth_tools"] == ["example_write"]
    db.execute("UPDATE mcp_http_oauth SET status='reauth_required',expires_at=0")
    backend.gateway.resources["mcp.target"] = {}
    backend.gateway.tools = {"": []}
    disconnected = client.post("/api/mcp-oauth/tools", json={}).json()["tools"]
    assert next(item for item in disconnected if item["name"] == "example_write") == tool
    catalog = client.get("/api/catalog?refresh=true").json()
    assert {"name": "example", "host": "", "count": 1} in catalog["tool_servers"]


def test_cancelled_gate_is_removed_and_cannot_resume(db, demand):
    payload, _ = demand
    mcp_auth_wait.gate(payload)
    assert mcp_auth_wait.gate({**payload, "cancel": True})["status"] == "failed"
    assert mcp_oauth.auth_status()["requests"] == []
    assert mcp_auth_wait.gate(payload)["status"] == "failed"


def test_non_chat_agents_can_use_connected_tools_but_cannot_create_login_prompts(db, demand):
    payload, _ = demand
    payload.pop("grant")
    assert mcp_auth_wait.gate(payload)["status"] == "failed"
    assert mcp_oauth.auth_status()["requests"] == []
    db.execute("UPDATE mcp_http_oauth SET status='connected',expires_at=?", (time.time() + 300,))
    assert mcp_auth_wait.gate(payload)["status"] == "ready"


def test_already_expired_connected_tokens_are_not_dispatched(db, demand):
    payload, _ = demand
    db.execute("UPDATE mcp_http_oauth SET status='connected',expires_at=1")
    assert mcp_auth_wait.gate(payload)["status"] == "waiting"
