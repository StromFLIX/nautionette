"""Automatic deployment ordering and fail-closed recovery, without SSH credentials."""

import io
import json
import os
import shlex
import subprocess
import urllib.request
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
        self.env = {
            "TEMPORAL_NUM_HISTORY_SHARDS": "4",
            "TEMPORAL_MAINTENANCE_APP": "app",
            "TEMPORAL_VERSION": "1.29.0",
        }

    def call(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        if path.endswith("/envs"):
            return [{"key": key, "value": value} for key, value in self.env.items()]
        if path.endswith("/envs/bulk") and method == "PATCH":
            self.env.update({row["key"]: row["value"] for row in payload["data"]})
            return {}
        if method == "PATCH":
            self.details.update(payload)
        return self.details.copy()


def test_hooks_use_only_existing_coolify_credentials_and_preserve_custom_hooks(monkeypatch):
    monkeypatch.setattr(
        transport,
        "candidate_files",
        lambda commit: {
            "temporal_deploy_hook.py": "pass",
            "context/upgrade-plan.json": json.dumps({"target": "1.32.2"}),
        },
    )
    api = API()
    deploy = transport.TemporalDeployment(api, "app", "staging", COMMIT)
    assert deploy.prepare() == "4"
    assert api.env["TEMPORAL_VERSION"] == "1.32.2"  # Persisted CLI build-arg cannot force the old image.
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


def test_old_candidate_hook_receives_read_only_receipt_adapter(monkeypatch):
    files = {
        "temporal_deploy_hook.py": (
            "def state(container): raise AssertionError('old exec must not run')\n"
            "def hook(payload): assert state(None) == {'status':'completed'}\n"
        )
    }
    monkeypatch.setattr(
        transport.Path,
        "read_text",
        lambda path: "def controller_state(container): return {'status':'completed'}\n",
    )
    command = transport.command(files, "app", "staging", COMMIT, "4", "prepare")
    exec(compile(shlex.split(command)[-1], "legacy-hook-bootstrap", "exec"), {})  # noqa: S102 - generated bootstrap test
    assert "temporal_state.py" not in files


def test_preflight_selects_private_data_network_not_coolifys_shared_network(monkeypatch):
    container = {"NetworkSettings": {"Networks": {"data": {}, "coolify": {}}}}
    monkeypatch.setattr(host, "docker", lambda *args: json.dumps([{"Internal": args[-1] == "data"}]))
    assert host.private_data_network(container, container) == "data"
    monkeypatch.setattr(host, "docker", lambda *args: json.dumps([{"Internal": False}]))
    with pytest.raises(RuntimeError, match="private PostgreSQL"):
        host.private_data_network(container, container)
    monkeypatch.setattr(host, "docker", lambda *args: json.dumps([{"Internal": True}]))
    with pytest.raises(RuntimeError, match="private PostgreSQL"):
        host.private_data_network(container, container)


@pytest.mark.parametrize(
    "version,kind,dockerfile",
    [
        ("1.29.7", "tools", "Dockerfile.tools-legacy"),
        ("1.29.7", "server", "Dockerfile.legacy"),
        ("1.30.9", "tools", "Dockerfile.tools"),
        ("1.32.2", "server", "Dockerfile"),
    ],
)
def test_legacy_patch_uses_published_bundle_not_nonexistent_admin_tag(
    tmp_path, monkeypatch, version, kind, dockerfile
):
    command = Mock(return_value="sha256:fixture")
    monkeypatch.setattr(host, "docker", command)
    host.build(tmp_path, version, kind)
    assert str(tmp_path / "context" / dockerfile) in command.call_args_list[0].args


def test_shutdown_drains_backend_before_freezing_broker_and_closes_mcp_clients_first(monkeypatch):
    monkeypatch.delenv("MAINTENANCE_RUN_ID", raising=False)
    calls = []
    running = {"backend": True, "docker-broker": True}

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "inspect":
            return json.dumps(
                [
                    {
                        "State": {"Running": running[args[1]], "Paused": False, "ExitCode": 0},
                        "Config": {"Env": ["BACKEND_CHAT_DRAIN_SECONDS=25"], "StopTimeout": 300},
                    }
                ]
            )
        if args[0] == "stop":
            running[args[-1]] = False
        return ""

    monkeypatch.setattr(host, "docker", docker)
    monkeypatch.setattr(
        host.storage, "stop", lambda group: calls.append(("group", group["containers"][0]["service"]))
    )
    stack = {
        "containers": [
            {"id": name, "service": name}
            for name in (
                "backend",
                "docker-broker",
                "workflow-mcp",
                "agentgateway",
                "worker",
            )
        ]
    }
    host.stop(stack)
    assert calls.index(("stop", "--time", "300", "backend")) < calls.index(("pause", "docker-broker"))
    assert calls.index(("group", "worker")) < calls.index(("group", "agentgateway"))
    assert calls.index(("group", "agentgateway")) < calls.index(("group", "workflow-mcp"))


@pytest.mark.parametrize("exit_code", [0, 137])
def test_maxio_uses_its_graceful_sigint_and_still_refuses_sigkill(monkeypatch, exit_code):
    monkeypatch.delenv("MAINTENANCE_RUN_ID", raising=False)
    docker = Mock(return_value=json.dumps([{"State": {"Running": False, "ExitCode": exit_code}}]))
    monkeypatch.setattr(host, "docker", docker)
    stack = {"containers": [{"id": "maxio", "service": "maxio"}]}
    if exit_code == 137:
        with pytest.raises(RuntimeError, match="MaxIO did not drain"):
            host.stop(stack)
    else:
        host.stop(stack)
    assert any(
        call.args == ("stop", "--signal", "SIGINT", "--time", "90", "maxio") for call in docker.call_args_list
    )
    assert "STOPSIGNAL SIGINT" in (ROOT / "infra/maxio/Dockerfile").read_text()


@pytest.mark.parametrize(
    "preflight,fault,allowed",
    [
        (False, None, False),
        (True, None, True),
        (True, "workers", False),
        (True, "images", False),
        (True, "temporal", False),
        (True, "auth", False),
    ],
)
def test_future_migration_preflight_does_not_require_its_own_completed_receipt(
    monkeypatch, preflight, fault, allowed
):
    monkeypatch.setenv("APP_ENVIRONMENT", "staging")
    monkeypatch.setenv("APP_TOKEN", "test-only")
    detail = {
        "docker": True,
        "image_status": "ready",
        "workers": {"status": "ready"},
        "temporal_migration": {"phase": "verification_pending"},
    }
    payload = {
        "environment": "staging",
        "auth_enabled": fault != "auth",
        "components": [
            {
                "name": name,
                "status": "degraded" if name == "broker" else "ok",
                "detail": detail if name == "broker" else {},
            }
            for name in ("temporal", "broker", "agentgateway", "workflow-mcp")
        ],
    }
    if fault == "workers":
        detail["workers"]["status"] = "degraded"
    if fault == "images":
        detail["image_status"] = "missing"
    if fault == "temporal":
        payload["components"][0]["status"] = "down"
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, **kwargs: io.BytesIO(
            json.dumps(
                {"health": {"status": "ok"}} if request.full_url.endswith("/storage") else payload
            ).encode()
        ),
    )

    def execute(*args, **kwargs):
        try:
            exec(compile(args[-1], "maintenance-health", "exec"), {})  # noqa: S102 - fixed health program under test
        except AssertionError:
            raise RuntimeError("health refused") from None
        return ""

    monkeypatch.setattr(host.storage, "docker", execute)
    if allowed:
        host.storage.health({"backend": "backend"}, "staging", attempts=1, migration_preflight=preflight)
    else:
        with pytest.raises(RuntimeError, match="health checks failed"):
            host.storage.health({"backend": "backend"}, "staging", attempts=1, migration_preflight=preflight)


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
    metadata = {"stack": stack, "from": "1.29.0", "shards": "4"}
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
    first_write = next(index for index, call in enumerate(calls) if "upgrade-from-1.29.0" in call)
    assert calls.index(("backup",)) < calls.index(("restore",)) < first_write
    assert [(call[1], call[2]) for call in calls if call[0] == "probe"] == [
        ("capture", "1.29.0"),
        ("check", "1.29.7"),
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
    assert ("probe", "resume", "1.29.0") in calls


@pytest.mark.parametrize(
    "phase,commit,expected",
    [
        ("prepared", COMMIT, "degraded"),
        ("needs_recovery", COMMIT, "degraded"),
        ("completed", "c" * 40, "degraded"),
        ("completed", COMMIT, "ready"),
    ],
)
@pytest.mark.parametrize("container_status", ["running", "exited"])
def test_swallowed_post_hook_failure_cannot_pass_application_health(
    monkeypatch, phase, commit, expected, container_status
):
    monkeypatch.setenv("APP_VERSION", COMMIT)
    monkeypatch.setattr(
        temporal_maintenance.workers, "worker_filters", lambda: {"label": ["com.docker.compose.project=app"]}
    )
    container = SimpleNamespace(
        status=container_status,
        exec_run=Mock(
            return_value=SimpleNamespace(
                exit_code=0, output=json.dumps({"status": phase, "commit": commit}).encode()
            )
        ),
    )
    client = SimpleNamespace(containers=SimpleNamespace(list=Mock(return_value=[container])))
    monkeypatch.setattr(temporal_maintenance.daemon, "client", lambda: client)
    read = Mock(return_value={"status": phase, "commit": commit})
    monkeypatch.setattr(temporal_maintenance, "controller_state", read)
    assert temporal_maintenance.snapshot()["status"] == expected
    read.assert_called_once_with(container)
    container.exec_run.assert_not_called()


@pytest.mark.parametrize(
    "status,policy,expected",
    [
        ("running", "unless-stopped", "degraded"),
        ("running", "no", "degraded"),
        ("exited", "unless-stopped", "degraded"),
        ("exited", "no", "ready"),
    ],
)
def test_automatic_health_requires_completed_controller_to_be_retired(monkeypatch, status, policy, expected):
    monkeypatch.setenv("APP_VERSION", COMMIT)
    monkeypatch.setenv("TEMPORAL_MAINTENANCE_APP", "app")
    monkeypatch.setattr(
        temporal_maintenance.workers, "worker_filters", lambda: {"label": ["com.docker.compose.project=app"]}
    )
    container = SimpleNamespace(status=status, attrs={"HostConfig": {"RestartPolicy": {"Name": policy}}})
    monkeypatch.setattr(
        temporal_maintenance.daemon,
        "client",
        lambda: SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [container])),
    )
    monkeypatch.setattr(
        temporal_maintenance, "controller_state", lambda _: {"status": "completed", "commit": COMMIT}
    )
    assert temporal_maintenance.snapshot()["status"] == expected


@pytest.mark.parametrize("automatic", [True, False])
def test_automatic_health_cannot_silently_accept_a_missing_receipt(monkeypatch, automatic):
    if automatic:
        monkeypatch.setenv("TEMPORAL_MAINTENANCE_APP", "app")
    else:
        monkeypatch.delenv("TEMPORAL_MAINTENANCE_APP", raising=False)
    monkeypatch.setattr(
        temporal_maintenance.workers, "worker_filters", lambda: {"label": ["com.docker.compose.project=app"]}
    )
    monkeypatch.setattr(
        temporal_maintenance.daemon,
        "client",
        lambda: SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: [])),
    )
    assert temporal_maintenance.snapshot()["status"] == ("degraded" if automatic else "ready")


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


@pytest.mark.asyncio
async def test_readiness_waits_for_matching_membership_but_does_not_hide_other_rpc_errors(monkeypatch):
    monkeypatch.setattr(probe.asyncio, "sleep", AsyncMock())
    handle = SimpleNamespace(
        query=AsyncMock(
            side_effect=[
                probe.RPCError("Not enough hosts", probe.RPCStatusCode.UNAVAILABLE, b""),
                True,
            ]
        )
    )
    await probe.checkpoint(handle)
    assert handle.query.await_count == 2
    handle.query.side_effect = probe.RPCError("Denied", probe.RPCStatusCode.PERMISSION_DENIED, b"")
    with pytest.raises(probe.RPCError):
        await probe.checkpoint(handle)
