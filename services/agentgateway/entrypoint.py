"""Enable upstream tracing only when explicitly configured, using standard OTel env.

The read-only baseline and persisted runtime gateway resources remain untouched.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def tracing_config(env: dict[str, str]) -> dict | None:
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint or env.get("OTEL_SDK_DISABLED", "true").lower() == "true":
        return None
    return {
        "otlpEndpoint": endpoint,
        "otlpProtocol": "http",
        "randomSampling": True,
        "clientSampling": True,
        "fields": {
            "remove": [
                "http.path",
                "jwt.sub",
                "mcp.resource.uri",
                "mcp.session.id",
                "src.identity",
                "src.addr",
                "error",
            ]
        },
    }


def main() -> None:
    args = sys.argv[1:]
    config = tracing_config(dict(os.environ))
    if config is not None:
        index = args.index("-f") + 1
        source = Path(args[index]).read_text()
        if source.count("config:\n") != 1:
            raise ValueError("Expected one top-level config block in gateway baseline")
        source = source.replace("config:\n", "config:\n  tracing: " + json.dumps(config) + "\n", 1)
        path = Path("/tmp/nautionette-gateway-config.yaml")  # noqa: S108 - private container
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(source)
        args[index] = str(path)
    # A private helper owns short-lived CLI auth processes. Supervise both so
    # a helper crash is visible/restarted, and container shutdown kills logins.
    os.umask(0o077)
    commands = [["/app/agentgateway", *args]]
    if os.environ.get("CLI_OAUTH_TOKEN", "").strip():
        commands.append([sys.executable, "/app/cli_oauth.py"])
    children = [subprocess.Popen(command, start_new_session=True) for command in commands]  # noqa: S603
    stopping = False

    def stop(*_args):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, stop)
    try:
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.2)
        status = (
            0
            if stopping
            else next((child.returncode or 1 for child in children if child.poll() is not None), 1)
        )
    finally:
        stop()
        deadline = time.monotonic() + 15
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    raise SystemExit(status if status >= 0 else 1)


if __name__ == "__main__":
    main()
