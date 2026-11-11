"""Real TLS hosted MCP + OAuth backend + native receiver + real agentgateway.

A temporary test CA is trusted explicitly (TLS verification is never disabled).
No Microsoft account, real provider credential, CLI or workplace data is used.
"""

import asyncio
import base64
import datetime
import hashlib
import ipaddress
import json
import os
import re
import selectors
import shutil
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from nautionette_backend import mcp_http_oauth as oauth
from nautionette_backend import mcp_servers
from nautionette_backend.pi_package_secrets import seal, unseal

from .test_mcp_oauth_e2e import stack  # noqa: F401 - imported pytest fixture

pytestmark = pytest.mark.skipif(
    not os.environ.get("NAUTIONETTE_TEST_GATEWAY_BINARY") or not shutil.which("node"),
    reason="Provide a real gateway binary and Node for HTTPS OAuth E2E",
)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def tls(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nautionette test CA")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file, leaf_file = (
        tmp_path / "test-ca.pem",
        tmp_path / "test-key.pem",
        tmp_path / "test-leaf.pem",
    )
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    leaf_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    key_file.chmod(0o600)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(leaf_file, key_file)
    client_context = ssl.create_default_context(cafile=str(cert_file))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    monkeypatch.setattr(
        oauth, "network_client", lambda: httpx.AsyncClient(verify=client_context, trust_env=False, timeout=10)
    )
    monkeypatch.setattr(oauth, "public_addresses", lambda _host: ["127.0.0.1"])
    monkeypatch.setattr(oauth, "_sessions", {})
    # The fixture deliberately substitutes a loopback TLS server for public DNS.
    # Real production discovery rejects loopback addresses; it is not weakened.
    monkeypatch.setattr(mcp_servers, "reachable_endpoint", lambda _url: None)
    tokens, requests = {"counter": 0}, []

    class Server(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if "oauth-protected-resource" in self.path:
                return self.reply(
                    200,
                    {
                        "resource": origin + "/mcp",
                        "authorization_servers": [origin],
                        "scopes_supported": ["read"],
                    },
                )
            return self.reply(
                200,
                {
                    "issuer": origin,
                    "authorization_endpoint": origin + "/authorize",
                    "token_endpoint": origin + "/token",
                    "response_types_supported": ["code"],
                    "response_modes_supported": ["query", "form_post"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "scopes_supported": ["read", "offline_access"],
                },
            )

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/token":
                fields = {key: value[0] for key, value in parse_qs(data.decode()).items()}
                requests.append(fields)
                if fields["grant_type"] == "authorization_code":
                    challenge = (
                        base64.urlsafe_b64encode(hashlib.sha256(fields["code_verifier"].encode()).digest())
                        .rstrip(b"=")
                        .decode()
                    )
                    if fields["code"] != challenge:
                        return self.reply(400, {"error": "invalid_grant"})
                elif fields["refresh_token"] != tokens["refresh_token"]:
                    return self.reply(400, {"error": "invalid_grant"})
                tokens["counter"] += 1
                tokens.update(
                    access_token=f"access-test-{tokens['counter']}",
                    refresh_token=f"refresh-test-{tokens['counter']}",
                )
                return self.reply(
                    200,
                    {
                        "access_token": tokens["access_token"],
                        "refresh_token": tokens["refresh_token"],
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            if self.path != "/baseline" and self.headers.get("Authorization") != "Bearer " + tokens.get(
                "access_token", ""
            ):
                return self.reply(401, {"error": "not_authenticated"})
            body = json.loads(data)
            if "id" not in body:
                return self.reply(202, {})
            if body["method"] == "tools/call":
                tokens["tool_calls"] = tokens.get("tool_calls", 0) + 1
                return self.reply(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {"content": [{"type": "text", "text": "authenticated"}]},
                    },
                )
            result = (
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "hosted-fixture", "version": "1"},
                }
                if body["method"] == "initialize"
                else {"tools": [{"name": "hosted_read", "inputSchema": {"type": "object"}}]}
            )
            return self.reply(200, {"jsonrpc": "2.0", "id": body["id"], "result": result})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    origin = f"https://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin, tokens, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("mode", ["query", "form_post"])
def test_real_https_oauth_backend_gateway_and_refresh_without_cli(client, stack, tls, mode):  # noqa: F811
    native, control, tmp_path = stack
    origin, tokens, requests = tls
    connected = client.post(
        "/api/mcp-oauth/connect/oauth-test",
        json={
            "transport": "http",
            "authentication": "oauth",
            "url": origin + "/mcp",
            "client_id": "public-client",
            "response_mode": mode,
            "redirect_uri": "http://localhost:0/callback",
        },
    )
    assert connected.status_code == 200
    config = connected.json()["config"]
    node = subprocess.Popen(  # noqa: S603 - production receiver/controller test driver
        [shutil.which("node"), str(ROOT / "tests/agentgateway/oauth-desktop.mjs")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(node.stdout, selectors.EVENT_READ)
    try:
        node.stdin.write(json.dumps({"kind": "begin", "config": config}) + "\n")
        node.stdin.flush()
        buffered = bytearray()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            while b"\n" not in buffered:
                assert selector.select(timeout=15), "Desktop driver stopped responding"
                chunk = os.read(node.stdout.fileno(), 65536)
                assert chunk, "Desktop driver exited: " + node.stderr.read()[-4000:]
                buffered.extend(chunk)
            line, _, buffered = buffered.partition(b"\n")
            message = json.loads(line)
            if message["kind"] == "done":
                if not message["ok"]:
                    # Only synthetic test endpoints/credentials exist here. Print
                    # error reasons, never headers, tokens or MCP session IDs.
                    reasons = re.findall(r'error="([^"]+)"', (tmp_path / "gateway.log").read_text())[-3:]
                    pytest.fail(str(message.get("error")) + "; gateway reasons: " + "; ".join(reasons))
                break
            operation, args = message["operation"], message["args"]
            if operation == "startMcpOAuth":
                response = client.post("/api/mcp-oauth/start/" + args[0], json=args[1])
            elif operation == "pollMcpOAuth":
                response = client.get("/api/mcp-oauth/login/" + args[0])
            elif operation == "forwardMcpOAuth":
                response = client.post("/api/mcp-oauth/callback/" + args[0], json=args[1])
            else:
                response = client.delete("/api/mcp-oauth/login/" + args[0])
            reply = {"id": message["id"], "result": response.json() if response.content else None}
            if response.status_code >= 400:
                reply = {"id": message["id"], "error": response.json().get("detail", "API failed")}
            node.stdin.write(json.dumps(reply) + "\n")
            node.stdin.flush()
        else:
            pytest.fail("HTTPS OAuth bridge timed out")
        assert node.wait(timeout=5) == 0
        assert control.supervisor.sessions == {}  # No CLI was started for hosted OAuth.
        assert requests[0]["resource"] == origin + "/mcp"
        assert client.post("/api/mcp-servers/oauth-test/test").json()["ok"]
        servers = client.get("/api/mcp-servers").json()["servers"]
        assert servers[0]["transport"] == "http" and servers[0]["tool_count"] == 1
        assert servers[0]["oauth"]["mode"] == "https"
        assert "access-test-1" not in json.dumps(servers) and "refresh-test-1" not in json.dumps(servers)
        from nautionette_backend.db import db

        row = db.one("SELECT * FROM mcp_http_oauth")
        value = unseal(row["secret"], namespace=oauth.NAMESPACE)
        value["tokens"]["renew_at"] = time.time() - 1
        db.execute(
            "UPDATE mcp_http_oauth SET secret=?,retry_at=0 WHERE name=?",
            (seal(value, namespace=oauth.NAMESPACE), row["name"]),
        )
        asyncio.run(oauth.refresh_once())
        assert tokens["counter"] == 2 and requests[-1]["grant_type"] == "refresh_token"
        assert client.post("/api/mcp-servers/oauth-test/test").json()["ok"]
        assert client.delete("/api/mcp-servers/oauth-test").status_code == 200
        assert db.query("SELECT * FROM mcp_http_oauth") == []
    finally:
        selector.close()
        if node.poll() is None:
            node.kill()
        node.communicate(timeout=5)


def test_agent_started_before_sign_in_dispatches_once_on_fresh_federation(
    client,
    stack,  # noqa: F811
    tls,
    live,
    monkeypatch,
):
    from nautionette_backend.config import settings

    from .test_mcp_http_oauth import begin, forward

    origin, tokens, requests = tls
    native, _, _ = stack
    tokens["access_token"] = "pre-login-credential"
    asyncio.run(
        native.put_config_resources(
            "mcp.target", [mcp_servers.target_value("example", origin + "/mcp", tokens["access_token"])]
        )
    )
    for _ in range(50):
        try:
            if asyncio.run(native.mcp_tools()):
                break
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.1)
    else:
        pytest.fail("Pre-login federation did not become ready")
    module = (ROOT / "images/agent-sets/default/extensions/nautionette/mcp-transport.ts").as_uri()
    script = f"""
      import {{ createMcpTransport }} from {json.dumps(module)};
      const transport = createMcpTransport({json.dumps(settings.mcp_url)});
      await transport.connect();
      console.log('ready');
      await new Promise(resolve => process.stdin.once('data', resolve));
      const result = await transport.rpcFresh('tools/call', {{
        name: 'example_hosted_read', arguments: {{}}
      }});
      console.log(JSON.stringify(result));
      await transport.close();
      process.stdin.destroy();
    """
    agent = subprocess.Popen(  # noqa: S603 - real production transport with synthetic credentials only
        [shutil.which("node"), "--input-type=module", "-e", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(agent.stdout, selectors.EVENT_READ)
    try:
        assert selector.select(timeout=15), "Pre-login MCP handshake timed out"
        assert agent.stdout.readline().strip() == "ready", agent.stderr.read()[-2000:]
        identifier, auth, _ = begin(client, url=origin + "/mcp")
        # Production has several federated targets, not only the OAuth target.
        asyncio.run(
            native.put_config_resources(
                "mcp.target", [mcp_servers.target_value("baseline", origin + "/baseline", "")]
            )
        )
        challenge = parse_qs(urlsplit(auth["authorization_url"]).query)["code_challenge"][0]
        assert forward(client, identifier, auth, code=challenge).status_code == 200
        original = live.gateway.mcp_tools
        checks = []

        async def warming(url=None, *args, **kwargs):
            if url is None:
                checks.append(True)
                if len(checks) == 1:
                    return []  # Config acknowledged; federation watch not yet caught up.
            return await original(url, *args, **kwargs)

        monkeypatch.setattr(live.gateway, "mcp_tools", warming)
        assert client.get("/api/mcp-oauth/login/" + identifier).json()["status"] == "gateway_pending"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if client.get("/api/mcp-oauth/login/" + identifier).json()["status"] == "complete":
                break
            time.sleep(0.1)
        else:
            from nautionette_backend.db import db

            row = db.one("SELECT * FROM mcp_http_oauth WHERE name='example'")
            value = unseal(row["secret"], namespace=oauth.NAMESPACE)
            names = [tool["name"] for tool in asyncio.run(native.mcp_tools())]
            pytest.fail(f"Federation never ready: isolated={value['tool_names']}, federated={names}")
        stdout, stderr = agent.communicate("dispatch\n", timeout=20)
        assert agent.returncode == 0, stderr[-2000:]
        assert json.loads(stdout)["content"][0]["text"] == "authenticated"
        assert tokens["tool_calls"] == 1
        assert len(requests) == 1  # Readiness checking never exchanges the code again.
    finally:
        selector.close()
        if agent.poll() is None:
            agent.kill()
        agent.communicate(timeout=5)
