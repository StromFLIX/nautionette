import base64
import io
import json
import os
import tarfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import docker
import pytest
from nautionette_docker_broker import agent_run

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1",
    reason="Set NAUTIONETTE_DOCKER_TESTS=1 to exercise the installed Pi RPC runtime",
)


@pytest.mark.parametrize("stop", [False, True])
def test_real_pi_steering_and_stop_at_a_running_tool(monkeypatch, repo_root, stop):
    client = docker.from_env()
    with ExitStack() as cleanup:
        cleanup.callback(client.close)
        job = {
            "chat_id": "chat-test",
            "turn_id": "turn-test",
            "model": "test-model",
            "prompt": "Run the blocking tool",
        }
        container = client.containers.create(
            "nautionette/pi-base:dev",
            entrypoint="node",
            command=["/workspace/chat-model.mjs"],
            environment={
                "AGENT_JOB": base64.b64encode(json.dumps(job).encode()).decode(),
                "PI_CODING_AGENT_DIR": "/workspace/pi-config",
            },
            labels={"nautionette.chat": job["chat_id"], "nautionette.turn": job["turn_id"]},
            network="none",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )
        cleanup.callback(container.remove, force=True)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            for name in ("agent-run.mjs", "chat-control.mjs", "project-git.mjs", "context-usage.mjs"):
                bundle.add(repo_root / "images/pi-base" / name, arcname=name)
            bundle.add(repo_root / "tests/agent/fixtures/chat-model.mjs", arcname="chat-model.mjs")
            bundle.add(
                repo_root / "tests/agent/fixtures/chat-provider.ts",
                arcname="pi-config/extensions/provider.ts",
            )
        container.put_archive("/workspace", archive.getvalue())
        monkeypatch.setattr(agent_run.daemon, "client", lambda: client)
        container.start()

        def wait_for_tool():
            buffer = b""
            for chunk in container.logs(stream=True, follow=True):
                buffer += chunk
                if b'"type":"tool"' in buffer:
                    return
            raise AssertionError(buffer.decode())

        with ThreadPoolExecutor() as executor:
            future = executor.submit(wait_for_tool)
            try:
                future.result(timeout=30)
            except Exception:
                container.kill()
                raise
        assert not agent_run.control("other-chat", job["turn_id"], {"type": "stop", "id": "wrong"})
        if stop:
            assert agent_run.control(job["chat_id"], job["turn_id"], {"type": "stop", "id": "stop"})
            assert container.wait(timeout=10)["StatusCode"] != 0
        else:
            command = {"type": "steer", "id": "queued", "text": "queued instruction"}
            assert agent_run.control(job["chat_id"], job["turn_id"], command)
            assert agent_run.control(job["chat_id"], job["turn_id"], command)
            container.exec_run(["node", "-e", "require('fs').writeFileSync('/tmp/release-tool','yes')"])
            assert container.wait(timeout=30)["StatusCode"] == 0, container.logs().decode()
            events = [json.loads(line) for line in container.logs(stdout=True, stderr=False).splitlines()]
            assert [event["id"] for event in events if event["type"] == "input_consumed"] == ["queued"]
            result = next(event for event in events if event["type"] == "result")
            assert result["ok"], result
            assert result["text"] == "Received queued instruction after tool"
