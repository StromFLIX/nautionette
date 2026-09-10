"""Opt-in test against a real gateway binary; no package download or API key.

NAUTIONETTE_TEST_GATEWAY_BINARY=/path/to/agentgateway uv run pytest tests/agentgateway
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import HTTPException
from nautionette_backend import mcp_servers
from nautionette_backend.clients import gateway
from nautionette_backend.config import settings

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("NAUTIONETTE_TEST_GATEWAY_BINARY") or not shutil.which("node"),
        reason="Set NAUTIONETTE_TEST_GATEWAY_BINARY and install Node to run the gateway smoke test",
    ),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class HttpMcp(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body["method"]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-fixture", "version": "1"},
            }
        else:
            result = {"tools": [{"name": "http_echo", "inputSchema": {"type": "object"}}]}
        content = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode()
        self.send_response(202 if "id" not in body else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


async def test_stdio_with_real_gateway_and_http_baseline(tmp_path, monkeypatch):
    binary = str(Path(os.environ["NAUTIONETTE_TEST_GATEWAY_BINARY"]).resolve())
    http_server = ThreadingHTTPServer(("127.0.0.1", 0), HttpMcp)
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(settings, "gateway_url", base)
    monkeypatch.setattr(settings, "mcp_url", f"{base}/mcp")
    monkeypatch.setattr(gateway, "base_url", base)
    config_file = tmp_path / "gateway.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "config": {
                    "storage": {"mode": "hybrid"},
                    "database": {"url": f"sqlite:///{tmp_path}/gateway.db"},
                    "adminAddr": "127.0.0.1:0",
                    "readinessAddr": "127.0.0.1:0",
                    "statsAddr": "127.0.0.1:0",
                },
                "gateways": {"default": {"port": port}},
                "ui": {"gateways": "default"},
                "mcp": {
                    "targets": [
                        {
                            "name": "baseline",
                            "mcp": {
                                "host": f"http://127.0.0.1:{http_server.server_port}/mcp",
                            },
                        }
                    ]
                },
            }
        )
    )
    log = (tmp_path / "gateway.log").open("w")
    process = None

    async def start():
        nonlocal process
        process = subprocess.Popen(  # noqa: S603 - explicitly supplied opt-in test binary
            [binary, "-f", str(config_file)],
            stdout=log,
            stderr=log,
            env={**os.environ, "NAUTIONETTE_GATEWAY_ONLY": "must-not-reach-child"},
        )
        for _ in range(100):
            assert process.poll() is None, (tmp_path / "gateway.log").read_text()
            try:
                await gateway.runtime()
                return
            except httpx.HTTPError:
                await asyncio.sleep(0.1)
        pytest.fail("gateway did not start")

    def stop():
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    try:
        await start()
        launch = mcp_servers.normalise_config(
            "fixture",
            {
                "transport": "stdio",
                "command": shutil.which("node"),
                "args": [str(Path(__file__).with_name("stdio-server.mjs")), "two words", "${LITERAL_ARG}"],
                "env": {"TEST_VALUE": "$literal", "PID_FILE": str(tmp_path / "pids")},
            },
        )
        await mcp_servers.save("fixture", launch)
        servers = (await mcp_servers.payload())["servers"]
        assert {server["name"] for server in servers} == {"fixture", "baseline"}
        assert all(server["tool_count"] == 1 for server in servers)
        assert "$literal" not in json.dumps(servers)
        assert (await mcp_servers.test("fixture"))["ok"]
        # Round-trip the secret-free Settings payload: no double expansion or key loss.
        saved = next(server for server in servers if server["name"] == "fixture")
        assert saved["args"][-1] == "${LITERAL_ARG}"
        await mcp_servers.save("fixture", mcp_servers.normalise_config("fixture", saved))
        previous = await gateway.config_resources("mcp.target")
        with pytest.raises(HTTPException, match="stdio process did not answer"):
            await mcp_servers.save("fixture", {**launch, "command": "/no-such-command"})
        assert await gateway.config_resources("mcp.target") == previous
        assert await gateway.config_resources("traffic.route") == []
        assert len(await gateway.mcp_tools()) == 2
        stop()
        await start()
        assert len(await gateway.mcp_tools()) == 2  # persisted launch survives a gateway restart
        await mcp_servers.remove("fixture")
        assert len(await gateway.mcp_tools()) == 1  # HTTP baseline still works
        assert (await mcp_servers.payload())["servers"][0]["name"] == "baseline"
        # All short-lived discovery sessions should have released their children.
        pids = (tmp_path / "pids").read_text().splitlines()
        for _ in range(50):
            if all(not Path(f"/proc/{pid}").exists() for pid in pids):
                break
            await asyncio.sleep(0.1)
        assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
    finally:
        stop()
        log.close()
        http_server.shutdown()
        http_server.server_close()
