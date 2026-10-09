"""Enable upstream tracing only when explicitly configured, using standard OTel env.

The read-only baseline and persisted runtime gateway resources remain untouched.
"""

from __future__ import annotations

import json
import os
import sys
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
    os.execv("/app/agentgateway", ["/app/agentgateway", *args])  # noqa: S606 - fixed upstream binary


if __name__ == "__main__":
    main()
