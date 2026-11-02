"""Fixed mini-app Docker policy and crash-safe state machine (no Docker required)."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound
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
        if group["action"] == "SCMP_ACT_ALLOW"
        for name in group["names"]
    }
    assert not allowed.intersection(
        {"fallocate", "ioctl", "io_uring_setup", "io_uring_enter", "io_uring_register"}
    )
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
