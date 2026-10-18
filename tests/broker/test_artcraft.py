from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound
from fastapi.testclient import TestClient
from nautionette.artcraft import bundled_release
from nautionette_docker_broker import artcraft, daemon, images, main

from ..conftest import INTERNAL_TOKEN

ID = "b" * 32
SPEC = bundled_release("cadcraft")


@pytest.fixture
def docker(monkeypatch):
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
    monkeypatch.setattr(daemon, "client", lambda: client)
    monkeypatch.setattr(images, "has_image", lambda tag: True)
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


def test_never_overwrites_an_existing_artifact_and_bounds_volume_count(docker):
    client, *_ = docker
    client.volumes.get.side_effect = None
    with pytest.raises(ValueError, match="never overwritten"):
        artcraft.install(ID, SPEC)
    client.volumes.create.assert_not_called()
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
    assert artcraft.mounts([{"app": "cadcraft", "release": SPEC}]) == []
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
