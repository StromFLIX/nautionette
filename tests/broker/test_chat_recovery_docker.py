"""Real Docker counterpart to the backend restart/retry regression tests."""

import os
from contextlib import ExitStack

import docker
import pytest
from docker.errors import NotFound
from nautionette_docker_broker import agent_run, chat_agents, projects

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1",
    reason="Set NAUTIONETTE_DOCKER_TESTS=1 to test orphan cleanup with persistent Git worktrees",
)


def test_orphan_cleanup_preserves_git_commits_and_uncommitted_work(monkeypatch):
    client = docker.from_env()
    image = "nautionette/pi-base:dev"
    chat, project = "c" * 12, "a" * 32
    session = f"/projects/.sessions/{project}/{chat}"
    with ExitStack() as cleanup:
        cleanup.callback(client.close)
        workflows = client.volumes.create()
        data = client.volumes.create()
        staging = client.volumes.create()
        staging_data = client.volumes.create()
        for volume in (workflows, data, staging, staging_data):
            cleanup.callback(volume.remove)
        monkeypatch.setattr(chat_agents, "WORKFLOWS_VOLUME", workflows.name)
        monkeypatch.setattr(agent_run.daemon, "client", lambda: client)
        monkeypatch.setattr(projects, "volume_name", lambda: data.name)
        # Simulate a restarted broker: no in-memory owner or worktree claims.
        monkeypatch.setattr(agent_run, "_stopped", {})
        monkeypatch.setattr(agent_run, "_completed", {})
        monkeypatch.setattr(agent_run, "_retired", {})
        monkeypatch.setattr(projects, "_claimed", set())

        def remove(container):
            try:
                container.remove(force=True)
            except NotFound:
                pass

        def create(script, workflow_volume=workflows, data_volume=data, turn="old"):
            container = client.containers.create(
                image,
                entrypoint="node",
                command=["-e", script],
                volumes={
                    workflow_volume.name: {"bind": "/workflows", "mode": "ro"},
                    data_volume.name: {"bind": "/projects", "mode": "rw"},
                },
                labels={
                    "nautionette.chat": chat,
                    "nautionette.turn": turn,
                    f"nautionette.project.{project}": chat,
                },
                network="none",
            )
            cleanup.callback(remove, container)
            container.start()
            return container

        initializer = create(
            "const fs=require('fs'),cp=require('child_process');"
            "const git=(...a)=>cp.execFileSync('git',a);"
            f"fs.mkdirSync('/projects/{project}',{{recursive:true}});"
            f"git('init','-b','main','/projects/{project}');"
            f"git('-C','/projects/{project}','config','user.email','test@example.test');"
            f"git('-C','/projects/{project}','config','user.name','Test');"
            f"git('-C','/projects/{project}','commit','--allow-empty','-m','Initial');"
            f"git('-C','/projects/{project}','worktree','add','--detach','{session}','HEAD');"
            f"fs.writeFileSync('{session}/committed.txt','saved commit');"
            f"git('-C','{session}','add','.');git('-C','{session}','commit','-m','Local work');"
            f"fs.writeFileSync('{session}/unfinished.txt','uncommitted edits');",
            turn="initializer",
        )
        assert initializer.wait(timeout=30)["StatusCode"] == 0, initializer.logs().decode()
        remove(initializer)
        orphan = create("console.log('ready');setInterval(()=>{},1000)")
        foreign = create("setInterval(()=>{},1000)", workflow_volume=staging, data_volume=staging_data)
        with pytest.raises(ValueError, match="still in use"):
            projects.claim([project], chat)
        agent_run.cleanup_chat(chat, "old")
        with pytest.raises(NotFound):
            orphan.reload()
        foreign.reload()
        assert foreign.status == "running"
        projects.claim([project], chat)
        try:
            retry = create(
                "const fs=require('fs'),cp=require('child_process'),assert=require('assert');"
                f"assert.equal(fs.readFileSync('{session}/unfinished.txt','utf8'),'uncommitted edits');"
                f"assert.equal(cp.execFileSync('git',['-C','{session}','show','HEAD:committed.txt'],"
                "{encoding:'utf8'}),'saved commit');"
                f"assert.equal(cp.execFileSync('git',['-C','{session}','rev-parse','--abbrev-ref','HEAD'],"
                "{encoding:'utf8'}).trim(),'HEAD');",
                turn="retry",
            )
            assert retry.wait(timeout=30)["StatusCode"] == 0, retry.logs().decode()
        finally:
            projects.release([project], chat)
