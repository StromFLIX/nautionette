"""Opt-in real Docker check, offline: private delivery and Git across expiry."""

import json
import os
from contextlib import ExitStack

import docker
import pytest
from nautionette_docker_broker import git_credentials

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1",
    reason="Set NAUTIONETTE_DOCKER_TESTS=1 to check Git credential rotation in Docker",
)


def test_live_unprivileged_container_reads_rotated_credentials(repo_root):
    client = docker.from_env()
    with ExitStack() as cleanup:
        cleanup.callback(client.close)
        container = client.containers.create(
            "nautionette/pi-base:dev",
            entrypoint="node",
            command=["-e", "setInterval(() => {}, 1000)"],
            user="10001:10001",
            network="none",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )
        cleanup.callback(container.remove, force=True)
        credentials = [
            {
                "full_name": "owner/repo",
                "token": "expired-token",
                "expires_at": "2000-01-01T00:00:00Z",
            }
        ]
        git_credentials.prepare(container, credentials, {"owner/repo"})
        container.start()
        # Exercise the shipped helper and its dependencies, not an incomplete
        # /tmp copy. A stale local image must fail clearly instead of masking
        # a bootstrap/import failure as the expected expired-token rejection.
        for name in ("project-git.mjs", "brand.json"):
            shipped = container.exec_run(
                [
                    "node",
                    "-e",
                    "process.stdout.write(require('node:fs').readFileSync(process.argv[1]))",
                    f"/usr/local/lib/nautionette/{name}",
                ]
            )
            assert shipped.exit_code == 0, f"Agent image is missing {name}; rebuild the current Pi base."
            assert shipped.output == (repo_root / "images/pi-base" / name).read_bytes(), (
                f"Agent image has stale {name}; rebuild the current Pi base."
            )
        ready = container.exec_run(
            [
                "node",
                "--input-type=module",
                "-e",
                "await import('/usr/local/lib/nautionette/project-git.mjs')",
            ]
        )
        assert ready.exit_code == 0, ready.output.decode("utf-8", errors="replace")
        script = """
            import { execFileSync } from 'node:child_process';
            import { projectEnvironment } from '/usr/local/lib/nautionette/project-git.mjs';
            const env = {...process.env, ...projectEnvironment({project_ids: ['selected'],
                project_remotes: {selected: 'owner/repo'}})};
            try {
                process.stdout.write(execFileSync('git', ['credential', 'fill'], {
                    env, input: 'url=https://github.com/owner/repo.git\\n\\n', stdio: 'pipe',
                }));
            } catch { process.exit(1); }
        """
        command = ["node", "--input-type=module", "-e", script]
        assert container.exec_run(command).exit_code == 1
        for token in ("renewed-token", "renewed-again"):
            git_credentials.replace(
                container,
                [
                    credentials[0]
                    | {
                        "token": token,
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                ],
            )
            result = container.exec_run(command)
            assert result.exit_code == 0
            assert f"password={token}".encode() in result.output
        assert "renewed-token" not in json.dumps(container.attrs["Config"]["Env"])
        # UID 10001 can read, but cannot alter, replace or redirect the private file.
        for expression in (
            "writeFileSync('/tmp/nautionette-git-credentials/current.json', '[]')",
            "unlinkSync('/tmp/nautionette-git-credentials/current.json')",
            "renameSync('/tmp/nautionette-git-credentials', '/tmp/stolen')",
        ):
            result = container.exec_run(["node", "-e", f"require('node:fs').{expression}"])
            assert result.exit_code != 0
