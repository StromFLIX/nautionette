from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import staging_refresh as runner
from scripts import staging_refresh_host as host


def container(app, svc, *, environment=None):
    volumes = {dest: name for name, (service, dest) in host.DATASETS.items() if service == svc}
    if svc == "worker":
        volumes = {"/workflows": "workflows", "/artifacts": "artifacts"}
    if svc == "docker-broker":
        volumes = {"/projects": "projects"}
    return {
        "Id": app + "-" + svc,
        "Image": "same-postgres-image" if svc == "postgres" else app + "-" + svc + "-image",
        "Config": {
            "Labels": {"com.docker.compose.project": app, "com.docker.compose.service": svc},
            "Env": [
                f"APP_ENVIRONMENT={environment or ('production' if app == 'prod' else 'staging')}",
                "APP_TOKEN=private",
                f"WORKFLOWS_VOLUME={app}-workflows",
                f"TARGET_NETWORK={app}-internal",
                "POSTGRES_USER=temporal",
                "POSTGRES_PASSWORD=private",
            ],
        },
        "State": {"Status": "running", "Running": True, "Paused": False},
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0}},
        "NetworkSettings": {"Networks": {app + "-internal": {}}},
        "Mounts": [
            {"Type": "volume", "Destination": dest, "Name": app + "-" + name}
            for dest, name in volumes.items()
        ],
    }


@pytest.fixture
def stacks():
    rows = [container(app, svc) for app in ("prod", "stage") for svc in sorted(host.SERVICES)]
    return rows, host.inventory(rows, "prod", "production"), host.inventory(rows, "stage", "staging")


def test_inventory_is_secret_free_and_datasets_are_distinct(stacks):
    rows, prod, stage = stacks
    host.assert_isolated(rows, prod, stage)
    host.assert_password_compatible(rows, prod, stage)
    assert len(prod["volumes"]) == 6
    assert "private" not in json.dumps(prod)
    assert "private" not in json.dumps(stage)


@pytest.mark.parametrize(
    "fault", ["shared_volume", "shared_network", "postgres_image", "foreign_mount", "active_agent"]
)
def test_isolation_failures_are_closed(stacks, fault):
    rows, prod, stage = stacks
    if fault == "shared_volume":
        stage["volumes"]["projects"] = prod["volumes"]["projects"]
    elif fault == "shared_network":
        stage["network"] = prod["network"]
    elif fault == "postgres_image":
        stage["postgres_image"] = "other-version"
    elif fault == "foreign_mount":
        rows[-1]["Mounts"].append({"Type": "volume", "Name": "prod-projects", "Destination": "/bad"})
    elif fault == "active_agent":
        agent = container("agent", "pi")
        agent["Config"]["Labels"] = {"nautionette.chat": "copied-chat"}
        agent["NetworkSettings"]["Networks"] = {prod["network"]: {}}
        rows.append(agent)
    with pytest.raises(RuntimeError):
        host.assert_isolated(rows, prod, stage)


@pytest.mark.parametrize(
    "fault", ["identity", "auth", "workflow_volume", "bind_mount", "missing_service", "stopped"]
)
def test_inventory_rejects_unsafe_or_incomplete_stacks(stacks, fault):
    rows, _, _ = stacks
    backend = next(r for r in rows if r["Id"] == "stage-backend")
    if fault == "identity":
        backend["Config"]["Env"][0] = "APP_ENVIRONMENT=production"
    elif fault == "auth":
        backend["Config"]["Env"][1] = "APP_TOKEN="
    elif fault == "workflow_volume":
        broker = next(r for r in rows if r["Id"] == "stage-docker-broker")
        broker["Config"]["Env"][2] = "WORKFLOWS_VOLUME=prod-workflows"
    elif fault == "bind_mount":
        backend["Mounts"][0]["Type"] = "bind"
    elif fault == "missing_service":
        rows.remove(backend)
    else:
        backend["State"]["Status"] = "exited"
    with pytest.raises(RuntimeError):
        host.inventory(rows, "stage", "staging")


def test_refuse_incompatible_restored_role_password(stacks):
    rows, prod, stage = stacks
    pg = next(r for r in rows if r["Id"] == "stage-postgres")
    pg["Config"]["Env"][-1] = "POSTGRES_PASSWORD=other-private"
    with pytest.raises(RuntimeError, match="role configuration"):
        host.assert_password_compatible(rows, prod, stage)


def test_copy_preserves_dotfiles_external_symlinks_hardlinks_permissions_and_bytes(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / ".sessions").mkdir()
    data = source / ".sessions" / "data"
    data.write_bytes(b"sqlite-and-postgres-placeholder\x00\xff")
    data.chmod(0o640)
    os.link(data, source / "hardlink")
    (source / "external-link").symlink_to("/not/mounted/in/helper/python")
    host.copy_tree(source, target)
    host.compare_tree(source, target)
    assert (target / "hardlink").stat().st_ino == (target / ".sessions" / "data").stat().st_ino
    assert (target / "external-link").is_symlink()
    (target / ".sessions" / "data").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="content mismatch"):
        host.compare_tree(source, target)


def test_copy_rejects_special_files(tmp_path):
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(RuntimeError, match="special file"):
        host.copy_tree(tmp_path / "fifo", tmp_path / "copy")


@pytest.fixture
def flow(tmp_path, stacks, monkeypatch):
    rows, prod, stage = stacks
    host.save(tmp_path, {"production": prod, "staging": stage, "status": "queued"})
    monkeypatch.setattr(host, "containers", lambda: rows)
    calls = []
    monkeypatch.setattr(host, "docker", lambda *args, **kwargs: "")
    monkeypatch.setattr(host, "stop", lambda stack: calls.append(("stop", stack["app"])))
    monkeypatch.setattr(host, "start", lambda stack, only=None: calls.append(("start", stack["app"], only)))
    monkeypatch.setattr(host, "health", lambda stack, expected, **kw: calls.append(("health", expected)))
    monkeypatch.setattr(host, "verify_stopped", lambda stack: calls.append(("stopped", stack["app"])))
    monkeypatch.setattr(
        host,
        "copy_datasets",
        lambda path, stack, backup, restore=False: calls.append(("copy", stack["app"], backup, restore)),
    )
    monkeypatch.setattr(host, "helper", lambda *args: calls.append(("quarantine",)))
    return tmp_path, calls


def test_snapshot_resume_restore_quarantine_order(flow):
    path, calls = flow
    host.run(path)
    assert calls.index(("copy", "stage", "staging-before", False)) < calls.index(("stop", "prod"))
    assert calls.index(("health", "production")) < calls.index(("copy", "stage", "production-snapshot", True))
    assert calls.index(("start", "stage", {"postgres", "temporal"})) < calls.index(("quarantine",))
    assert calls.index(("quarantine",)) < calls.index(("start", "stage", None))
    assert host.load(path)["status"] == "completed"


@pytest.mark.parametrize("failure_backup", ["staging-before", "production-snapshot", "restore", "quarantine"])
def test_failure_recovers_production_but_never_runs_unquarantined_staging(flow, monkeypatch, failure_backup):
    path, calls = flow
    copy = host.copy_datasets

    def failing_copy(path, stack, backup, restore=False):
        if (backup == failure_backup and not restore) or (restore and failure_backup == "restore"):
            raise RuntimeError("simulated copy interruption")
        copy(path, stack, backup, restore)

    monkeypatch.setattr(host, "copy_datasets", failing_copy)
    if failure_backup == "quarantine":

        def fail(*args):
            raise RuntimeError("simulated Temporal interruption")

        monkeypatch.setattr(host, "helper", fail)
    with pytest.raises(RuntimeError):
        host.run(path)
    assert ("start", "prod", None) in calls
    if failure_backup in {"restore", "quarantine"}:
        assert ("start", "stage", None) not in calls
    else:
        assert ("start", "stage", None) in calls
    assert host.load(path)["status"] == "failed"


def test_external_kill_recovery_removes_helper_before_restarting_production(flow, monkeypatch):
    path, calls = flow
    state = host.load(path)
    state.update(touched=True, restore_started=True)
    host.save(path, state)

    def docker(*args, **kwargs):
        calls.append(("docker", *args))
        return "helper-id" if args[0] == "ps" else ""

    monkeypatch.setattr(host, "docker", docker)
    host.recover(path)
    remove_index = next(i for i, call in enumerate(calls) if call[:3] == ("docker", "rm", "-f"))
    assert remove_index < calls.index(("start", "prod", None))
    assert ("start", "stage", None) not in calls


def test_successful_recovery_is_a_noop(flow):
    path, calls = flow
    state = host.load(path)
    state["status"] = "completed"
    host.save(path, state)
    host.recover(path)
    assert calls == []


def test_copy_helper_only_mounts_source_readonly(tmp_path, stacks, monkeypatch):
    calls = []
    monkeypatch.setattr(host, "helper", lambda *args: calls.append(args))
    _, prod, _ = stacks
    host.copy_datasets(tmp_path, prod, "snapshot")
    assert len(calls) == 6
    assert all("dst=/src,readonly" in args[3][0] for args in calls)
    assert all("type=bind" in args[3][1] for args in calls)


@pytest.fixture
def configuration(monkeypatch):
    values = {
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "1234",
        "GITHUB_RUN_ATTEMPT": "1",
        "PRODUCTION_APPLICATION_UUID": "prod",
        "STAGING_APPLICATION_UUID": "stage",
        "DRY_RUN": "true",
        "GITHUB_REPOSITORY": "StromFLIX/nautionette",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_dry_run_default_and_confirmation_required(configuration, monkeypatch):
    assert runner.configuration() == ("prod", "stage", "1234-1", "dry-run")
    monkeypatch.setenv("DRY_RUN", "false")
    with pytest.raises(ValueError, match="OVERWRITE STAGING"):
        runner.configuration()
    monkeypatch.setenv("CONFIRMATION", "OVERWRITE STAGING")
    assert runner.configuration()[-1] == "apply"


def test_existing_run_is_not_relaunched(configuration, monkeypatch):
    monkeypatch.setenv("OPERATION", "check-existing-run")
    monkeypatch.setenv("EXISTING_RUN_ID", "old-2")
    assert runner.configuration() == ("prod", "stage", "old-2", "check")


@pytest.mark.parametrize(
    "name,value",
    [
        ("GITHUB_REF", "refs/heads/pr"),
        ("STAGING_APPLICATION_UUID", "prod"),
        ("GITHUB_RUN_ID", "../../oops"),
        ("DRY_RUN", "maybe"),
    ],
)
def test_runner_rejects_bad_dispatch(configuration, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        runner.configuration()


def test_bootstrap_is_valid_python_and_refuses_overwriting_previous_run():
    code = runner.bootstrap("print('no secrets')", "1234-1", "prod", "stage", "apply")
    compile(code, "bootstrap", "exec")
    assert "path.mkdir(mode=0o700)" in code
    assert "exist_ok=True" not in code.split("path.mkdir")[1].splitlines()[0]


def test_ssh_pins_host_identity_and_quotes_remote_args(tmp_path, configuration, monkeypatch):
    monkeypatch.setenv("REFRESH_SSH_HOST", "94.130.151.7")
    monkeypatch.setenv("REFRESH_SSH_USER", "root")
    monkeypatch.setenv("REFRESH_SSH_PRIVATE_KEY", "test-key")
    monkeypatch.setenv("REFRESH_SSH_KNOWN_HOSTS", "verified-host-key")
    ssh = runner.SSH(tmp_path)
    assert "StrictHostKeyChecking=yes" in ssh.args
    assert (tmp_path / "key").stat().st_mode & 0o777 == 0o600
    assert "test-key" not in str(ssh.args)


class GuardAPI:
    def __init__(self):
        self.description = "Original app description"
        self.calls = []

    def call(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        if method == "PATCH":
            self.description = payload["description"]
        return {"description": self.description, "git_repository": "StromFLIX/nautionette"}


def test_persistent_guard_roundtrip_preserves_description(configuration):
    api = GuardAPI()
    runner.guard_stage(api, "stage", "1234-1")
    assert api.description == "[nautionette-refresh:1234-1] Original app description"
    with pytest.raises(RuntimeError, match="already exists"):
        runner.guard_stage(api, "stage", "other-2")
    with pytest.raises(RuntimeError, match="changed"):
        runner.guard_stage(api, "stage", "other-2", clear=True)
    runner.guard_stage(api, "stage", "1234-1", clear=True)
    assert api.description == "Original app description"


@pytest.mark.parametrize("result", ["completed", "failed", "recovery_failed"])
def test_existing_run_only_polls_and_clears_guard_on_success(configuration, monkeypatch, result):
    monkeypatch.setenv("OPERATION", "check-existing-run")
    monkeypatch.setenv("EXISTING_RUN_ID", "old-2")
    calls = []

    class FakeSSH:
        def __init__(self, folder):
            pass

        def call(self, args, *unused, **kwargs):
            calls.append(args)
            return {"status": result}

    guard = GuardAPI()
    guard.description = "[nautionette-refresh:old-2] Original description"
    monkeypatch.setattr(runner, "SSH", FakeSSH)
    monkeypatch.setattr(runner, "Coolify", lambda: guard)
    if result == "completed":
        runner.main()
        assert guard.description == "Original description"
    else:
        with pytest.raises(RuntimeError, match="not successful"):
            runner.main()
        assert guard.calls == []
    assert calls == [[runner.ROOT + "/old-2/staging_refresh_host.py", "status", runner.ROOT + "/old-2"]]


def test_lost_launch_ack_prints_run_id_and_keeps_guard(configuration, monkeypatch, capsys):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("CONFIRMATION", "OVERWRITE STAGING")
    calls = []

    class FakeSSH:
        def __init__(self, folder):
            pass

        def call(self, *args, **kwargs):
            calls.append(args)
            raise RuntimeError("SSH acknowledgement lost")

    guard = GuardAPI()
    monkeypatch.setattr(runner, "SSH", FakeSSH)
    monkeypatch.setattr(runner, "Coolify", lambda: guard)
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        runner.main()
    assert len(calls) == 1  # Never launch a second snapshot after an ambiguous response.
    assert "Host refresh run ID: 1234-1" in capsys.readouterr().out
    assert guard.description.startswith("[nautionette-refresh:1234-1] ")


def test_workflows_share_concurrency_and_refresh_is_manual_only():
    import yaml

    root = Path(__file__).parents[1]
    refresh = yaml.safe_load((root / ".github/workflows/refresh-staging.yml").read_text())
    deploy = yaml.safe_load((root / ".github/workflows/deploy.yml").read_text())
    assert refresh["concurrency"] == deploy["concurrency"]
    # PyYAML's YAML 1.1 resolver considers the key 'on' a boolean.
    assert set(refresh.get("on", refresh.get(True))) == {"workflow_dispatch"}
    assert refresh["jobs"]["refresh"]["environment"] == "staging-refresh"
