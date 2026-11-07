"""Synthetic CLI exercising the real supervisor, browser hook and token cache.

No provider account or real credentials. 127.0.0.2 can emulate the gateway's
separate network namespace while the desktop binds localhost on the same port.
"""

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode

mode = sys.argv[1]
home = Path(os.environ["HOME"])
cache = home / "synthetic-cli-cache.json"

if mode == "setup":
    assert "CLI_OAUTH_TOKEN" not in os.environ and "APP_TOKEN" not in os.environ
    assert "INTERNAL_TOKEN" not in os.environ and "OPENAI_API_KEY" not in os.environ
    print("Setup done", flush=True)
elif mode == "mcp":
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        if (
            not cache.exists()
            or json.loads(cache.read_text()).get("environment_hash")
            != hashlib.sha256(os.environ.get("LITERAL_SECRET", "").encode()).hexdigest()
        ):
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32000, "message": "Login required"},
                    }
                ),
                flush=True,
            )
            continue
        result = (
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "oauth-fixture", "version": "1"},
            }
            if request["method"] == "initialize"
            else {"tools": [{"name": "synthetic_read", "inputSchema": {"type": "object"}}]}
        )
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
elif mode == "native":
    print("Native-broker flow only; no loopback URL", flush=True)
elif mode in {"login", "deny", "retry", "unowned", "stdout", "hang"}:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    callbacks = []
    state = secrets.token_urlsafe(32)
    response_mode = os.environ.get("RESPONSE_MODE", "form_post")

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def callback(self, fields):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Return to the app")
            if fields.get("state") != [state]:
                return
            callbacks.append(fields)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def do_POST(self):
            self.callback(parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()))

        def do_GET(self):
            self.callback(parse_qs(self.path.partition("?")[2]))

    for attempt in range(2 if mode == "retry" else 1):
        state = secrets.token_urlsafe(32)
        server = ThreadingHTTPServer((os.environ.get("LOOPBACK_HOST", "127.0.0.1"), 0), Callback)
        port = (
            int(os.environ.get("UNOWNED_PORT", server.server_port))
            if mode == "unowned"
            else server.server_port
        )
        url = "https://login.example.test/authorize?" + urlencode(
            {
                "redirect_uri": f"http://localhost:{port}",
                "state": state,
                "response_type": "code",
                "response_mode": response_mode,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        if mode == "stdout":
            print("Open this URL: " + url, flush=True)
        else:
            subprocess.run([os.environ["BROWSER"], url], check=True)  # noqa: S603 - fixture browser hook
        server.serve_forever()
        server.server_close()
        fields = callbacks[-1]
        if "error" in fields or mode == "deny":
            print("Login failed: synthetic provider denial; do-not-return-this-private-detail", flush=True)
            sys.exit(0)  # Like WorkIQ: exit 0 alone must not mean authenticated.
        if mode == "retry" and attempt == 0:
            print("AADSTS9002313 synthetic rejection", flush=True)
            continue
        # Synthetic authority code is tied to the PKCE challenge. The verifier
        # never leaves the CLI; the bridge must preserve the callback exactly.
        if fields.get("code") != [challenge]:
            print("Login failed: synthetic code/PKCE mismatch", flush=True)
            sys.exit(0)
        home.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {
                    "access_token": "synthetic-private-token",
                    "refresh_token": "synthetic-private-refresh",
                    "environment_hash": hashlib.sha256(
                        os.environ.get("LITERAL_SECRET", "").encode()
                    ).hexdigest(),
                }
            )
        )
        cache.chmod(0o600)
        print("Logged in as synthetic-test-account", flush=True)
        break
else:
    raise SystemExit(1)
