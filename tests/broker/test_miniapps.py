"""Fixed mini-app Docker policy and crash-safe state machine (no Docker required)."""

import contextlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import DockerException, NotFound
from fastapi import HTTPException
from nautionette_docker_broker import miniapps as apps


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(apps, "ROOT", tmp_path)
    monkeypatch.setattr(apps, "INTERNAL_TOKEN", "test-internal")
    client = Mock()
    monkeypatch.setattr(apps.daemon, "client", lambda: client)
    return client


def config(revision=1):
    return {
        "revision": revision,
        "image": f"image-{revision}",
        "token": f"token-{revision}",
        "memory_mb": 64,
        "mode": "on-demand",
    }


def test_runtime_mounts_and_limits_are_fixed(runtime, monkeypatch):
    monkeypatch.setattr(apps, "network", lambda app_id: SimpleNamespace(name="isolated"))
    monkeypatch.setattr(apps, "host_path", lambda path: str(path))
    runtime.images.get.return_value.labels = apps.labels("one")
    apps.create_runtime("one", config())
    args = runtime.containers.create.call_args.kwargs
    assert args["read_only"] and args["user"] == "65532:65532"
    assert args["cap_drop"] == ["ALL"] and "no-new-privileges:true" in args["security_opt"]
    profile = json.loads(
        next(v.removeprefix("seccomp=") for v in args["security_opt"] if v.startswith("seccomp="))
    )
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    allowed = {
        name
        for group in profile["syscalls"]
        if group["action"] == "SCMP_ACT_ALLOW" and not group.get("args")
        for name in group["names"]
    }
    assert not allowed.intersection(
        {"fallocate", "ioctl", "io_uring_setup", "io_uring_enter", "io_uring_register"}
    )
    ioctl_rules = [g for g in profile["syscalls"] if "ioctl" in g["names"]]
    assert ioctl_rules == [
        {
            "names": ["ioctl"],
            "action": "SCMP_ACT_ALLOW",
            "args": [{"index": 1, "value": 0x5421, "op": "SCMP_CMP_EQ"}],
        }
    ]
    assert args["sysctls"] == {
        "net.ipv6.conf.all.disable_ipv6": "1",
        "net.ipv6.conf.default.disable_ipv6": "1",
    }
    assert args["nano_cpus"] == 250000000 and args["pids_limit"] == 32
    assert args["mem_limit"] == args["memswap_limit"] == "64m"
    assert set(
        args["volumes"][str(apps.root("one") / "1" / filename)]["bind"]
        for filename in ["state.db", "state.db-wal", "state.db-shm"]
    ) == {"/data/state.db", "/data/state.db-wal", "/data/state.db-shm"}
    assert len(args["volumes"]) == 3 and "ports" not in args and "privileged" not in args
    assert set(args["environment"]) == {"MINIAPP_TOKEN"}
    assert args["ulimits"][0]["Hard"] == 16 * 1024 * 1024
    assert args["log_config"]["Config"]["max-size"] == "256k"


def test_plain_internal_network_and_old_engines_fail_closed(runtime):
    runtime.version.return_value = {"Version": "27.5.1"}
    with pytest.raises(ValueError, match="28"):
        apps.network("one")
    runtime.version.return_value = {"Version": "28.5.2"}
    runtime.networks.get.return_value.attrs = {"Internal": True, "Labels": apps.labels("one")}
    with pytest.raises(RuntimeError, match="ownership"):
        apps.network("one")
    runtime.networks.get.side_effect = NotFound("no network")
    net = Mock()
    net.attrs = {
        "Internal": True,
        "EnableIPv6": False,
        "Labels": apps.labels("one"),
        "Options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
    }
    runtime.networks.create.return_value = net
    runtime.containers.get.return_value.attrs = {"NetworkSettings": {"Networks": {}}}
    assert apps.network("one") is net
    assert (
        runtime.networks.create.call_args.kwargs["options"]["com.docker.network.bridge.gateway_mode_ipv4"]
        == "isolated"
    )


@pytest.mark.parametrize("status", ["running", "exited"])
def test_existing_runtime_reattaches_broker_before_health_and_rpc(runtime, monkeypatch, status):
    container = Mock(status=status)
    container.attrs = {"NetworkSettings": {"Networks": {apps.name("one", "net"): {"IPAddress": "10.0.0.2"}}}}
    me = Mock()
    me.attrs = {"NetworkSettings": {"Networks": {}}}
    runtime.version.return_value = {"Version": "28.5.2"}
    net = runtime.networks.get.return_value
    net.attrs = {
        "Internal": True,
        "EnableIPv6": False,
        "Labels": apps.labels("one"),
        "Options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
    }
    monkeypatch.setattr(apps, "owned_container", lambda *args: container)

    def attach(broker, aliases):
        assert broker is me and aliases == ["miniapp-host"]
        me.attrs["NetworkSettings"]["Networks"][apps.name("one", "net")] = {}

    runtime.containers.get.return_value = me
    net.connect.side_effect = attach

    def http(method, url, headers, payload, *, timeout):
        assert apps.name("one", "net") in me.attrs["NetworkSettings"]["Networks"]
        return 200, {"status": "ok"} if url.endswith("/healthz") else {"value": 1}

    invoke = Mock(side_effect=http)
    monkeypatch.setattr(apps, "bounded_http", invoke)
    apps.ready("one", config())
    assert apps.runtime_call("one", config(), "/rpc", {"method": "POST"}) == {"value": 1}
    net.connect.assert_called_once()
    runtime.containers.create.assert_not_called()
    assert invoke.call_count == 2  # Health plus exactly one RPC, never a mutation replay.
    if status == "running":
        container.start.assert_not_called()
    else:
        container.start.assert_called()


def test_existing_runtime_network_policy_failure_never_dispatches(runtime, monkeypatch):
    monkeypatch.setattr(apps, "network", Mock(side_effect=RuntimeError("ownership mismatch")))
    invoke = Mock()
    monkeypatch.setattr(apps, "bounded_http", invoke)
    with pytest.raises(RuntimeError, match="ownership"):
        apps.runtime_call("one", config(), "/rpc", {"method": "POST"})
    invoke.assert_not_called()
    runtime.containers.get.assert_not_called()


def test_fresh_database_is_wal_ready_with_readonly_parent(runtime):
    apps.data_files("one", 1, None)
    folder = apps.root("one") / "1"
    assert (folder / "state.db").read_bytes()[18:20] == b"\x02\x02"
    assert {p.name for p in folder.iterdir()} == {"state.db", "state.db-wal", "state.db-shm"}
    folder.chmod(0o555)
    try:
        with contextlib.closing(sqlite3.connect(folder / "state.db")) as conn:
            assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
            conn.execute("CREATE TABLE smoke(value INTEGER)")
            conn.execute("INSERT INTO smoke VALUES(1)")
            conn.commit()
            assert conn.execute("SELECT value FROM smoke").fetchone()[0] == 1
    finally:
        folder.chmod(0o755)


def test_failed_health_preserves_previous_database(runtime, monkeypatch):
    old = config()
    apps.data_files("one", 1, None)
    with (apps.root("one") / "1" / "state.db").open("ab") as handle:
        handle.write(b"unchanged")
    original = (apps.root("one") / "1" / "state.db").read_bytes()
    apps.save("one", {"active": old})
    monkeypatch.setattr(apps, "build", lambda *args: "image-2")
    monkeypatch.setattr(apps, "stop", lambda *args, **kwargs: None)

    def health(app_id, candidate):
        if candidate["revision"] == 2:
            (apps.root(app_id) / "2" / "state.db").write_bytes(b"destroyed by bad migration")
            raise ValueError("bad health")

    monkeypatch.setattr(apps, "ready", health)
    with pytest.raises(ValueError, match="bad health"):
        apps.deploy("one", 2, "bad health source", 64, "on-demand")
    assert apps.load("one") == {"active": old}
    assert (apps.root("one") / "1" / "state.db").read_bytes() == original
    assert not (apps.root("one") / "2").exists()
    runtime.images.remove.assert_called_with(apps.name("one", "release-2"))


def test_interrupted_candidate_discarded_not_promoted(runtime, monkeypatch):
    stopped = []
    monkeypatch.setattr(apps, "stop", lambda app_id, config, **kwargs: stopped.append(config["revision"]))
    apps.data_files("one", 1, None)
    apps.data_files("one", 2, config(1))
    saved = {"active": config(1), "candidate": config(2)}
    apps.save("one", saved)
    recovered = apps.recover("one", apps.load("one"))
    assert recovered == apps.load("one") == {"active": config(1)}
    assert stopped == [2] and not (apps.root("one") / "2").exists()


def test_activation_retirement_survives_crash_and_failed_unlink(runtime, monkeypatch):
    apps.data_files("one", 1, None)
    apps.data_files("one", 2, config(1))
    journal = {"active": config(2), "retiring": config(1)}
    apps.save("one", journal)
    stopped = Mock()
    monkeypatch.setattr(apps, "stop", stopped)
    original = apps.shutil.rmtree
    monkeypatch.setattr(apps.shutil, "rmtree", Mock(side_effect=OSError("disk busy")))
    with pytest.raises(OSError):
        apps.recover("one", apps.load("one"))
    assert apps.load("one") == journal
    monkeypatch.setattr(apps.shutil, "rmtree", original)
    assert apps.recover("one", apps.load("one")) == {"active": config(2)}
    assert not (apps.root("one") / "1").exists()
    assert (apps.root("one") / "2" / "state.db").exists()
    assert all(call.args[1]["revision"] == 1 for call in stopped.call_args_list)


def test_ambiguous_activation_commit_is_reconciled_not_rolled_back(runtime, monkeypatch):
    apps.data_files("one", 1, None)
    apps.save("one", {"active": config(1)})
    original_save = apps.save

    def lose_commit_reply(app_id, state):
        original_save(app_id, state)
        if state.get("retiring"):
            raise OSError("directory fsync failed after rename")

    monkeypatch.setattr(apps, "save", lose_commit_reply)
    monkeypatch.setattr(apps, "build", Mock(return_value="image-2"))
    monkeypatch.setattr(apps, "ready", Mock())
    stop = Mock()
    monkeypatch.setattr(apps, "stop", stop)
    with pytest.raises(OSError):
        apps.deploy("one", 2, "source", 64, "on-demand")
    assert apps.load("one")["active"]["revision"] == 2
    assert not any(call.args[1]["revision"] == 2 for call in stop.call_args_list)
    monkeypatch.setattr(apps, "save", original_save)
    recovered = apps.recover("one", apps.load("one"))
    assert recovered["active"]["revision"] == 2 and "retiring" not in recovered
    assert (apps.root("one") / "2" / "state.db").exists()


def test_failed_candidate_cleanup_keeps_journal_for_retry(runtime, monkeypatch):
    apps.data_files("one", 1, None)
    apps.data_files("one", 2, config(1))
    journal = {"active": config(1), "candidate": config(2)}
    apps.save("one", journal)
    monkeypatch.setattr(apps, "stop", Mock())
    original = apps.shutil.rmtree
    monkeypatch.setattr(apps.shutil, "rmtree", Mock(side_effect=OSError("disk busy")))
    with pytest.raises(OSError):
        apps.recover("one", apps.load("one"))
    assert apps.load("one") == journal
    monkeypatch.setattr(apps.shutil, "rmtree", original)
    assert apps.recover("one", apps.load("one")) == {"active": config(1)}
    assert (apps.root("one") / "1" / "state.db").exists()


def test_suspend_rotates_token_preserves_data_and_revokes_requests(runtime, monkeypatch):
    apps.data_files("one", 1, None)
    apps.save("one", {"active": config()})
    monkeypatch.setattr(apps, "stop", lambda *args, **kwargs: None)
    monkeypatch.setattr(apps, "snapshot", lambda *args: {"revision": 1, "status": "suspended"})
    apps.control("one", "suspend")
    saved = apps.load("one")
    assert saved["disabled"] and saved["active"]["token"] != "token-1"
    assert (apps.root("one") / "1" / "state.db").exists()
    with pytest.raises(ValueError, match="not active"):
        apps.request("one", 1, {})
    with pytest.raises(HTTPException) as error:
        apps.capability("token-1", {})
    assert error.value.status_code == 401


def test_interrupted_removal_remains_revoked_and_retryable(runtime, monkeypatch):
    apps.save("one", {"active": config()})
    stop = Mock(side_effect=RuntimeError("daemon unavailable"))
    monkeypatch.setattr(apps, "stop", stop)
    with pytest.raises(RuntimeError):
        apps.remove("one")
    assert apps.load("one")["disabled"] and apps.load("one")["deleting"]
    stop.side_effect = None
    runtime.networks.get.side_effect = NotFound("already removed")
    assert apps.remove("one") == {"removed": True}
    assert not apps.root("one").exists()
    assert stop.call_count == 2


@pytest.mark.parametrize("app_id", ["../escape", "/absolute", "UPPER", "x/y", "a" * 49, ""])
def test_invalid_ids_never_reach_docker(runtime, app_id):
    with pytest.raises(ValueError):
        apps.root(app_id)
    runtime.containers.get.assert_not_called()


def test_host_mount_resolves_only_broker_volume(runtime):
    runtime.containers.get.return_value.attrs = {
        "Mounts": [{"Destination": str(apps.ROOT), "Type": "bind", "Source": "/etc"}]
    }
    with pytest.raises(RuntimeError, match="volume"):
        apps.host_path(apps.root("one") / "1" / "state.db")
    runtime.containers.get.return_value.attrs = {
        "Mounts": [
            {"Destination": str(apps.ROOT), "Type": "volume", "Source": "/var/lib/docker/volumes/test/_data"}
        ]
    }
    assert (
        apps.host_path(apps.root("one") / "1" / "state.db")
        == "/var/lib/docker/volumes/test/_data/one/1/state.db"
    )


def test_runtime_credentials_are_rejected_before_admin_json_parse(monkeypatch):
    from fastapi.testclient import TestClient
    from nautionette_docker_broker import main

    monkeypatch.setattr(main, "INTERNAL_TOKEN", "host-control-only")
    response = TestClient(main.app).post(
        "/agent/run",
        content=b"{" * 131072,
        headers={"Content-Type": "application/json", "Authorization": "Bearer app-only"},
    )
    assert response.status_code == 401


def test_runtime_http_has_total_deadline_not_only_idle_timeout(monkeypatch):
    import asyncio

    import httpx

    original = httpx.AsyncClient
    calls = []

    async def drip(request):
        calls.append(request)
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(drip), **kw)
    )
    with pytest.raises(TimeoutError):
        apps.bounded_http("GET", "http://example.test/healthz", {}, None, timeout=0.01)
    assert len(calls) == 1


def test_failed_compilation_journal_is_cleaned_without_touching_active_data(runtime, monkeypatch):
    apps.save("one", {"active": config()})
    monkeypatch.setattr(apps, "build", Mock(side_effect=ValueError("compile failed")))
    with pytest.raises(ValueError, match="compile failed"):
        apps.deploy("one", 2, "source", 64, "on-demand")
    assert apps.load("one") == {"active": config()}
    runtime.images.remove.assert_called_with(apps.name("one", "release-2"))


def test_recovery_reaps_only_namespace_owned_orphan_compilers(runtime):
    compiler = Mock()
    runtime.containers.list.return_value = [compiler]
    apps.reconcile()
    compiler.remove.assert_called_once_with(force=True)
    labels = runtime.containers.list.call_args.kwargs["filters"]["label"]
    assert "nautionette.miniapp.build=true" in labels
    assert f"nautionette.deployment={apps.WORKFLOWS_VOLUME}" in labels
    assert not any("unused" in entry for entry in labels)


def test_compiler_reads_private_tmpfs_and_ignores_forgeable_log_markers(runtime, monkeypatch):
    compiler = runtime.containers.create.return_value
    compiler.status = "running"
    compiler.logs.return_value = b"BUILD_READY"
    compiler.exec_run.side_effect = [
        Mock(exit_code=1),
        Mock(exit_code=0),
        Mock(output=iter([b"\x7fELF", b"controlled-artifact"])),
    ]
    runtime.images.build.return_value = (Mock(id="scratch-image"), [])
    monkeypatch.setattr(apps.time, "sleep", Mock())
    assert apps.build("one", 1, "pub fn health() {}") == "scratch-image"
    assert compiler.exec_run.call_count == 3
    assert compiler.exec_run.call_args_list[0].args == (["test", "-f", "/tmp/build.ready"],)  # noqa: S108
    assert compiler.exec_run.call_args.kwargs == {"stream": True, "stderr": False}
    compiler.get_archive.assert_not_called()
    compiler.remove.assert_called_once_with(force=True)


def test_compile_check_discards_artifact_without_images_data_or_runtime(runtime):
    compiler = runtime.containers.create.return_value
    compiler.status = "running"
    compiler.exec_run.side_effect = [
        Mock(exit_code=0),
        Mock(output=iter([b"\x7fELF", b"controlled-artifact"])),
    ]
    result = apps.build("source-check", 1, "pub fn health() {}", validate_only=True)
    assert result == {"valid": True, "artifact_bytes": 23, "sdk_fingerprint": apps.BUILDER_HASH}
    runtime.images.build.assert_not_called()
    runtime.images.remove.assert_not_called()
    compiler.remove.assert_called_once_with(force=True)
    assert not apps.root("source-check").exists()
    assert runtime.containers.create.call_args.kwargs["network_mode"] == "none"


def test_failed_docker_initialization_releases_the_compiler_slot(runtime, monkeypatch):
    monkeypatch.setattr(apps.daemon, "client", Mock(side_effect=DockerException("sensitive daemon error")))
    with pytest.raises(apps.CompilerUnavailable) as error:
        apps.build("source-check", 1, "pub fn health() {}", validate_only=True)
    assert error.value.detail["stage"] == "docker"
    assert "sensitive" not in str(error.value.detail)
    assert apps._build_slot.acquire(blocking=False)
    apps._build_slot.release()


def test_registry_failure_is_bounded_and_tagging_is_explicit(runtime, monkeypatch):
    monkeypatch.setattr(apps, "AGENT_IMAGE_REGISTRY_PREFIX", "ghcr.io/example/app/pi-")
    runtime.images.get.side_effect = NotFound("absent")
    runtime.images.pull.side_effect = DockerException("registry credential must not leak")
    with pytest.raises(apps.CompilerUnavailable) as error:
        apps.build("source-check", 1, "pub fn health() {}", validate_only=True)
    assert error.value.detail["stage"] == "image"
    assert "credential" not in str(error.value.detail)
    runtime.images.pull.side_effect = None
    runtime.images.pull.return_value.tag.return_value = False
    with pytest.raises(apps.CompilerUnavailable):
        apps.build("source-check", 1, "pub fn health() {}", validate_only=True)
    repository, _, version = apps.BUILDER.rpartition(":")
    runtime.images.pull.return_value.tag.assert_called_once_with(repository, version)
    runtime.containers.create.assert_not_called()


@pytest.mark.parametrize(
    "prefix,parent",
    [
        ("ghcr.io/example/app/pi-", "ghcr.io/example/app"),
        ("ghcr.io/example/app/custom-", "ghcr.io/example/app"),
        ("ghcr.io/example/app/", "ghcr.io/example/app"),
        ("registry.example:5000/org/name/pi-", "registry.example:5000/org/name"),
    ],
)
def test_compiler_is_pulled_beside_agent_images_not_under_the_pi_prefix(runtime, monkeypatch, prefix, parent):
    monkeypatch.setattr(apps, "AGENT_IMAGE_REGISTRY_PREFIX", prefix)
    runtime.images.get.side_effect = NotFound("absent")
    compiler = runtime.containers.create.return_value
    compiler.status = "running"
    compiler.exec_run.side_effect = [Mock(exit_code=0), Mock(output=iter([b"\x7fELFartifact"]))]
    result = apps.build("source-check", 1, "pub fn health() {}", validate_only=True)
    assert result["artifact_bytes"] > 0
    runtime.images.pull.assert_called_once_with(f"{parent}/miniapp-builder:{apps.BUILDER_HASH}")
    runtime.images.pull.return_value.tag.assert_called_once_with(*apps.BUILDER.rsplit(":", 1))
    runtime.images.build.assert_not_called()


def test_runtime_snapshot_reports_resources_without_starting_sleeping_apps(runtime, monkeypatch):
    apps.save("one", {"active": config()})
    container = Mock(status="running")
    container.attrs = {"State": {"OOMKilled": True, "ExitCode": 137, "StartedAt": "2026-11-02T00:00:00Z"}}
    container.stats.return_value = {
        "memory_stats": {"usage": 1234},
        "pids_stats": {"current": 3},
        "cpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 50}, "system_cpu_usage": 500},
    }
    monkeypatch.setattr(apps, "owned_container", lambda *args: container)
    runtime.images.get.return_value.attrs = {"Size": 42}
    result = apps.snapshot("one")
    assert result["memory_bytes"] == 1234 and result["cpu_percent"] == 20.0
    assert result["pids"] == 3 and result["cpu_limit"] == 0.25
    assert result["oom_killed"] and result["exit_code"] == 137
    container.start.assert_not_called()
    container.status = "exited"
    container.stats.reset_mock()
    result = apps.snapshot("one")
    assert result["status"] == "suspended" and result["memory_bytes"] == 0
    assert result["cpu_percent"] is None
    container.stats.assert_not_called()
    container.start.assert_not_called()


def test_app_locks_do_not_collide_or_retain_deleted_ids():
    first = apps.lock("one")
    assert apps.lock("one") is first
    assert apps.lock("other") is not first
    del first
    assert "one" not in apps._locks


def test_failed_file_removal_keeps_revoked_tombstone(runtime, monkeypatch):
    apps.save("one", {"active": config()})
    monkeypatch.setattr(apps, "stop", Mock())
    runtime.networks.get.side_effect = NotFound("absent")
    original = apps.shutil.rmtree
    monkeypatch.setattr(apps.shutil, "rmtree", Mock(side_effect=OSError("filesystem unavailable")))
    with pytest.raises(OSError):
        apps.remove("one")
    assert apps.load("one")["deleting"] and apps.load("one")["disabled"]
    monkeypatch.setattr(apps.shutil, "rmtree", original)
    assert apps.remove("one") == {"removed": True}
    assert not apps.root("one").exists()
