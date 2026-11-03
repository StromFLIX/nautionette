"""CI-only broker host with a private authenticated backend-adapter stub.

This tests the real Rust SDK -> isolated network -> broker -> backend round trip.
It is mounted only by test_miniapps_docker.py, never copied into production images.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn


class BackendStub(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("X-Internal-Token") != "integration-test-internal":
            self.send_error(401)
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if (
            self.path != "/internal/miniapps/probe/capability"
            or payload.get("revision") != 1
            or payload["request"].get("kind") != "mcp"
            or payload["request"].get("tool") != "probe_transport"
        ):
            self.send_error(403)
            return
        body = json.dumps({"sdk_callback": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    # Reproduce Coolify's build-argument injection before importing the broker.
    source = Path("/miniapp-sdk/Dockerfile")
    source.write_text(source.read_text() + "\nARG COOLIFY_RESOURCE_UUID\nARG APP_TOKEN\n")
    server = ThreadingHTTPServer(("127.0.0.1", 18080), BackendStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    uvicorn.run("nautionette_docker_broker.main:app", host="0.0.0.0", port=9100, lifespan="off")  # noqa: S104
