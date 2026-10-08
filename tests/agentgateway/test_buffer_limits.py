"""Keep large chat payloads off the gateway's upstream 2 MiB default.

Static guards always run. Runtime checks use an opt-in gateway binary and a local
fake provider only: no credentials, external model calls or Docker required.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml
from nautionette_backend.chat_attachments import MAX_ATTACHMENTS, MAX_FILE_BYTES

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "services/agentgateway/config/config.yaml"
MIB = 1024 * 1024
BUFFER_BYTES = 256 * MIB


def test_global_buffer_has_room_for_large_chats_and_base64_attachments():
    config = yaml.safe_load(CONFIG.read_text())
    assert config["frontendPolicies"]["http"]["maxBufferSize"] == BUFFER_BYTES
    # Current attachments plus bounded history, with room for instructions,
    # schemas, tool output, JSON overhead and multibyte text as well as images.
    encoded_images = 2 * MAX_ATTACHMENTS * ((MAX_FILE_BYTES + 2) // 3 * 4)
    assert encoded_images + 32 * MIB < BUFFER_BYTES


class EchoProvider(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        content = request["messages"][-1]["content"]
        response = json.dumps(
            {
                "id": "chat-buffer-test",
                "object": "chat.completion",
                "model": "buffer-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def buffered_gateway(tmp_path):
    binary = os.environ.get("NAUTIONETTE_TEST_GATEWAY_BINARY")
    if not binary:
        pytest.skip("Set NAUTIONETTE_TEST_GATEWAY_BINARY to run real buffer boundary checks")
    # Isolate boundary probes so asynchronous logging / allocator retention from
    # several 256 MiB requests cannot accumulate in a small test sandbox.
    directory = tmp_path
    provider = ThreadingHTTPServer(("127.0.0.1", 0), EchoProvider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    config = yaml.safe_load(CONFIG.read_text())
    # Start from the shipped config, replacing only storage/listen addresses and
    # upstreams. The actual global frontend policy must survive runtime merging.
    config["config"].update(
        database={"url": f"sqlite:///{directory}/gateway.db"},
        adminAddr="127.0.0.1:0",
        readinessAddr="127.0.0.1:0",
        statsAddr="127.0.0.1:0",
    )
    config["gateways"]["default"]["port"] = port
    config["mcp"]["targets"] = []
    config["llm"]["models"] = [
        {
            "name": "buffer-test",
            "provider": "openAI",
            "params": {
                "baseUrl": f"http://127.0.0.1:{provider.server_port}/v1",
                "apiKey": "local-test-only",
            },
        }
    ]
    config_file = directory / "config.yaml"
    config_file.write_text(yaml.safe_dump(config))
    log_file = directory / "gateway.log"
    process = None
    try:
        with (
            log_file.open("w") as log,
            httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False) as client,
        ):
            process = subprocess.Popen(  # noqa: S603 - explicitly supplied opt-in test binary
                [str(Path(binary).resolve()), "-f", str(config_file)], stdout=log, stderr=log
            )
            for _ in range(100):
                assert process.poll() is None, log_file.read_text()
                try:
                    response = client.get("/api/runtime")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(f"gateway did not start: {log_file.read_text()}")
            yield client
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


def padded_json(size):
    """Stream exact byte sizes without allocating another 256 MiB in the client.

    JSON whitespace counts toward transport buffering without making a huge
    parsed string. Omitting the model guarantees these probes cannot invoke one.
    """
    yield b"{}"
    remaining = size - 2
    chunk = b" " * (64 * 1024)
    while remaining:
        length = min(remaining, len(chunk))
        yield chunk[:length]
        remaining -= length


@pytest.mark.slow
@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/responses", "/v1/messages"])
@pytest.mark.parametrize("chunked", [False, True])
def test_all_llm_apis_accept_more_than_the_old_limit(buffered_gateway, path, chunked):
    size = 2 * MIB + 1
    headers = {"Content-Type": "application/json"}
    if not chunked:
        headers["Content-Length"] = str(size)
    response = buffered_gateway.post(path, content=padded_json(size), headers=headers)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "missing_model"


@pytest.mark.slow
@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("extra_byte", [False, True])
def test_exact_new_buffer_boundary_remains_bounded(buffered_gateway, chunked, extra_byte):
    size = BUFFER_BYTES + int(extra_byte)
    headers = {"Content-Type": "application/json"}
    if not chunked:
        headers["Content-Length"] = str(size)
    response = buffered_gateway.post("/v1/chat/completions", content=padded_json(size), headers=headers)
    assert response.status_code == (413 if extra_byte else 400), response.text
    assert response.json()["error"]["code"] == ("request_body_too_large" if extra_byte else "missing_model")


@pytest.mark.slow
def test_large_request_and_response_reach_a_local_provider_without_truncation(buffered_gateway):
    content = "large chat payload " * (256 * 1024)
    assert len(content.encode()) > 2 * MIB
    response = buffered_gateway.post(
        "/v1/chat/completions",
        json={"model": "buffer-test", "messages": [{"role": "user", "content": content}], "stream": False},
    )
    assert response.status_code == 200, response.text[:1000]
    assert response.json()["choices"][0]["message"]["content"] == content
