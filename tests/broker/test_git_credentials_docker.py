"""Opt-in real Docker check, offline: private delivery and Git across expiry."""

import io
import json
import os
import tarfile
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
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            tar.add(repo_root / "images/pi-base/project-git.mjs", arcname="project-git.mjs")
        container.put_archive("/tmp", archive.getvalue())  # noqa: S108
        container.start()
        script = """
            import { execFileSync } from 'node:child_process';
            import { projectEnvironment } from '/tmp/project-git.mjs';
            const env = {...process.env, ...projectEnvironment({project_ids: ['selected'],
                project_remotes: {selected: 'owner/repo'}})};
            env.GIT_CONFIG_VALUE_1 = '!node /tmp/project-git.mjs';
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
