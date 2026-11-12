"""One expired/unavailable MCP upstream must not take down unrelated tools.

The runtime regression loads the shipped config and the checksum-pinned gateway
used in CI. Only local fixture credentials are used; no provider/model calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml
from nautionette_backend.clients.agentgateway import GatewayClient
from nautionette_backend.mcp_selection import HEADER, scoped_target

CONFIG = Path(__file__).resolve().parents[2] / "services/agentgateway/config/config.yaml"


def test_shared_federation_isolates_failed_upstreams():
    assert yaml.safe_load(CONFIG.read_text())["mcp"]["failureMode"] == "failOpen"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class McpUpstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, value):
        content = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body["method"]
        self.server.requests.append((self.path, method))
        # Authentication is still enforced independently for every upstream.
        if self.headers.get("Authorization") != "Bearer local-test-token":
            return self.reply(401, {"error": {"message": "unauthorized"}})
        broken = self.path == "/broken" or self.server.all_broken
        if broken and (self.server.failure_stage == "initialize" or method in {"tools/list", "tools/call"}):
            return self.reply(self.server.failure_status, {"error": {"message": "fixture failure"}})
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            self.server.calls.append(self.path)
            result = {"content": [{"type": "text", "text": "healthy result"}]}
        else:
            return self.reply(202, {})
        return self.reply(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": result})


@pytest.mark.slow
@pytest.mark.parametrize("stage", ["initialize", "tools/list"])
@pytest.mark.parametrize("status", [401, 503])
async def test_real_gateway_keeps_healthy_tools_and_never_bypasses_failed_auth(tmp_path, stage, status):
    binary = os.environ.get("NAUTIONETTE_TEST_GATEWAY_BINARY")
    if not binary:
        pytest.skip("Set NAUTIONETTE_TEST_GATEWAY_BINARY for real MCP failure-isolation checks")
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), McpUpstream)
    upstream.failure_stage, upstream.failure_status = stage, status
    upstream.all_broken, upstream.requests, upstream.calls = False, [], []
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    config = yaml.safe_load(CONFIG.read_text())
    config["config"].update(
        database={"url": f"sqlite:///{tmp_path}/gateway.db"},
        adminAddr="127.0.0.1:0",
        readinessAddr="127.0.0.1:0",
        statsAddr="127.0.0.1:0",
    )
    config["gateways"]["default"]["port"] = port
    config["mcp"]["targets"] = [
        {
            "name": name,
            "mcp": {"host": f"http://127.0.0.1:{upstream.server_port}/{name}"},
            "policies": {"backendAuth": {"key": {"value": "local-test-token"}}},
        }
        for name in ("healthy", "broken")
    ]
    # Production adds integrations through hybrid storage, not the baseline.
    # Its merge must preserve the baseline's isolation policy too.
    runtime_target = config["mcp"]["targets"].pop()
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(config))
    process = None
    try:
        with (tmp_path / "gateway.log").open("w") as log:
            process = subprocess.Popen(  # noqa: S603 - opt-in pinned test binary
                [str(Path(binary).resolve()), "-f", str(path)], stdout=log, stderr=log
            )
            async with httpx.AsyncClient(base_url=base, timeout=5, trust_env=False) as client:
                for _ in range(100):
                    assert process.poll() is None, (tmp_path / "gateway.log").read_text()
                    try:
                        if (await client.get("/api/runtime")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("gateway did not start")
                saved = await client.put(
                    "/api/config/resources/mcp.target", json={"resources": [{"value": runtime_target}]}
                )
                saved.raise_for_status()
                # Config acknowledgement precedes the asynchronous data-plane
                # update. Poll only read-only discovery, never repeat the PUT.
                for _ in range(50):
                    tools = await GatewayClient(base).mcp_tools(f"{base}/mcp", timeout=5)
                    if [tool["name"] for tool in tools] == ["healthy_read"]:
                        break
                    await asyncio.sleep(0.1)
                assert [tool["name"] for tool in tools] == ["healthy_read"]
                assert ("/broken", stage) in upstream.requests
                handshake = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "isolation-test", "version": "1"},
                        },
                    },
                    headers={"Accept": "application/json, text/event-stream"},
                )
                handshake.raise_for_status()
                headers = {
                    "Accept": "application/json, text/event-stream",
                    "Mcp-Session-Id": handshake.headers["mcp-session-id"],
                    "MCP-Protocol-Version": "2025-06-18",
                }
                try:
                    initialized = await client.post(
                        "/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    )
                    initialized.raise_for_status()
                    # No fallback to a healthy server, replay or auth bypass for the bad target.
                    rejected = await client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "broken_read", "arguments": {}},
                        },
                    )
                    assert (
                        rejected.is_error
                        or "error" in rejected.json()
                        or rejected.json().get("result", {}).get("isError")
                    )
                    assert upstream.calls == []
                    answer = await client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {"name": "healthy_read", "arguments": {}},
                        },
                    )
                    answer.raise_for_status()
                    assert "healthy result" in answer.text
                    assert upstream.calls == ["/healthy"]
                finally:
                    await client.delete("/mcp", headers=headers)
                # Isolation is not unconditional success: all failures stay failures.
                upstream.all_broken = True
                with pytest.raises((httpx.HTTPError, ValueError)):
                    await GatewayClient(base).mcp_tools(f"{base}/mcp", timeout=5)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


@pytest.mark.slow
async def test_real_gateway_scopes_before_contact_preserves_names_and_singleton_bootstrap(
    tmp_path, monkeypatch
):
    binary = os.environ.get("NAUTIONETTE_TEST_GATEWAY_BINARY")
    if not binary:
        pytest.skip("Set NAUTIONETTE_TEST_GATEWAY_BINARY for real MCP target-selection checks")
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), McpUpstream)
    upstream.failure_stage, upstream.failure_status = "initialize", 401
    upstream.all_broken, upstream.requests, upstream.calls = False, [], []
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    config = yaml.safe_load(CONFIG.read_text())
    config["config"].update(
        database={"url": f"sqlite:///{tmp_path}/selection.db"},
        adminAddr="127.0.0.1:0",
        readinessAddr="127.0.0.1:0",
        statsAddr="127.0.0.1:0",
    )
    config["gateways"]["default"]["port"] = port

    def target(name):
        return {
            "name": name,
            "mcp": {"host": f"http://127.0.0.1:{upstream.server_port}/{name}"},
            "policies": {"backendAuth": {"key": {"value": "local-test-token"}}},
        }

    # The file-owned first-party workflow service stays unfiltered: startup is
    # valid on an empty volume, before backend registers the second real target.
    config["mcp"]["targets"] = [target("workflows")]
    path = tmp_path / "selection.yaml"
    path.write_text(yaml.safe_dump(config))
    process = None
    try:
        with (tmp_path / "selection.log").open("w") as log:
            process = subprocess.Popen(  # noqa: S603 - opt-in checksum-pinned test binary
                [str(Path(binary).resolve()), "-f", str(path)], stdout=log, stderr=log
            )
            async with httpx.AsyncClient(base_url=base, timeout=5, trust_env=False) as client:
                for _ in range(100):
                    assert process.poll() is None, (tmp_path / "selection.log").read_text()
                    try:
                        if (await client.get("/api/runtime")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("gateway did not start")
                native = GatewayClient(base)
                assert [tool["name"] for tool in await native.mcp_tools(f"{base}/mcp")] == ["read"]
                # Existing hybrid records must round-trip through real GET, not
                # only our in-memory fake's simplified resource representation.
                saved = await client.put(
                    "/api/config/resources/mcp.target",
                    json={
                        "resources": [
                            {"value": target(name)} for name in ["backend", "alpha", "beta", "broken"]
                        ]
                    },
                )
                saved.raise_for_status()
                from nautionette_backend import mcp_servers

                monkeypatch.setattr(mcp_servers, "gateway", native)
                await mcp_servers.reconcile_selection()
                assert all(
                    "condition" in item["value"] for item in await native.config_resources("mcp.target")
                )
                for _ in range(50):
                    try:
                        tools = await native.mcp_tools(f"{base}/mcp", extra={HEADER: "alpha"})
                        if {tool["name"] for tool in tools} == {
                            "workflows_read",
                            "backend_read",
                            "alpha_read",
                        }:
                            break
                    except httpx.HTTPError:
                        pass  # Config acknowledgement can precede data-plane session invalidation.
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("scoped gateway catalog did not become ready")
                # Control uses its own backend-only scope. A newly connected
                # remote target must not invalidate a held OAuth gate's session.
                control_headers = {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                    HEADER: "",
                }
                handshake = await client.post(
                    "/mcp",
                    headers=control_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 71,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "control", "version": "1"},
                        },
                    },
                )
                handshake.raise_for_status()
                control_headers["Mcp-Session-Id"] = handshake.headers["mcp-session-id"]
                initialized = await client.post(
                    "/mcp",
                    headers=control_headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                initialized.raise_for_status()
                await native.put_config_resources("mcp.target", [target("gamma")])
                for _ in range(50):
                    try:
                        fresh = await native.mcp_tools(f"{base}/mcp", extra={HEADER: "gamma"})
                        if "gamma_read" in {tool["name"] for tool in fresh}:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
                answer = await client.post(
                    "/mcp",
                    headers=control_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 72,
                        "method": "tools/call",
                        "params": {"name": "backend_read", "arguments": {}},
                    },
                )
                answer.raise_for_status()
                assert "healthy result" in answer.text
                await client.delete("/mcp", headers=control_headers)
                upstream.requests.clear()
                for selected, expected in [
                    ("", {"workflows_read", "backend_read"}),
                    ("alpha", {"workflows_read", "backend_read", "alpha_read"}),
                ]:
                    tools = await native.mcp_tools(f"{base}/mcp", extra={HEADER: selected})
                    assert {tool["name"] for tool in tools} == expected
                assert all(path not in {"/beta", "/broken"} for path, _ in upstream.requests)
                # Separate concurrent sessions never change shared target membership.
                alpha, beta = await asyncio.gather(
                    native.mcp_tools(f"{base}/mcp", extra={HEADER: "alpha"}),
                    native.mcp_tools(f"{base}/mcp", extra={HEADER: "beta"}),
                )
                assert {tool["name"] for tool in alpha} == {"workflows_read", "backend_read", "alpha_read"}
                assert {tool["name"] for tool in beta} == {"workflows_read", "backend_read", "beta_read"}
                # Missing header remains legacy all-target discovery, including
                # failed auth. It is not made successful by removing authentication.
                upstream.requests.clear()
                tools = await native.mcp_tools(f"{base}/mcp")
                assert {tool["name"] for tool in tools} == {
                    "workflows_read",
                    "backend_read",
                    "alpha_read",
                    "beta_read",
                    "gamma_read",
                }
                assert ("/broken", "initialize") in upstream.requests
                denied = scoped_target({**target("alpha"), "condition": "false"})
                await native.put_config_resources("mcp.target", [denied])
                for _ in range(50):
                    tools = await native.mcp_tools(f"{base}/mcp", extra={HEADER: "alpha"})
                    if "alpha_read" not in {tool["name"] for tool in tools}:
                        break
                    await asyncio.sleep(0.1)
                assert {tool["name"] for tool in tools} == {"workflows_read", "backend_read"}
                assert upstream.calls == ["/backend"]  # Only the deliberate read-only control call ran.
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)
