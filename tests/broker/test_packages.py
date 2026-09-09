from __future__ import annotations

import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound
from nautionette.pi_packages import configuration, filters, installation_source
from nautionette_docker_broker import agent_run, daemon, images, packages

ID = "a" * 32


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("npm:example@1.2.3", {"kind": "npm", "name": "example", "spec": "example@1.2.3"}),
        ("npm:@scope/name@next", {"kind": "npm", "name": "@scope/name", "spec": "@scope/name@next"}),
        (
            "git:github.com/org/repo@v1",
            {"kind": "git", "url": "https://github.com/org/repo.git", "ref": "v1"},
        ),
        (
            "https://github.com/org/repo.git",
            {"kind": "git", "url": "https://github.com/org/repo.git", "ref": "HEAD"},
        ),
    ],
)
def test_sources_are_data_not_commands(source, expected):
    assert installation_source(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "npm:--help",
        "npm:@scope",
        "npm:owner/name",
        "npm:example;echo bad",
        "https://github.com/org/repo@--help",
        "https://github.com/org/repo@../bad",
        "npm:example@file:/tmp/x",
        "http://169.254.169.254/pkg",
        "https://user:password@github.com/org/repo",
        "/local/pkg",
        None,
    ],
)
def test_rejects_sources_that_need_a_general_shell_or_network_target(source):
    with pytest.raises(ValueError):
        installation_source(source)


def test_filters_and_configuration_are_bounded_and_cannot_replace_core_controls():
    assert filters({"extensions": [], "skills": ["skills/*", "!skills/old"]})["extensions"] == []
    for key in ("GIT_CONFIG", "NODE_OPTIONS", "NAUTIONETTE_TOOLS", "PATH", "INTERNAL_TOKEN", "HOME"):
        with pytest.raises(ValueError):
            configuration({"env": {key: "value"}})
    for path in ("agent/../settings.json", "agent/settings.json", "agent/extensions/a.js", "/etc/profile"):
        with pytest.raises(ValueError):
            configuration({"files": {path: "value"}})


@pytest.fixture
def docker(monkeypatch):
    container = Mock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = json.dumps(
        {
            "root": "npm/node_modules/example",
            "resolved": "npm:example@1",
            "resources": {"prompts": ["prompts"]},
        }
    ).encode()
    network = Mock(name="network")
    network.name = "installer-network"
    volume = Mock()
    client = SimpleNamespace(containers=Mock(), networks=Mock(), volumes=Mock())
    client.containers.create.return_value = container
    client.networks.create.return_value = network
    client.volumes.get.side_effect = NotFound("missing")
    client.volumes.create.return_value = volume
    monkeypatch.setattr(daemon, "client", lambda: client)
    monkeypatch.setattr(images, "has_image", lambda tag: True)
    return client, container, network, volume


def test_installer_has_only_its_artifact_no_application_credentials_or_mounts(docker):
    client, container, network, volume = docker
    assert packages.install(ID, "npm:example")["resolved"] == "npm:example@1"
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["entrypoint"] == ["node", "/usr/local/lib/nautionette/package-install.mjs"]
    assert set(kwargs["environment"]) == {"PACKAGE_SOURCE", "PACKAGE_SCRIPTS"}
    assert kwargs["environment"]["PACKAGE_SCRIPTS"] == "false"
    assert list(kwargs["volumes"].values()) == [{"bind": "/artifact", "mode": "rw"}]
    assert kwargs["network"] == "installer-network"
    assert kwargs["read_only"] and kwargs["pids_limit"] == 128
    assert kwargs["cap_drop"] == ["ALL"]
    container.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()
    volume.remove.assert_not_called()


@pytest.mark.parametrize("failure", ["exit", "timeout", "bad-json"])
def test_install_failure_reaps_container_network_and_unpublished_artifact(docker, failure):
    client, container, network, volume = docker
    if failure == "exit":
        container.wait.return_value = {"StatusCode": 1}
    elif failure == "timeout":
        container.wait.side_effect = TimeoutError()
    else:
        container.logs.return_value = b"invalid"
    with pytest.raises((ValueError, TimeoutError)):
        packages.install(ID, "npm:example")
    container.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()
    volume.remove.assert_called_once()


def test_never_overwrites_existing_artifact(docker):
    client, container, network, volume = docker
    client.volumes.get.side_effect = None
    with pytest.raises(ValueError, match="never overwritten"):
        packages.install(ID, "npm:example")
    client.volumes.create.assert_not_called()


def test_read_only_mounts_are_namespaced_and_missing_artifacts_fail(docker, monkeypatch):
    client, *_ = docker
    with pytest.raises(NotFound):
        packages.mounts([ID])
    client.volumes.get.side_effect = None
    client.volumes.get.return_value = SimpleNamespace(attrs={"Labels": {"nautionette.package": ID}})
    mount = packages.mounts([ID])[0]
    assert mount["ReadOnly"] and mount["Target"].endswith(ID)
    name = packages.volume_name(ID)
    monkeypatch.setattr(packages, "WORKFLOWS_VOLUME", "another-stack")
    assert packages.volume_name(ID) != name
    with pytest.raises(ValueError):
        packages.mounts(["../../host"])


def test_private_runtime_delivery_is_mode_0600_archive_not_job_environment():
    container = Mock()
    job = {"prompt": "hi", "project_ids": ["p"], "packages": [ID]}
    runtime = [{"configuration": {"env": {"SERVICE_API_KEY": "secret-value"}}}]
    agent_run._copy_job(container, job, "nautionette-packages.json", runtime)
    assert "secret-value" not in json.dumps(agent_run._environment(job))
    with tarfile.open(fileobj=io.BytesIO(container.put_archive.call_args.args[1])) as archive:
        entry = archive.getmembers()[0]
        assert entry.name == "nautionette-packages.json" and entry.mode == 0o600 and entry.uid == 10001
        assert json.load(archive.extractfile(entry)) == runtime
