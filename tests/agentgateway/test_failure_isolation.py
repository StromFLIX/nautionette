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
                tools = await GatewayClient(base).mcp_tools(f"{base}/mcp", timeout=5)
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
