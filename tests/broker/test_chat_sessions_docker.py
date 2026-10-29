"""Real Docker volume-subpath isolation and disposal; no model/network calls."""

import json
import os
import uuid
from pathlib import Path

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
        # A privileged broker creates the lock, then the real backend UID must
        # acquire that same inode for deletion. Never test only as root.
        shared = Path(__file__).resolve().parents[2] / "libs/nautionette/src/nautionette/chat_sessions.py"
        lock_script = (
            "import importlib.util; "
            "s=importlib.util.spec_from_file_location('sessions','/session-state.py'); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "lock=m.session_lock('one'); lock.__enter__(); lock.__exit__(None,None,None)"
        )
        for uid in ("0:0", "10001:10001"):
            client.containers.run(
                IMAGE,
                entrypoint="python3",
                command=["-c", lock_script],
                user=uid,
                mounts=[
                    Mount(target="/chat-sessions", source=volume.name, type="volume"),
                    Mount(target="/session-state.py", source=str(shared), type="bind", read_only=True),
                ],
                network_mode="none",
                remove=True,
            )
        assert first["id"] == second["id"]
        assert first["sibling"] is second["sibling"] is False
        assert first["escape"] is second["escape"] is False
        assert any(e.get("message", {}).get("content") == "durable evidence" for e in second["entries"])
        assert any(e.get("customType") == "extension-state" for e in second["entries"])
    finally:
        volume.remove(force=True)
        client.close()
