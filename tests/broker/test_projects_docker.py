import io
import json
import os
import tarfile
import uuid
from contextlib import ExitStack

import docker
import pytest
from nautionette_docker_broker import projects

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1",
    reason="Set NAUTIONETTE_DOCKER_TESTS=1 to test real project worktree isolation",
)


def test_chat_worktree_mounts_are_isolated_and_persistent(monkeypatch, tmp_path, repo_root):
    client = docker.from_env()
    image = "nautionette/pi-base:dev"
    selected, hidden = "a" * 32, "b" * 32
    first_chat, second_chat = "c" * 12, "d" * 12
    with ExitStack() as cleanup:
        cleanup.callback(client.close)
        volume = client.volumes.create(name="nautionette-projects-test-" + uuid.uuid4().hex[:12])
        cleanup.callback(volume.remove)
        initializer = client.containers.create(
            image,
            entrypoint="node",
            command=[
                "-e",
                "const fs=require('fs'),cp=require('child_process');"
                f"fs.mkdirSync('/projects/{selected}',{{recursive:true}});"
                f"fs.mkdirSync('/projects/{hidden}',{{recursive:true}});"
                f"fs.mkdirSync('/projects/.sessions/{selected}/{first_chat}',{{recursive:true}});"
                f"fs.mkdirSync('/projects/.sessions/{selected}/{second_chat}',{{recursive:true}});"
                f"cp.execFileSync('git',['init','-b','main','/projects/{selected}']);"
                f"cp.execFileSync('git',['-C','/projects/{selected}','-c','user.name=Test',"
                "'-c','user.email=test@example.test','commit','--allow-empty','-m','Initial']);"
                f"cp.execFileSync('git',['-C','/projects/{selected}','remote','add','origin',"
                f"'/project-repositories/{selected}']);"
                "cp.execFileSync('chown',['-R','10001:10001','/projects']);",
            ],
            volumes={volume.name: {"bind": "/projects", "mode": "rw"}},
            network="none",
        )
        cleanup.callback(initializer.remove, force=True)
        initializer.start()
        assert initializer.wait()["StatusCode"] == 0, initializer.logs().decode()
        monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(projects, "PROJECTS_VOLUME", volume.name)
        (tmp_path / selected / ".git").mkdir(parents=True)
        for chat_id in (first_chat, second_chat):
            (tmp_path / ".sessions" / selected / chat_id).mkdir(parents=True)

        def run(chat_id, script):
            job = {
                "chat_id": chat_id,
                "project_ids": [selected],
                "project_baselines": {selected: "main"},
                "project_remotes": {selected: "owner/repository"},
            }
            container = client.containers.create(
                image,
                entrypoint="node",
                command=[
                    "--input-type=module",
                    "-e",
                    "import {prepareProjects,projectEnvironment} from '/workspace/project-git.mjs';"
                    "import fs from 'node:fs';import assert from 'node:assert/strict';"
                    "import {execFileSync} from 'node:child_process';"
                    f"const job={json.dumps(job)};prepareProjects(job);"
                    "const git=(...args)=>execFileSync('git',args,{encoding:'utf8',"
                    "env:{...process.env,...projectEnvironment(job)}}).trim();" + script,
                ],
                mounts=projects.mounts([selected], chat_id),
                network="none",
                cap_drop=["ALL"],
                user="10001:10001",
                tmpfs={"/projects": "size=1m,uid=10001,gid=10001"},
                security_opt=["no-new-privileges:true"],
            )
            cleanup.callback(container.remove, force=True)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as bundle:
                bundle.add(repo_root / "images/pi-base/project-git.mjs", arcname="project-git.mjs")
            container.put_archive("/workspace", archive.getvalue())
            container.start()
            assert container.wait()["StatusCode"] == 0, container.logs().decode()

        run(
            first_chat,
            f"assert.equal(git('-C','/projects/{selected}','remote','get-url','origin'),'https://github.com/owner/repository.git');"
            f"assert.equal(git('-C','/projects/{selected}','rev-parse','--abbrev-ref','HEAD'),'HEAD');"
            f"fs.writeFileSync('/projects/{selected}/change.txt','first');"
            f"git('-C','/projects/{selected}','add','.');git('-C','/projects/{selected}','commit','-m','First');"
            f"fs.writeFileSync('/projects/{selected}/unfinished.txt','keep');",
        )
        run(
            second_chat,
            f"assert.equal(fs.existsSync('/projects/{selected}/change.txt'),false);"
            f"assert.equal(fs.existsSync('/projects/{hidden}'),false);"
            f"assert.equal(fs.existsSync('/projects/.sessions/{selected}/{first_chat}'),false);"
            f"fs.writeFileSync('/projects/{selected}/second.txt','second');",
        )
        run(
            first_chat,
            f"assert.equal(fs.readFileSync('/projects/{selected}/unfinished.txt','utf8'),'keep');"
            f"assert.equal(git('-C','/projects/{selected}','show','HEAD:change.txt'),'first');"
            f"assert.equal(fs.existsSync('/projects/{selected}/second.txt'),false);",
        )
