"""Automatic deployment ordering and fail-closed recovery, without SSH credentials."""

import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nautionette_docker_broker import temporal_maintenance

from scripts import temporal_deploy as transport
from scripts import temporal_deploy_host as host
from scripts import temporal_deploy_probe as probe

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "b" * 40


class API:
    def __init__(self):
        self.details = {}
        self.calls = []

    def call(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        if path.endswith("/envs"):
            return [
                {"key": "TEMPORAL_NUM_HISTORY_SHARDS", "value": "4"},
                {"key": "TEMPORAL_MAINTENANCE_APP", "value": "app"},
            ]
        if method == "PATCH":
            self.details.update(payload)
        return self.details.copy()


def test_hooks_use_only_existing_coolify_credentials_and_preserve_custom_hooks(monkeypatch):
    monkeypatch.setattr(transport, "candidate_files", lambda commit: {"temporal_deploy_hook.py": "pass"})
    api = API()
    deploy = transport.TemporalDeployment(api, "app", "staging", COMMIT)
    assert deploy.prepare() == "4"
    assert api.details["pre_deployment_command_container"] == "docker-broker"
    assert api.details["post_deployment_command_container"] == "docker-broker"
    assert all("\n" not in api.details[key] for key in ("pre_deployment_command", "post_deployment_command"))
    deploy.finish()
    api.details["pre_deployment_command"] = "echo custom-admin-hook"
    before = len(api.calls)
    with pytest.raises(RuntimeError, match="custom deployment hooks"):
        deploy.prepare()
    assert all(method == "GET" for _, method, _ in api.calls[before:])
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "DEPLOY_SSH" not in workflow
    assert "python3 -m scripts.coolify_release" in workflow


def test_helper_container_names_fit_docker_dns_labels():
    first = Path("a" * 100)
    second = Path("b" * 100)
    assert len(host.name(first, "server")) <= 63
    assert host.name(first, "server") != host.name(second, "server")
    assert host.name(first, "server") == host.name(first, "server")


@pytest.fixture
def operation(tmp_path, monkeypatch):
    (tmp_path / "context").mkdir()
    (tmp_path / "context/upgrade-plan.json").write_text(
        (ROOT / "infra/temporal/upgrade-plan.json").read_text()
    )
    stack = {
        "app": "app",
        "namespace": "default",
        "containers": [
            {"id": name, "service": name, "restart": {"Name": "unless-stopped"}}
            for name in ("backend", "docker-broker", "worker", "workflow-mcp", "temporal", "postgres")
        ],
        "data_network": "data",
        "volumes": {},
    }
    metadata = {"stack": stack, "from": "1.29.7", "shards": "4"}
    state = {
        **metadata,
        "status": "queued",
        "target": "1.32.2",
        "app": "app",
        "environment": "staging",
        "commit": COMMIT,
    }
    host.save(tmp_path, state)
    calls = []
    monkeypatch.setattr(host, "build", lambda path, version, kind: version + "/" + kind)
    monkeypatch.setattr(host, "preflight", lambda *args: metadata)
    monkeypatch.setattr(host, "stop", lambda stack: calls.append(("stop",)))
    monkeypatch.setattr(host, "start", lambda *args: calls.append(("start",)))
    monkeypatch.setattr(host, "snapshot", lambda *args: calls.append(("backup",)))
    monkeypatch.setattr(host, "restore_check", lambda *args: calls.append(("restore",)))
    monkeypatch.setattr(
        host, "probe", lambda path, stack, server, mode, version: calls.append(("probe", mode, version))
    )
    monkeypatch.setattr(host, "server", lambda *args: "temporary-server")
    monkeypatch.setattr(host, "remove", lambda *args: None)
    monkeypatch.setattr(host, "schemas", lambda stack: ("1.18", "1.9"))
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)

    def docker(*args, **kwargs):
        if "upgrade-from-" in " ".join(args):
            assert host.load(tmp_path)["restore_verified"]
            assert host.load(tmp_path)["schema_writes_started"]
        calls.append(("docker", *args))
        return ""

    monkeypatch.setattr(host, "docker", docker)
    return tmp_path, calls, state


def test_automatic_migration_restores_backup_before_any_schema_write_and_checks_every_minor(operation):
    path, calls, _ = operation
    host.run(path)
    assert host.load(path)["status"] == "prepared"
    first_write = next(index for index, call in enumerate(calls) if "upgrade-from-1.29.7" in call)
    assert calls.index(("backup",)) < calls.index(("restore",)) < first_write
    assert [(call[1], call[2]) for call in calls if call[0] == "probe"] == [
        ("capture", "1.29.7"),
        ("check", "1.30.9"),
        ("check", "1.31.5"),
        ("check", "1.32.2"),
        ("finish", "1.32.2"),
        ("resume", "1.32.2"),
    ]
    # App writers are not resumed by prepare; only PostgreSQL starts for schema tools.
    assert calls.count(("start",)) == 1


def test_failure_after_possible_schema_write_never_restarts_old_server(operation, monkeypatch):
    path, calls, state = operation
    state.update(status="migrating-1.31.5", schema_writes_started=True)
    host.save(path, state)
    monkeypatch.setattr(
        host.storage, "containers", lambda: [{"Id": item["id"]} for item in state["stack"]["containers"]]
    )
    host.recover(path)
    assert host.load(path)["status"] == "needs_recovery"
    assert ("start",) not in calls
    assert ("stop",) in calls


def test_failure_before_schema_writes_can_restore_original_stack(operation):
    path, calls, state = operation
    state.update(status="backing_up", stopping=True)
    host.save(path, state)
    (path / "probe.json").write_text("{}")
    host.recover(path)
    assert host.load(path)["status"] == "failed"
    assert host.load(path)["safe_to_retry"] is True
    assert calls.count(("start",)) == 2
    assert ("probe", "resume", "1.29.7") in calls


@pytest.mark.parametrize(
    "phase,commit,expected",
    [
        ("prepared", COMMIT, "degraded"),
        ("needs_recovery", COMMIT, "degraded"),
        ("completed", "c" * 40, "degraded"),
        ("completed", COMMIT, "ready"),
    ],
)
def test_swallowed_post_hook_failure_cannot_pass_application_health(monkeypatch, phase, commit, expected):
    monkeypatch.setenv("APP_VERSION", COMMIT)
    monkeypatch.setattr(
        temporal_maintenance.workers, "worker_filters", lambda: {"label": ["com.docker.compose.project=app"]}
    )
    container = SimpleNamespace(
        status="running",
        exec_run=Mock(
            return_value=SimpleNamespace(
                exit_code=0, output=json.dumps({"status": phase, "commit": commit}).encode()
            )
        ),
    )
    client = SimpleNamespace(containers=SimpleNamespace(list=Mock(return_value=[container])))
    monkeypatch.setattr(temporal_maintenance.daemon, "client", lambda: client)
    assert temporal_maintenance.snapshot()["status"] == expected
    assert container.exec_run.call_args.args[0][-1] == "status"  # Fixed read only, never a generic command.


@pytest.mark.parametrize(
    "phase,revision,allowed",
    [
        ("prepared", COMMIT, True),
        ("current", COMMIT, True),
        ("completed", COMMIT, True),
        ("migrating-1.31.5", COMMIT, False),
        ("prepared", "c" * 40, False),
    ],
)
def test_server_startup_fence_blocks_skipped_hooks_and_wrong_revisions(tmp_path, phase, revision, allowed):
    folder = tmp_path / "fences"
    folder.mkdir()
    (folder / "app").write_text(revision + "\n" + phase + "\n")
    script = tmp_path / "start.sh"
    script.write_text(
        (ROOT / "infra/temporal/start.sh").read_text().replace("/etc/temporal/maintenance", str(folder))
    )
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 1\n")  # Refusal path exits immediately instead of waiting 20 minutes.
    sleep.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + ":" + os.environ["PATH"],
        "TEMPORAL_MAINTENANCE_APP": "app",
        "TEMPORAL_DEPLOY_COMMIT": COMMIT,
    }
    result = subprocess.run(  # noqa: S603 - fixed fixture command
        ["/bin/sh", str(script), "/usr/bin/printf", "started"], env=env, capture_output=True, text=True
    )
    assert (result.stdout == "started") is allowed
    assert (result.returncode == 0) is allowed


@pytest.mark.asyncio
async def test_probe_awaits_sdk_schedule_iterator_before_capture(tmp_path, monkeypatch):
    async def empty():
        if False:
            yield

    @asynccontextmanager
    async def worker(*args, **kwargs):
        yield

    client = SimpleNamespace(
        workflow_service=SimpleNamespace(
            get_cluster_info=AsyncMock(
                return_value=SimpleNamespace(
                    cluster_id="cluster",
                    history_shard_count=4,
                )
            )
        ),
        list_schedules=AsyncMock(side_effect=lambda: empty()),
        list_workflows=Mock(side_effect=lambda *args: empty()),
        start_workflow=AsyncMock(return_value=SimpleNamespace(query=AsyncMock(return_value=True))),
        create_schedule=AsyncMock(),
    )
    monkeypatch.setattr(probe.Client, "connect", AsyncMock(return_value=client))
    monkeypatch.setattr(
        probe, "namespaces", AsyncMock(return_value={"default": {"id": "ns", "retention": 604800}})
    )
    monkeypatch.setattr(probe, "Worker", worker)
    monkeypatch.setattr(probe, "STATE", tmp_path / "probe.json")
    monkeypatch.setattr(probe.os, "sync", lambda: None)
    for key, value in {
        "TEMPORAL_ADDRESS": "server:7233",
        "TEMPORAL_NAMESPACE": "default",
        "PROBE_ID": "unique",
        "PROBE_SHARDS": "4",
    }.items():
        monkeypatch.setenv(key, value)
    await probe.probe("capture")
    client.list_schedules.assert_awaited_once()
    assert json.loads(probe.STATE.read_text())["cluster"] == {"id": "cluster", "shards": 4}
