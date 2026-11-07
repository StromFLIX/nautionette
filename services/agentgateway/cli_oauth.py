"""Private, authenticated CLI-owned OAuth supervisor beside agentgateway.

Trusted login commands run with the same HOME/environment as their stdio MCP.
A browser hook captures code+PKCE authorization URLs. Only callbacks for a
captured, still CLI-owned loopback listener may be relayed. There is no generic
localhost proxy, token exchange, credential log or unauthenticated control API.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

LIMIT = 65536
TTL = 600
BROWSER = Path(__file__).with_name("cli_oauth_browser.py")
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/data/mcp"}


class Problem(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def arguments(value) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 256
        or any(not isinstance(v, str) or len(v) > 8192 or "\0" in v for v in value)
    ):
        raise Problem(400, "Arguments must be a bounded JSON array of strings")
    return value


def configuration(value: dict) -> dict:
    command = value.get("command", "")
    if (
        not isinstance(command, str)
        or len(command) > 1024
        or not re.fullmatch(r"(?:/[A-Za-z0-9._/-]+|[A-Za-z0-9._-]+)", command)
    ):
        raise Problem(400, "Invalid CLI executable")
    env = value.get("env", {})
    if (
        not isinstance(env, dict)
        or len(env) > 128
        or any(
            not isinstance(k, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", k)
            or not isinstance(v, str)
            or len(v) > 32768
            or "\0" in v
            for k, v in env.items()
        )
    ):
        raise Problem(400, "Invalid CLI environment")
    setup = value.get("setup_args", [])
    if not isinstance(setup, list) or len(setup) > 4:
        raise Problem(400, "Use at most four setup commands")
    provider = value.get("provider", "generic")
    if provider not in {"generic", "workiq"}:
        raise Problem(400, "Unknown CLI OAuth provider adapter")
    return {
        "command": command,
        "login_args": arguments(value.get("login_args")),
        "setup_args": [arguments(args) for args in setup],
        "env": {**ENV, **env},
        "provider": provider,
    }


def authorization(value: str) -> dict:
    try:
        u = urlsplit(value)
        if (
            len(value) > 16384
            or u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.fragment
            or u.port not in (None, 443)
            or "\\" in value
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError
        q = parse_qs(u.query, keep_blank_values=True, max_num_fields=128)
        if any(k in q for k in ["access_token", "refresh_token", "client_secret", "code", "id_token"]):
            raise ValueError
        if any(
            len(q.get(k, [])) != 1
            for k in ["redirect_uri", "state", "response_type", "code_challenge", "code_challenge_method"]
        ):
            raise ValueError
        redirect = q["redirect_uri"][0]
        r = urlsplit(redirect)
        state = q["state"][0]
        mode = q.get("response_mode", ["query"])
        if (
            r.scheme != "http"
            or r.hostname not in {"localhost", "127.0.0.1", "::1"}
            or r.port is None
            or not 1024 <= r.port <= 65535
            or r.username
            or r.password
            or r.query
            or r.fragment
            or "\\" in redirect
            or any(ord(c) < 33 for c in redirect)
            or not 16 <= len(state) <= 8192
            or not state.isascii()
            or q["response_type"] != ["code"]
            or mode not in (["query"], ["form_post"])
            or q["code_challenge_method"] != ["S256"]
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", q["code_challenge"][0])
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError) as exc:
        raise Problem(400, "CLI did not request supported code/PKCE OAuth with a loopback callback") from exc
    return {
        "id": secrets.token_hex(16),
        "authorization_url": value,
        "redirect_uri": redirect,
        "state": state,
        "response_mode": mode[0],
        "port": r.port,
    }


def group_listeners(process: subprocess.Popen) -> list[tuple[int, str]]:
    """Identify sockets held by this login's process group, not unrelated ports.

    Linux-only: the helper lives in the Linux gateway, not the desktop app.
    Listener ownership is checked both at capture and immediately before relay.
    """
    if process.poll() is not None:
        return []
    inodes = set()
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            stat = (directory / "stat").read_text().rsplit(") ", 1)[1].split()
            if int(stat[2]) != process.pid:
                continue
            for fd in (directory / "fd").iterdir():
                try:
                    link = os.readlink(fd)
                    if link.startswith("socket:["):
                        inodes.add(link[8:-1])
                except OSError:
                    pass
        except (OSError, ValueError, IndexError):
            continue
    found = []
    for name, family in [("tcp", socket.AF_INET), ("tcp6", socket.AF_INET6)]:
        for line in Path("/proc/net", name).read_text().splitlines()[1:]:
            columns = line.split()
            if columns[3] != "0A" or columns[9] not in inodes:
                continue
            address, port = columns[1].split(":")
            raw = bytes.fromhex(address)
            raw = (
                raw[::-1]
                if family == socket.AF_INET
                else b"".join(raw[i : i + 4][::-1] for i in range(0, 16, 4))
            )
            host = socket.inet_ntop(family, raw)
            if ipaddress.ip_address(host).is_loopback:
                found.append((int(port, 16), host))
    return found


@dataclass
class Capture:
    public: dict
    used: bool = False
    digest: str = ""
    outcome: str = ""


@dataclass
class Login:
    id: str
    config: dict
    expires: float
    status: str = "starting"
    message: str = "Starting trusted CLI login; first-time package downloads can take a minute."
    process: subprocess.Popen | None = None
    captures: list[Capture] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)
    forwarded_code: bool = False
    cli_failed: bool = False
    workiq_success: bool = False
    error_code: str = ""

    def fail(self, message: str):
        self.status, self.message = "failed", message
        kill_group(self.process)

    def public(self) -> dict:
        with self.lock:
            result = {
                "status": self.status,
                "message": self.message,
                "expires_in": max(0, int(self.expires - time.monotonic())),
            }
            if self.status == "authorizing" and self.captures and not self.captures[-1].used:
                result["authorization"] = self.captures[-1].public
            return result

    def capture(self, url: str) -> None:
        info = authorization(url)
        with self.lock:
            if self.status in {"cancelled", "expired", "failed", "complete"}:
                raise Problem(410, "CLI login is no longer active")
            if not self.process or not any(port == info["port"] for port, _ in group_listeners(self.process)):
                raise Problem(400, "Redirect port is not a loopback listener owned by the login CLI")
            if self.captures and self.captures[-1].public["authorization_url"] == url:
                return
            if self.captures and not self.captures[-1].used:
                raise Problem(409, "Another callback is already pending")
            if len(self.captures) >= 8:
                raise Problem(400, "CLI requested too many login attempts; start again")
            self.captures.append(Capture(info))
            self.status, self.message = (
                "authorizing",
                f"Sign in in your desktop browser; detected local port {info['port']}.",
            )

    def line(self, line: str) -> None:
        # No raw CLI output is logged, persisted or returned. Only static verdicts
        # and allowlisted error identifiers leave this process.
        with self.lock:
            if "Login failed:" in line or line.startswith("ERROR:"):
                self.cli_failed = True
            match = re.search(r"\bAADSTS[0-9]{4,8}\b", line)
            if match:
                self.error_code = match[0]
            # WorkIQ's login handler prints this only after token acquisition.
            if (
                line.startswith("Logged in as ")
                or line == "Login succeeded but could not determine account username."
            ):
                self.workiq_success = True
        for value in re.findall(r"https://[^\s<>\"']+", line):
            try:
                self.capture(value)
            except Problem:
                pass

    def terminate(self, status="cancelled") -> None:
        with self.lock:
            self.status, self.message = (
                status,
                "CLI login expired; start again." if status == "expired" else "CLI login cancelled.",
            )
            process = self.process
        kill_group(process)

    def callback(self, payload: dict) -> dict:
        fields = payload.get("fields")
        if (
            not isinstance(fields, dict)
            or len(fields) > 32
            or any(
                not isinstance(k, str) or len(k) > 128 or not isinstance(v, str) or len(v) > 16384
                for k, v in fields.items()
            )
            or len(json.dumps(fields)) > LIMIT
            or any(k in fields for k in ["access_token", "refresh_token", "id_token"])
        ):
            raise Problem(400, "Invalid OAuth callback fields")
        with self.lock:
            capture = next((c for c in self.captures if c.public["id"] == payload.get("capture_id")), None)
            if (
                not capture
                or not isinstance(fields.get("state"), str)
                or not fields["state"].isascii()
                or not secrets.compare_digest(fields["state"], capture.public["state"])
            ):
                raise Problem(400, "OAuth callback state or capture did not match")
            if bool(fields.get("code")) == bool(fields.get("error")):
                raise Problem(400, "OAuth callback must contain either a code or an error")
            method = "POST" if capture.public["response_mode"] == "form_post" else "GET"
            if payload.get("method") != method:
                raise Problem(400, "OAuth callback method does not match response mode")
            digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
            if capture.used:
                if capture.digest != digest:
                    raise Problem(409, "This callback was already submitted with different fields")
                return {"status": capture.outcome}
            if self.status in {"cancelled", "expired", "failed", "complete"}:
                raise Problem(410, "CLI login is no longer active")
            choices = (
                [host for port, host in group_listeners(self.process) if port == capture.public["port"]]
                if self.process
                else []
            )
            if not choices:
                raise Problem(410, "CLI callback listener is no longer owned by this login")
            # Fence before network I/O. A lost result never repeats a code or
            # blindly retries a possibly delivered callback on another socket.
            capture.used, capture.digest, capture.outcome = True, digest, "uncertain"
            self.status, self.message = (
                "waiting_cli",
                "Callback forwarded; waiting for the CLI to finish authentication.",
            )
            redirect = urlsplit(capture.public["redirect_uri"])
            host = choices[0]
        connection = http.client.HTTPConnection(host, redirect.port, timeout=10)
        path = redirect.path or "/"
        body = urlencode(fields)
        headers = {"Host": redirect.netloc, "Content-Type": "application/x-www-form-urlencoded"}
        if method == "GET":
            path += "?" + body
            body = None
        try:
            connection.request(method, path, body=body, headers=headers)
            reply = connection.getresponse()
            # Never forward the CLI's callback HTML/headers, follow redirects,
            # or return any upstream credential-containing text.
            reply.read(LIMIT + 1)
            if not 200 <= reply.status < 300:
                raise OSError("callback refused")
        except (OSError, http.client.HTTPException):
            with self.lock:
                self.fail(
                    "Callback delivery is uncertain or was refused; start a fresh login. "
                    "It will not be replayed."
                )
            return {"status": "uncertain"}
        finally:
            connection.close()
        with self.lock:
            capture.outcome = "forwarded"
            if fields.get("code"):
                self.forwarded_code = True
            else:
                self.cli_failed = True
            return {"status": "forwarded"}


def kill_group(process):
    if process is None:
        return
    # The process group also covers npx -> native executable -> browser hooks.
    for sig in [signal.SIGTERM, signal.SIGKILL]:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        if sig == signal.SIGTERM:
            time.sleep(0.1)


class Supervisor:
    def __init__(self, ttl=TTL):
        self.ttl, self.sessions, self.lock = ttl, {}, threading.RLock()

    def start(self, value: dict) -> dict:
        config = configuration(value)
        with self.lock:
            self.expire()
            active = []
            for existing in self.sessions.values():
                with existing.lock:
                    if existing.status not in {"complete", "failed", "cancelled", "expired"}:
                        active.append(os.path.realpath(existing.config["env"]["HOME"]))
            if len(active) >= 16 or len(self.sessions) >= 64:
                raise Problem(429, "Too many active or recent CLI logins; cancel an active login or wait")
            # Compare canonical paths, but preserve the exact HOME passed to the
            # CLI/MCP. Taking the login lock also waits for failed-group teardown.
            if os.path.realpath(config["env"]["HOME"]) in active:
                raise Problem(409, "A CLI login is already active for this HOME; finish or cancel it first")
            identifier = secrets.token_hex(32)
            login = Login(identifier, config, time.monotonic() + self.ttl)
            self.sessions[identifier] = login
            threading.Thread(target=self.run, args=(login,), daemon=True).start()
            return {"id": identifier, **login.public()}

    def get(self, identifier: str) -> Login:
        with self.lock:
            self.expire()
            if identifier not in self.sessions:
                raise Problem(410, "CLI login was lost on restart or expired; start again")
            return self.sessions[identifier]

    def expire(self):
        now = time.monotonic()
        for key in list(self.sessions):
            login = self.sessions[key]
            if now >= login.expires:
                if login.status not in {"complete", "failed", "expired", "cancelled"}:
                    login.terminate("expired")
                if now > login.expires + 60:
                    del self.sessions[key]

    def close(self):
        with self.lock:
            for login in self.sessions.values():
                login.terminate()

    def run(self, login: Login):
        with tempfile.TemporaryDirectory(prefix="nautionette-cli-oauth-") as root:
            directory = Path(root)
            sock = socket.socket(socket.AF_UNIX)
            path = directory / "browser.sock"
            sock.bind(str(path))
            os.chmod(path, 0o600)
            sock.listen(4)
            sock.settimeout(0.25)
            for name in ["xdg-open", "browser"]:
                (directory / name).symlink_to(BROWSER)
            env = {
                **login.config["env"],
                "PATH": f"{root}:{login.config['env']['PATH']}",
                "BROWSER": str(directory / "browser"),
                "NAUTIONETTE_CLI_CAPTURE_SOCKET": str(path),
            }
            stopped = threading.Event()

            def captures():
                while not stopped.is_set():
                    try:
                        connection, _ = sock.accept()
                    except (TimeoutError, OSError):
                        continue
                    with connection:
                        try:
                            connection.settimeout(5)
                            peer = struct.unpack(
                                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                            )[0]
                            with login.lock:
                                if not login.process or os.getpgid(peer) != login.process.pid:
                                    raise Problem(403, "Unrelated browser hook")
                            data = bytearray()
                            while len(data) <= LIMIT:
                                part = connection.recv(4096)
                                if not part:
                                    break
                                data.extend(part)
                            if len(data) > LIMIT:
                                raise Problem(413, "Browser URL exceeded limit")
                            login.capture(json.loads(data)["url"])
                            connection.sendall(b"1")
                        except (Problem, OSError, ValueError, KeyError, TypeError):
                            with login.lock:
                                if login.status not in {"cancelled", "expired", "failed", "complete"}:
                                    login.fail(
                                        "CLI browser request is unsupported or its callback port "
                                        "is not CLI-owned. Use a loopback code/PKCE login."
                                    )

            listener = threading.Thread(target=captures, daemon=True)
            listener.start()
            try:
                Path(env["HOME"]).mkdir(parents=True, exist_ok=True, mode=0o700)
                for args in [*login.config["setup_args"], login.config["login_args"]]:
                    with login.lock:
                        if login.status in {"cancelled", "expired", "failed"}:
                            return
                        login.process = subprocess.Popen(  # noqa: S603 - authenticated trusted CLI, argv only
                            [login.config["command"], *args],
                            env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        process = login.process
                    buffer = bytearray()
                    while True:
                        chunk = process.stdout.read1(4096)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        while b"\n" in buffer:
                            line, _, rest = buffer.partition(b"\n")
                            buffer = bytearray(rest)
                            login.line(line.decode("utf-8", "replace").strip())
                        if len(buffer) > LIMIT:
                            buffer.clear()
                    if buffer:
                        login.line(buffer.decode("utf-8", "replace").strip())
                    code = process.wait()
                    kill_group(process)  # Remove orphan descendants, not unrelated MCPs.
                    with login.lock:
                        if login.status in {"cancelled", "expired", "failed"}:
                            return
                        if code != 0 or login.cli_failed:
                            login.fail(
                                "CLI authentication failed"
                                + (f" ({login.error_code})" if login.error_code else "")
                                + "; check tenant policy, consent, licensing or CLI setup, "
                                "then sign in again."
                            )
                            return
                with login.lock:
                    if (login.config["provider"] == "workiq" and login.workiq_success) or (
                        login.config["provider"] == "generic" and login.forwarded_code
                    ):
                        login.status, login.message = (
                            "complete",
                            "CLI reports login complete; Nautionette must still verify the MCP connection.",
                        )
                    else:
                        login.fail(
                            "CLI did not confirm a bridged login. A cached/native-broker login or "
                            "unsupported flow is not proof that this bridge worked."
                        )
            except (OSError, ValueError):
                with login.lock:
                    login.fail("Could not run the CLI; check its executable, arguments and required runtime.")
            finally:
                stopped.set()
                sock.close()
                listener.join(timeout=1)
                kill_group(login.process)
                login.process = None  # Completed jobs must not retain a reusable process-group ID.
                # Auth URLs remain only for this short-lived session. Never keep
                # command environment values once the process has finished.
                login.config["env"] = {"HOME": login.config["env"]["HOME"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "NautionetteCLI/1"

    def log_message(self, *_args):
        pass

    def response(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        token = self.headers.get("Authorization", "")
        if (
            not self.server.control_token
            or not token.isascii()
            or not secrets.compare_digest(token, f"Bearer {self.server.control_token}")
        ):
            return self.response(401, {"message": "CLI OAuth control authentication required"})
        try:
            path = urlsplit(self.path).path.split("/")
            if self.command == "GET" and path == ["", "health"]:
                return self.response(200, {"version": 1, "available": True})
            data = {}
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    not 0 < length <= LIMIT
                    or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
                ):
                    raise Problem(400, "Invalid CLI control body")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise Problem(400, "Expected JSON object")
            if path == ["", "sessions"] and self.command == "POST":
                return self.response(200, self.server.supervisor.start(data))
            if (
                len(path) in {3, 4}
                and path[:2] == ["", "sessions"]
                and re.fullmatch(r"[a-f0-9]{64}", path[2])
            ):
                login = self.server.supervisor.get(path[2])
                if len(path) == 3 and self.command == "GET":
                    return self.response(200, login.public())
                if len(path) == 3 and self.command == "DELETE":
                    login.terminate()
                    return self.response(200, {"status": "cancelled"})
                if len(path) == 4 and path[3] == "callback" and self.command == "POST":
                    return self.response(200, login.callback(data))
            raise Problem(404, "Unknown CLI control operation")
        except Problem as exc:
            return self.response(exc.status, {"message": exc.message})
        except (ValueError, OSError, TypeError):
            return self.response(400, {"message": "Invalid CLI control request"})

    do_GET = do_POST = do_DELETE = dispatch


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token: str, supervisor: Supervisor | None = None):
        self.control_token, self.supervisor = token, supervisor or Supervisor()
        super().__init__(address, Handler)
        self.timeout = 1

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(15)
        return sock, address


def main():
    token = os.environ.get("CLI_OAUTH_TOKEN", "").strip()
    if not token:
        raise SystemExit("CLI OAuth control requires CLI_OAUTH_TOKEN; refusing open access")
    os.umask(0o077)
    # Private Docker network only; mandatory authentication, no published port.
    server = ControlServer(("0.0.0.0", 4001), token)  # noqa: S104
    stop = threading.Event()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set():
            server.handle_request()
            with server.supervisor.lock:
                server.supervisor.expire()
    finally:
        server.supervisor.close()
        server.server_close()


if __name__ == "__main__":
    main()
