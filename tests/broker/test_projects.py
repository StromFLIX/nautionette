from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound
from nautionette_docker_broker import projects


def test_mounts_only_selected_checkouts(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    broker = SimpleNamespace(
        attrs={
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": "deployment-prefixed-projects",
                    "Destination": str(tmp_path),
                }
            ]
        }
    )
    containers = SimpleNamespace(get=Mock(return_value=broker))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setattr(projects.socket, "gethostname", lambda: "broker-container")
    selected = "a" * 32
    hidden = "b" * 32
    chat_id = "c" * 12
    (tmp_path / selected / ".git").mkdir(parents=True)
    (tmp_path / hidden).mkdir()
    (tmp_path / ".sessions" / selected / chat_id).mkdir(parents=True)
    mounts = projects.mounts([selected, selected], chat_id)
    assert len(mounts) == 2
    assert {mount["Source"] for mount in mounts} == {"deployment-prefixed-projects"}
    assert mounts[0]["Target"] == f"/project-repositories/{selected}"
    assert mounts[0]["ReadOnly"] is False
    assert mounts[0]["VolumeOptions"] == {"Subpath": f"{selected}/.git"}
    assert mounts[1]["VolumeOptions"] == {"Subpath": f".sessions/{selected}/{chat_id}"}
    assert projects.mounts([]) == []
    containers.get.assert_called_once_with("broker-container")


@pytest.mark.parametrize(
    "mounted",
    [
        [],
        [{"Type": "volume", "Name": "other-volume", "Destination": "/other"}],
        [{"Type": "bind", "Source": "/host/projects", "Destination": "/projects"}],
        [{"Type": "volume", "Destination": "/projects"}],
    ],
)
def test_project_volume_discovery_refuses_missing_or_unsupported_mounts(monkeypatch, mounted):
    monkeypatch.setattr(projects, "PROJECTS_DIR", projects.Path("/projects"))
    containers = SimpleNamespace(get=Mock(return_value=SimpleNamespace(attrs={"Mounts": mounted})))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    with pytest.raises(ValueError, match="requires a named Docker volume"):
        projects.volume_name()


def test_project_volume_discovery_never_falls_back_to_a_different_volume(monkeypatch):
    containers = SimpleNamespace(get=Mock(side_effect=NotFound("unknown broker")))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setenv("PROJECTS_VOLUME", "wrong-volume")
    with pytest.raises(ValueError, match="Cannot inspect the broker's project volume"):
        projects.volume_name()


@pytest.mark.parametrize("project_id", ["../escape", "/etc", "", "a" * 32])
def test_unavailable_or_invalid_checkouts_are_rejected(tmp_path, monkeypatch, project_id):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    with pytest.raises(ValueError):
        projects.mounts([project_id], "c" * 12)


def test_symlink_checkouts_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    (tmp_path / ("a" * 32)).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        projects.mounts(["a" * 32], "c" * 12)


def test_claim_refuses_active_and_surviving_containers(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    containers = SimpleNamespace(list=Mock(return_value=[]))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setattr(projects, "_claimed", set())
    project_id = "a" * 32
    chat_id = "c" * 12
    other_chat = "d" * 12
    projects.claim([project_id], chat_id)
    with pytest.raises(ValueError):
        projects.claim([project_id], chat_id)
    projects.claim([project_id], other_chat)
    projects.release([project_id], other_chat)
    projects.release([project_id], chat_id)
    containers.list.return_value = [object()]
    with pytest.raises(ValueError):
        projects.claim([project_id], chat_id)
    assert projects._claimed == set()
    containers.list.assert_called_with(
        all=True, filters={"label": f"nautionette.project.{project_id}={chat_id}"}
    )
