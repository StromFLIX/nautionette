"""OAuth augmentation must not rediscover unrelated live tools on every Pi start.

The initial agent tools/list remains authoritative. Published OAuth targets still
need trusted fresh discovery to learn schemas/owners; saved schemas cover offline
reconnect. Call-time gating, expiry and fresh post-login dispatch are unchanged.
"""

import asyncio
import json
import time

import pytest
from nautionette_backend import chat_outputs, mcp_auth_wait
from nautionette_backend.routers import gateway as routes


@pytest.fixture
def bridge(backend, live, monkeypatch):
    backend.gateway.resources["mcp.target"] = {
        "backend": {"name": "backend", "mcp": {"host": "http://backend.test/mcp"}}
    }
    backend.gateway.tools = {
        "": [{"name": "backend_health", "description": "Health", "inputSchema": {"type": "object"}}]
    }
    backend.config_calls = []
    backend.discovery_calls = []
    config, discover = live.gateway.config, live.gateway.mcp_tools

    async def record_config():
        backend.config_calls.append(True)
        return await config()

    async def record_discovery(url=None, extra=None, **kwargs):
        backend.discovery_calls.append(url or "federation")
        return await discover(url, extra, **kwargs)

    monkeypatch.setattr(live.gateway, "config", record_config)
    monkeypatch.setattr(live.gateway, "mcp_tools", record_discovery)
    return backend


def connection(bridge, name="example", *, status="reauth_required", published=False):
    bridge.db.execute(
        "INSERT INTO mcp_http_oauth VALUES (?,?,?,?,?,?,?)",
        (name, json.dumps({"mode": "https"}), "", status, time.time() + 300, 0, "first-revision"),
    )
    tool = {
        "name": f"{name}_write",
        "server": name,
        "description": "Write once",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
    mcp_auth_wait.remember_tools([tool])
    if published:
        bridge.gateway.resources["mcp.target"][name] = {
            "name": name,
            "mcp": {"host": f"https://{name}.test/mcp"},
        }
        bridge.gateway.tools[""].append({key: value for key, value in tool.items() if key != "server"})
    return tool


def augment(client, body=None):
    response = client.post("/api/mcp-oauth/tools", json=body or {})
    assert response.status_code == 200, response.text
    return response.json()


def test_no_oauth_needs_no_config_or_second_discovery_and_ignores_caller_definitions(client, bridge):
    for _ in range(3):
        assert augment(client, {"tools": [{"name": "untrusted_write"}]}) == {"tools": [], "oauth_tools": []}
    assert bridge.config_calls == []
    assert bridge.discovery_calls == []
    assert not bridge.db.query("SELECT * FROM mcp_oauth_tools")


def test_disconnected_connection_preserves_schemas_and_gates_without_second_discovery(client, bridge):
    tool = connection(bridge)
    for _ in range(3):
        assert augment(client) == {"tools": [tool], "oauth_tools": [tool["name"]]}
    # Fresh target checks, not a TTL cache or a stored authentication verdict.
    assert len(bridge.config_calls) == 3
    assert bridge.discovery_calls == []
    assert mcp_auth_wait.gate({"tool": tool["name"], "tool_call_id": "probe"})["status"] == "failed"
    assert mcp_auth_wait.pending() == []
    # Removing a connection also removes its saved schemas and gate metadata.
    bridge.db.execute("DELETE FROM mcp_http_oauth WHERE name='example'")
    assert augment(client) == {"tools": [], "oauth_tools": []}
    assert len(bridge.config_calls) == 3


def test_disconnected_chat_tools_still_create_the_normal_pre_dispatch_login_wait(client, bridge):
    tool = connection(bridge)
    chat = bridge.db.create_chat("Bridge", "default")
    message, _ = bridge.db.accept_chat_message(chat["id"], "Use the tool", "bridge-auth-turn")
    with chat_outputs.publication_grant(chat["id"], message["id"]) as grant:
        payload = {**grant, "tool": tool["name"], "tool_call_id": "bridge-demand"}
        assert augment(client) == {"tools": [tool], "oauth_tools": [tool["name"]]}
        assert bridge.discovery_calls == []
        assert mcp_auth_wait.gate(payload) == {"status": "waiting", "server": "example"}
        assert len(mcp_auth_wait.pending()) == 1
        bridge.db.execute("UPDATE mcp_http_oauth SET status='connected',expires_at=?", (time.time() + 300,))
        assert mcp_auth_wait.gate(payload) == {"status": "ready"}


@pytest.mark.parametrize(
    "status", ["connected", "authorizing", "gateway_pending", "refreshing", "reauth_required"]
)
def test_every_published_oauth_state_keeps_fresh_discovery_and_learns_new_schemas(client, bridge, status):
    tool = connection(bridge, status=status, published=True)
    # First successful login need not have stored schemas yet.
    bridge.db.execute("DELETE FROM mcp_oauth_tools")
    assert tool["name"] in augment(client)["oauth_tools"]
    # Upstream schemas/new tools can change without a new login or owner revision.
    bridge.gateway.tools[""][-1]["inputSchema"] = {
        "type": "object",
        "properties": {"updated": {"type": "boolean"}},
    }
    bridge.gateway.tools[""].append(
        {"name": "example_new", "description": "New tool", "inputSchema": {"type": "object"}}
    )
    payload = augment(client)
    assert set(payload["oauth_tools"]) == {"example_write", "example_new"}
    assert bridge.discovery_calls == ["federation", "federation"]
    stored = bridge.db.one("SELECT server,schema FROM mcp_oauth_tools WHERE name='example_write'")
    assert stored["server"] == "example"
    assert json.loads(stored["schema"])["inputSchema"]["properties"] == {"updated": {"type": "boolean"}}


def test_mixed_live_and_offline_oauth_preserves_both_owner_sets(client, bridge):
    offline = connection(bridge)
    connection(bridge, "active", status="connected", published=True)
    payload = augment(client)
    assert set(payload["oauth_tools"]) == {"example_write", "active_write"}
    assert offline in payload["tools"]
    assert bridge.discovery_calls == ["federation"]


def test_single_target_raw_names_keep_existing_trusted_attribution(client, bridge):
    connection(bridge, status="connected", published=True)
    bridge.gateway.resources["mcp.target"].pop("backend")
    bridge.gateway.tools[""] = [{"name": "raw_new_tool", "inputSchema": {"type": "object"}}]
    payload = augment(client)
    assert "raw_new_tool" in payload["oauth_tools"]
    assert (
        bridge.db.one("SELECT server FROM mcp_oauth_tools WHERE name='raw_new_tool'")["server"] == "example"
    )
    assert bridge.discovery_calls == ["federation"]


def test_failed_config_read_does_not_infer_oauth_absence(client, bridge, live, monkeypatch):
    offline = connection(bridge)

    async def unavailable():
        raise RuntimeError("Private gateway error must not be forwarded")

    monkeypatch.setattr(live.gateway, "config", unavailable)
    payload = augment(client)
    assert bridge.discovery_calls == ["federation"]
    assert offline in payload["tools"]
    assert payload["oauth_tools"] == [offline["name"]]
    assert "Private" not in json.dumps(payload)


@pytest.mark.parametrize("change", ["login", "revision", "remove", "add"])
async def test_changes_during_config_io_take_fresh_config_and_conservative_discovery(
    bridge, live, monkeypatch, change
):
    connection(bridge)
    entered, release = asyncio.Event(), asyncio.Event()
    original = live.gateway.config
    reads = 0

    async def blocked_config():
        nonlocal reads
        reads += 1
        snapshot = await original()
        if reads == 1:
            entered.set()
            await release.wait()
        return snapshot

    monkeypatch.setattr(live.gateway, "config", blocked_config)
    task = asyncio.create_task(routes.mcp_oauth_tools({}))
    try:
        await entered.wait()
        if change == "remove":
            bridge.db.execute("DELETE FROM mcp_http_oauth WHERE name='example'")
        else:
            owner = "example"
            if change == "add":
                owner = "second"
                connection(bridge, owner)
            elif change == "revision":
                bridge.db.execute("UPDATE mcp_http_oauth SET revision='new-revision'")
            else:
                bridge.db.execute("UPDATE mcp_http_oauth SET status='gateway_pending'")
            bridge.gateway.resources["mcp.target"][owner] = {
                "name": owner,
                "mcp": {"host": f"https://{owner}.test/mcp"},
            }
            bridge.gateway.tools[""].append({"name": f"{owner}_fresh", "inputSchema": {"type": "object"}})
        release.set()
        payload = await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert reads == 2  # Do not attribute a new tool using the pre-login config snapshot.
    assert bridge.discovery_calls == ["federation"]
    if change == "remove":
        assert payload["oauth_tools"] == []
        assert not any(tool["name"] == "example_write" for tool in payload["tools"])
    else:
        assert f"{owner}_fresh" in payload["oauth_tools"]
        assert (
            bridge.db.one("SELECT server FROM mcp_oauth_tools WHERE name=?", (f"{owner}_fresh",))["server"]
            == owner
        )


async def test_cancelling_config_lookup_never_starts_an_extra_discovery(bridge, live, monkeypatch):
    connection(bridge)
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(live.gateway, "config", blocked)
    task = asyncio.create_task(routes.mcp_oauth_tools({}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bridge.discovery_calls == []
