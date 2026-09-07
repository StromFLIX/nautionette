import os
import uuid
from contextlib import ExitStack

import docker
import pytest
from nautionette_docker_broker import agent_run

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1",
    reason="Set NAUTIONETTE_DOCKER_TESTS=1 to test real Docker network isolation",
)


def test_live_chat_egress_requires_approval(monkeypatch):
    client = docker.from_env()
    image = "node:24-bookworm-slim"
    prefix = "nautionette-internet-test-" + uuid.uuid4().hex[:12]
    with ExitStack() as cleanup:
        cleanup.callback(client.close)
        isolated = client.networks.create(prefix + "-isolated", internal=True)
        cleanup.callback(isolated.remove)
        egress = client.networks.create(prefix + "-egress")
        cleanup.callback(egress.remove)
        monkeypatch.setattr(agent_run.daemon, "client", lambda: client)
        monkeypatch.setattr(agent_run, "AGENT_EGRESS_NETWORK", egress.name)

        def serve(network, labels=None):
            container = client.containers.run(
                image,
                command=["node", "-e", 'require("http").createServer((request, response) => '
                         'response.end("ok")).listen(8080, "0.0.0.0", () => console.log("ready"))'],
                detach=True, network=network.name, labels=labels or {}, cap_drop=["ALL"],
            )
            cleanup.callback(container.remove, force=True)
            logs = container.logs(stream=True)
            try:
                assert b"ready" in next(logs)
            finally:
                logs.close()
            container.reload()
            return container

        upstream = serve(egress)
        approved = serve(isolated, {"nautionette.chat": prefix, "nautionette.turn": "approved"})
        denied = serve(isolated, {"nautionette.chat": prefix, "nautionette.turn": "denied"})
        upstream_ip = upstream.attrs["NetworkSettings"]["Networks"][egress.name]["IPAddress"]
        gateway_ip = denied.attrs["NetworkSettings"]["Networks"][isolated.name]["IPAddress"]

        def can_fetch(container, address):
            result = container.exec_run([
                "node", "-e", f'fetch("http://{address}:8080", {{signal: AbortSignal.timeout(1500)}})'
                '.then(response => { if (!response.ok) process.exit(1) }).catch(() => process.exit(1))',
            ])
            return result.exit_code == 0

        assert can_fetch(approved, gateway_ip)
        assert not can_fetch(approved, upstream_ip)
        assert not can_fetch(denied, upstream_ip)
        approved.exec_run([
            "node", "-e", 'require("fs").mkdirSync("/tmp/nautionette-internet-decision")',
        ])
        with pytest.raises(RuntimeError, match="Could not deliver"):
            agent_run.decide_internet(prefix, "approved", True)
        assert not can_fetch(approved, upstream_ip)
        approved.exec_run([
            "node", "-e", 'require("fs").rmdirSync("/tmp/nautionette-internet-decision")',
        ])
        assert agent_run.decide_internet(prefix, "approved", True)
        assert can_fetch(approved, upstream_ip)
        assert can_fetch(approved, gateway_ip)
        assert agent_run.decide_internet(prefix, "denied", False)
        assert not can_fetch(denied, upstream_ip)
        assert agent_run.decide_internet(prefix, "approved", True)
        for container, expected in [(approved, b"allowed"), (denied, b"denied")]:
            result = container.exec_run([
                "node", "-e", 'process.stdout.write(require("fs").readFileSync('
                '"/tmp/nautionette-internet-decision"))',
            ])
            assert result.output == expected