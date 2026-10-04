"""Opt-in real sandbox startup, gateway DNS and outbound HTTPS checks."""

import json
import os
import uuid
from contextlib import ExitStack
from types import SimpleNamespace

import docker
import pytest
from nautionette_docker_broker import agent_run

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1",
    reason="Set NAUTIONETTE_DOCKER_TESTS=1 to test real Docker sandbox networking",
)


def test_new_sandboxes_have_gateway_and_internet_from_start(monkeypatch):
    client = docker.from_env()
    image = "node:24-bookworm-slim"
    prefix = "nautionette-network-test-" + uuid.uuid4().hex[:12]
    with ExitStack() as cleanup:
        cleanup.callback(client.close)
        volume = client.volumes.create()
        cleanup.callback(volume.remove)
        network = client.networks.create(prefix)
        cleanup.callback(network.remove)
        monkeypatch.setattr(agent_run, "WORKFLOWS_VOLUME", volume.name)
        monkeypatch.setattr(agent_run, "AGENT_NETWORK", network.name)
        monkeypatch.setattr(agent_run, "TARGET_NETWORK", network.name)
        monkeypatch.setattr(agent_run.images, "discovered_agent_sets", lambda: ["default"])
        monkeypatch.setattr(agent_run.images, "has_image", lambda tag: True)
        monkeypatch.setattr(agent_run.images, "image_tag", lambda name: image)
        gateway = client.containers.run(
            image,
            command=[
                "node",
                "-e",
                'require("http").createServer((request, response) => '
                'response.end("ok")).listen(8080, "0.0.0.0", () => console.log("ready"))',
            ],
            name=prefix + "-gateway",
            detach=True,
            network=network.name,
        )
        cleanup.callback(gateway.remove, force=True)
        logs = gateway.logs(stream=True)
        try:
            assert b"ready" in next(logs)
        finally:
            logs.close()

        def create(tag, **kwargs):
            # Exercise the production run path with a deterministic probe instead of Pi.
            script = (
                "(async () => { "
                f'for (const url of ["http://{prefix}-gateway:8080", "https://example.com"]) {{ '
                "const response = await fetch(url, {signal: AbortSignal.timeout(15000)}); "
                "if (!response.ok) throw new Error(`${url}: ${response.status}`); } "
                'console.log(JSON.stringify({type: "result", ok: true})); '
                "})().catch(error => { console.error(error); process.exit(1); });"
            )
            container = client.containers.create(tag, command=["node", "-e", script], **kwargs)
            return container

        monkeypatch.setattr(
            agent_run.daemon,
            "client",
            lambda: SimpleNamespace(containers=SimpleNamespace(create=create)),
        )
        for job in ({"chat_id": prefix, "turn_id": "first"}, {"chat_id": prefix, "turn_id": "next"}, {}):
            events = [json.loads(event) for event in agent_run.run(job)]
            assert not any(event["type"] == "error" for event in events), events
            assert {"type": "result", "ok": True} in events
