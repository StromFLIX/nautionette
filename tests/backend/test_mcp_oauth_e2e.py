"""Opt-in full bridge with real gateway, backend APIs, CLI, desktop and MCP.

NAUTIONETTE_TEST_GATEWAY_BINARY=/path/to/agentgateway uv run pytest <this file>
Two loopback interfaces emulate the desktop/gateway network namespaces, so the
same detected callback port can be bound on both sides without Docker privileges.
No Microsoft account, EULA acceptance or workplace-data request is involved.
"""

import asyncio
import importlib.util
import json
import os
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import yaml
from nautionette_backend import mcp_oauth
from nautionette_backend.clients.agentgateway import GatewayClient
from nautionette_backend.config import settings

from ..conftest import INTERNAL_TOKEN

pytestmark = pytest.mark.skipif(
    not os.environ.get("NAUTIONETTE_TEST_GATEWAY_BINARY") or not shutil.which("node"),
    reason="Provide a real gateway binary and Node for full CLI OAuth E2E",
)
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cli_oauth_e2e", ROOT / "services/agentgateway/cli_oauth.py")
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)


@pytest.fixture
def stack(backend, live, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_oauth, "_sessions", {})
    monkeypatch.setattr(mcp_oauth, "_lock", asyncio.Lock())
    control = cli.ControlServer(("127.0.0.1", 0), INTERNAL_TOKEN)
    thread = threading.Thread(target=control.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(settings, "gateway_url", base)
    monkeypatch.setattr(settings, "mcp_url", base + "/mcp")
    monkeypatch.setattr(settings, "gateway_cli_oauth_url", f"http://127.0.0.1:{control.server_port}")
    native = GatewayClient(base)
    for name in [
        "runtime",
        "config",
        "config_resources",
        "put_config_resources",
        "delete_config_resource",
        "mcp_tools",
        "cli_oauth",
    ]:
        monkeypatch.setattr(live.gateway, name, getattr(native, name))
    path = tmp_path / "gateway.yaml"
    path.write_text(
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
                "mcp": {"targets": []},
            }
        )
    )
    log = (tmp_path / "gateway.log").open("w")
    process = subprocess.Popen(  # noqa: S603 - explicit test binary and local fixture config
        [os.environ["NAUTIONETTE_TEST_GATEWAY_BINARY"], "-f", str(path)],
        stdout=log,
        stderr=log,
        start_new_session=True,
        env={**os.environ, "OPENAI_API_KEY": "must-not-reach-cli", "CLI_OAUTH_TOKEN": "must-not-reach-cli"},
    )
    try:
        for _ in range(100):
            assert process.poll() is None, "Gateway failed to start"
            try:
                with httpx.Client(timeout=1) as http:
                    if http.get(base + "/api/runtime").is_success:
                        break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("Gateway did not become ready")
        yield native, control, tmp_path
    finally:
        control.supervisor.close()
        control.shutdown()
        control.server_close()
        thread.join(timeout=5)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        log.close()


@pytest.mark.parametrize(
    "response_mode,login_mode", [("form_post", "login"), ("query", "login"), ("form_post", "retry")]
)
def test_full_desktop_to_backend_to_cli_to_real_gateway_mcp(client, stack, response_mode, login_mode):
    native, control, tmp_path = stack
    home = tmp_path / "cli-home"
    fixture = str(ROOT / "tests/agentgateway/oauth-cli.py")
    config = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [fixture, "mcp"],
        "provider": "generic",
        "login_args": [fixture, login_mode],
        "setup_args": [[fixture, "setup"]],
        "env": {
            "HOME": str(home),
            "LOOPBACK_HOST": "127.0.0.2",
            "RESPONSE_MODE": response_mode,
            "LITERAL_SECRET": "$synthetic-with-$$-literal-dollars",
        },
    }
    node = subprocess.Popen(  # noqa: S603 - fixed production receiver/controller test driver
        [shutil.which("node"), str(ROOT / "tests/agentgateway/oauth-desktop.mjs")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(node.stdout, selectors.EVENT_READ)
    callbacks = 0
    try:
        node.stdin.write(json.dumps({"kind": "begin", "config": config}) + "\n")
        node.stdin.flush()
        deadline = time.monotonic() + 45
        buffered = bytearray()
        while time.monotonic() < deadline:
            while b"\n" not in buffered:
                assert selector.select(timeout=15), "Desktop driver stopped responding"
                chunk = os.read(node.stdout.fileno(), 65536)
                assert chunk, "Desktop driver exited unexpectedly: " + node.stderr.read()[-4000:]
                buffered.extend(chunk)
            line, _, buffered = buffered.partition(b"\n")
            message = json.loads(line)
            if message["kind"] == "done":
                assert message["ok"], message.get("error")
                assert any("detected local port" in stage for stage in message["stages"])
                break
            operation, args = message["operation"], message["args"]
            if operation == "startMcpOAuth":
                response = client.post("/api/mcp-oauth/start/" + args[0], json=args[1])
            elif operation == "pollMcpOAuth":
                response = client.get("/api/mcp-oauth/login/" + args[0])
            elif operation == "forwardMcpOAuth":
                callbacks += 1
                response = client.post("/api/mcp-oauth/callback/" + args[0], json=args[1])
            else:
                response = client.delete("/api/mcp-oauth/login/" + args[0])
            reply = {"id": message["id"], "result": response.json() if response.content else None}
            if response.status_code >= 400:
                reply = {"id": message["id"], "error": response.json().get("detail", "API failed")}
            node.stdin.write(json.dumps(reply) + "\n")
            node.stdin.flush()
        else:
            pytest.fail("Full OAuth bridge timed out")
        assert node.wait(timeout=5) == 0
        assert callbacks == (2 if login_mode == "retry" else 1)
        assert (home / "synthetic-cli-cache.json").exists()
        servers = client.get("/api/mcp-servers").json()["servers"]
        assert servers[0]["transport"] == "stdio" and servers[0]["tool_count"] == 1
        assert servers[0]["oauth"]["mode"] == "cli"
        assert "synthetic-private" not in json.dumps(servers)
        assert client.post("/api/mcp-servers/oauth-test/test").json()["ok"]
        assert client.delete("/api/mcp-servers/oauth-test").status_code == 200
        assert client.get("/api/mcp-servers").json()["servers"] == []
    finally:
        selector.close()
        if node.poll() is None:
            node.kill()
        node.communicate(timeout=5)
