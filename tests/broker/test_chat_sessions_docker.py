"""Real Docker volume-subpath isolation and disposal; no model/network calls."""

import json
import os
import uuid

import docker
import pytest
from docker.types import Mount

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1", reason="requires Docker and the built Pi image"
)
IMAGE = "nautionette/pi-agent-default:dev"


def test_native_session_survives_container_removal_without_exposing_other_chats():
    client = docker.from_env()
    volume = client.volumes.create(name=f"nautionette-session-test-{uuid.uuid4().hex}")
    try:
        client.containers.run(
            IMAGE,
            entrypoint="bash",
            command=[
                "-c",
                "mkdir -p /state/one /state/two; echo private > /state/two/secret; "
                "chown -R 10001:10001 /state",
            ],
            mounts=[Mount(target="/state", source=volume.name, type="volume")],
            network_mode="none",
            remove=True,
        )
        mount = Mount(target="/chat-session", source=volume.name, type="volume")
        mount["VolumeOptions"] = {"Subpath": "one"}
        script = """
            import { prepareSession } from '/usr/local/lib/nautionette/chat-session.mjs';
            import { SessionManager } from
                '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js';
            import { existsSync } from 'node:fs';
            const session = prepareSession({turn_id: process.env.INPUT}, '/workspace');
            const manager = SessionManager.open(session.file);
            if (process.env.INPUT === 'first') {
              manager.appendMessage({role:'user', content:'durable evidence', timestamp:1});
              manager.appendCustomEntry('extension-state', {counter:7});
              session.sync();
            }
            console.log(JSON.stringify({id:session.id, entries:manager.getEntries(),
              sibling:existsSync('/chat-session/two/secret'), escape:existsSync('/two/secret')}));
        """

        def run(turn):
            return json.loads(
                client.containers.run(
                    IMAGE,
                    entrypoint="node",
                    command=["--input-type=module", "-e", script],
                    environment={"HOME": "/workspace", "INPUT": turn},
                    user="10001:10001",
                    mounts=[mount],
                    tmpfs={"/workspace": "rw,mode=1777"},
                    network_mode="none",
                    remove=True,
                )
            )

        first, second = run("first"), run("second")
        assert first["id"] == second["id"]
        assert first["sibling"] is second["sibling"] is False
        assert first["escape"] is second["escape"] is False
        assert any(e.get("message", {}).get("content") == "durable evidence" for e in second["entries"])
        assert any(e.get("customType") == "extension-state" for e in second["entries"])
    finally:
        volume.remove(force=True)
        client.close()
