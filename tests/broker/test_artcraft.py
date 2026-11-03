from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound
from fastapi.testclient import TestClient
from nautionette.artcraft import bundled_release, bundled_selection, release_installation_id
from nautionette_docker_broker import artcraft, daemon, images, main

from ..conftest import INTERNAL_TOKEN

ID = "b" * 32
SPEC = bundled_release("cadcraft")


@pytest.fixture
def docker(monkeypatch):
    monkeypatch.setattr(artcraft, "_verified", set())
    monkeypatch.setattr(artcraft, "_installer_bridge", None)
    monkeypatch.setattr(artcraft, "_installer_users", 0)
    container, volume, network = Mock(), Mock(), Mock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = json.dumps(SPEC).encode()
    network.name = "isolated-artcraft-installer"
    client = SimpleNamespace(containers=Mock(), volumes=Mock(), networks=Mock())
    client.containers.create.return_value = container
    client.volumes.create.return_value = volume
    client.volumes.get.side_effect = NotFound("missing")
    client.volumes.list.return_value = []
    client.networks.create.return_value = network
    client.networks.get.side_effect = NotFound("missing")
    client.networks.list.return_value = []
    monkeypatch.setattr(daemon, "client", lambda: client)
    monkeypatch.setattr(images, "has_image", lambda tag: True)
    monkeypatch.setattr(images, "snapshot", lambda: {"status": "ready"})
    return client, container, volume, network


def test_fixed_installer_has_no_socket_credentials_projects_or_generic_shell(docker):
    client, container, volume, network = docker
    assert artcraft.install(ID, SPEC) == SPEC
    args = client.containers.create.call_args.kwargs
    assert args["entrypoint"] == ["python3", "/usr/local/lib/nautionette/artcraft/install.py"]
    assert args["environment"] == {"ARTCRAFT_RELEASE": json.dumps(SPEC)}
    assert list(args["volumes"].values()) == [{"bind": "/artifact", "mode": "rw"}]
    assert args["read_only"] and args["network"] == network.name
    assert args["cap_drop"] == ["ALL"] and args["security_opt"] == ["no-new-privileges:true"]
    assert args["pids_limit"] == 128 and args["mem_limit"] == "2g" and args["nano_cpus"] == 1_000_000_000
    assert args["tmpfs"]["/tmp"].startswith("size=768m")  # noqa: S108 - container's private tmpfs
    assert args["labels"]["nautionette.artcraft.sha256"] == SPEC["sha256"]
    container.wait.assert_called_once_with(timeout=300)
    container.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()
    volume.remove.assert_not_called()


@pytest.mark.parametrize("failure", ["exit", "timeout", "different-spec", "invalid-json", "network"])
def test_failed_verification_reaps_unpublished_artifacts_and_releases_slot(docker, failure):
    client, container, volume, network = docker
    if failure == "exit":
        container.wait.return_value = {"StatusCode": 1}
    elif failure == "timeout":
        container.wait.side_effect = TimeoutError()
    elif failure == "different-spec":
        container.logs.return_value = json.dumps({**SPEC, "version": "9.9.9"}).encode()
    elif failure == "invalid-json":
        container.logs.return_value = b"bad"
    else:
        client.networks.create.side_effect = RuntimeError("network failure")
    with pytest.raises((ValueError, TimeoutError, RuntimeError)):
        artcraft.install(ID, SPEC)
    volume.remove.assert_called_once()
    if failure != "network":
        container.remove.assert_called_once_with(force=True)
        network.remove.assert_called_once()
    assert artcraft._slots.acquire(blocking=False)
    artcraft._slots.release()


def test_reuses_only_verified_existing_artifacts_and_bounds_volume_count(docker):
    client, container, volume, network = docker
    client.volumes.get.side_effect = None
    client.volumes.get.return_value = SimpleNamespace(attrs={"Labels": artcraft._labels(ID, SPEC)})
    assert artcraft.install(ID, SPEC) == SPEC
    args = client.containers.create.call_args.kwargs
    assert args["entrypoint"][-1] == "--verify"
    assert args["network"] == "none" and args["volumes"][artcraft.volume_name(ID)]["mode"] == "ro"
    client.volumes.create.assert_not_called()
    client.networks.create.assert_not_called()
    client.volumes.get.side_effect = NotFound("missing")
    client.volumes.list.return_value = [object()] * 128
    with pytest.raises(ValueError, match="storage limit"):
        artcraft.install(ID, SPEC)


def test_missing_images_and_parallel_installs_fail_without_creating_any_resources(docker, monkeypatch):
    client, *_ = docker
    monkeypatch.setattr(images, "has_image", lambda tag: False)
    with pytest.raises(ValueError, match="image is not ready"):
        artcraft.install(ID, SPEC)
    monkeypatch.setattr(images, "has_image", lambda tag: True)
    assert artcraft._slots.acquire(blocking=False) and artcraft._slots.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match="already running"):
            artcraft.install(ID, SPEC)
    finally:
        artcraft._slots.release()
        artcraft._slots.release()
    client.containers.create.assert_not_called()


def test_override_mounts_are_read_only_scoped_to_deployment_app_release_and_digest(docker, monkeypatch):
    client, *_ = docker
    item = {"app": "cadcraft", "release": SPEC, "installation_id": ID}
    with pytest.raises(NotFound):
        artcraft.mounts([item])
    client.volumes.get.side_effect = None
    client.volumes.get.return_value = SimpleNamespace(attrs={"Labels": artcraft._labels(ID, SPEC)})
    mount = artcraft.mounts([item])[0]
    assert mount["ReadOnly"] and mount["Target"] == "/opt/artcraft/cadcraft"
    assert mount["Source"] == artcraft.volume_name(ID)
    default = bundled_selection("cadcraft")
    client.volumes.get.return_value = SimpleNamespace(
        attrs={"Labels": artcraft._labels(default["installation_id"], SPEC)}
    )
    assert artcraft.mounts([{"app": "cadcraft", "release": SPEC}])[0]["Source"] == artcraft.volume_name(
        default["installation_id"]
    )
    for field in ("sha256", "version", "app"):
        labels = artcraft._labels(ID, SPEC)
        labels[f"nautionette.artcraft.{field}"] = "wrong"
        client.volumes.get.return_value = SimpleNamespace(attrs={"Labels": labels})
        with pytest.raises(ValueError, match="ownership/release"):
            artcraft.mounts([item])
    original = artcraft.volume_name(ID)
    monkeypatch.setattr(artcraft, "WORKFLOWS_VOLUME", "another-deployment")
    assert artcraft.volume_name(ID) != original
    with pytest.raises(ValueError):
        artcraft.volume_name("../../host")


def test_stale_helpers_are_reaped_but_immutable_artifacts_are_never_deleted(docker):
    client, container, volume, network = docker
    container.labels = {**artcraft._labels(ID, SPEC), "nautionette.artcraft.installer": "true"}
    container.attrs = {"Created": (datetime.now(UTC) - timedelta(seconds=600)).isoformat()}
    client.containers.list.return_value = [container]
    client.networks.get.side_effect = None
    client.networks.get.return_value = network
    artcraft.reconcile()
    container.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()
    client.volumes.get.assert_not_called()
    volume.remove.assert_not_called()


def test_only_authenticated_fixed_endpoint_can_install(docker, monkeypatch):
    calls = []
    monkeypatch.setattr(
        artcraft, "install", lambda identifier, spec: calls.append((identifier, spec)) or spec
    )
    client = TestClient(main.app)
    path = "/artcraft/install"
    body = {"installation_id": ID, "release": SPEC}
    assert client.post(path, json=body).status_code == 401
    headers = {"X-Internal-Token": INTERNAL_TOKEN}
    assert client.post(path, headers=headers, json={**body, "command": "sh"}).status_code == 422
    assert not calls
    assert client.post(path, headers=headers, json=body).json() == SPEC
    assert calls == [(ID, SPEC)]


def test_defaults_are_prepared_once_reverified_offline_and_pruned_volumes_are_detected(docker, monkeypatch):
    client, container, volume, _ = docker
    stored = {}
    monkeypatch.setattr(artcraft, "catalog", lambda: {"cadcraft": {}})

    def get(name):
        if name not in stored:
            raise NotFound("missing")
        return stored[name]

    def create(name, labels):
        stored[name] = SimpleNamespace(attrs={"Labels": labels})
        return volume

    client.volumes.get.side_effect = get
    client.volumes.create.side_effect = create
    assert not artcraft.bundled_ready()
    artcraft.ensure_bundled()
    assert artcraft.bundled_ready()
    assert client.containers.create.call_count == 1
    identifier = release_installation_id(SPEC)
    assert client.volumes.create.call_args.kwargs["name"] == artcraft.volume_name(identifier)
    assert artcraft.mounts([bundled_selection("cadcraft")])[0]["ReadOnly"]
    artcraft.ensure_bundled()
    assert client.containers.create.call_count == 1
    artcraft._verified.clear()  # broker restart must not blindly trust labels
    artcraft.ensure_bundled()
    assert client.containers.create.call_count == 2
    assert client.containers.create.call_args.kwargs["network"] == "none"
    assert client.volumes.create.call_count == 1
    stored.clear()
    assert not artcraft.bundled_ready()
    artcraft.ensure_bundled()
    assert client.volumes.create.call_count == 2 and artcraft.bundled_ready()


def test_partial_unreferenced_artifact_is_replaced_not_overwritten(docker):
    client, container, volume, _ = docker
    client.volumes.get.side_effect = None
    old = Mock(attrs={"Labels": artcraft._labels(ID, SPEC)})
    client.volumes.get.return_value = old
    container.wait.side_effect = [{"StatusCode": 1}, {"StatusCode": 0}]
    assert artcraft.install(ID, SPEC) == SPEC
    old.remove.assert_called_once_with()
    assert client.containers.create.call_count == 2
    assert client.containers.create.call_args_list[0].kwargs["network"] == "none"
    assert client.containers.create.call_args_list[1].kwargs["network"] != "none"


def test_invalid_artifact_in_use_cannot_be_deleted_or_rewritten(docker):
    client, container, *_ = docker
    client.volumes.get.side_effect = None
    old = Mock(attrs={"Labels": artcraft._labels(ID, SPEC)})
    old.remove.side_effect = RuntimeError("volume is in use")
    client.volumes.get.return_value = old
    container.wait.return_value = {"StatusCode": 1}
    with pytest.raises(RuntimeError, match="in use"):
        artcraft.install(ID, SPEC)
    client.volumes.create.assert_not_called()
    assert artcraft.volume_name(ID) not in artcraft._verified


def test_mismatched_labels_are_never_reused_or_removed(docker):
    client, *_ = docker
    client.volumes.get.side_effect = None
    old = Mock(attrs={"Labels": {**artcraft._labels(ID, SPEC), "nautionette.artcraft.sha256": "0" * 64}})
    client.volumes.get.return_value = old
    with pytest.raises(ValueError, match="ownership/release"):
        artcraft.install(ID, SPEC)
    old.remove.assert_not_called()
    client.containers.create.assert_not_called()


def test_two_installers_share_one_isolated_bridge_and_release_it_after_both_finish(docker):
    client, container, _, network = docker
    barrier = threading.Barrier(2)

    def wait(**kwargs):
        barrier.wait(timeout=5)
        return {"StatusCode": 0}

    container.wait.side_effect = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(artcraft.install, identifier, SPEC) for identifier in (ID, "c" * 32)]
        assert [future.result(timeout=10) for future in futures] == [SPEC, SPEC]
    client.networks.create.assert_called_once()
    args = client.networks.create.call_args.kwargs
    assert args["options"]["com.docker.network.bridge.enable_icc"] == "false"
    assert args["labels"]["nautionette.deployment"] == artcraft.WORKFLOWS_VOLUME
    assert args["labels"]["nautionette.artcraft.installer_network"] == "true"
    assert all(call.kwargs["network"] == network.name for call in client.containers.create.call_args_list)
    network.remove.assert_called_once()
    assert artcraft._installer_users == 0 and artcraft._installer_bridge is None


def test_unused_network_reaping_is_deployment_scoped_and_never_touches_active_networks(docker):
    client, *_ = docker
    labels = {
        "nautionette.deployment": artcraft.WORKFLOWS_VOLUME,
        "nautionette.artcraft.installer_network": "true",
    }
    old = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    stale = Mock(attrs={"Labels": labels, "Created": old, "Containers": {}})
    active = Mock(attrs={"Labels": labels, "Created": old, "Containers": {"helper": {}}})
    foreign = Mock(
        attrs={"Labels": {**labels, "nautionette.deployment": "other"}, "Created": old, "Containers": {}}
    )
    application = Mock(
        attrs={
            "Labels": {"nautionette.deployment": artcraft.WORKFLOWS_VOLUME},
            "Created": old,
            "Containers": {},
        }
    )
    recent = Mock(attrs={"Labels": labels, "Created": datetime.now(UTC).isoformat(), "Containers": {}})
    client.networks.list.return_value = [stale, active, foreign, application, recent]
    artcraft._reap_unused_networks()
    stale.remove.assert_called_once()
    for network in (active, foreign, application, recent):
        network.remove.assert_not_called()


def test_preexisting_shared_network_must_have_exact_ownership_and_icc_disabled(docker):
    client, *_ = docker
    client.networks.get.side_effect = None
    foreign = Mock(attrs={"Labels": {}, "Driver": "bridge", "Options": {}})
    client.networks.get.return_value = foreign
    with pytest.raises(ValueError, match="ownership/isolation"):
        artcraft.install(ID, SPEC)
    client.containers.create.assert_not_called()
    foreign.remove.assert_not_called()
