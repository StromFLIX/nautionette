from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import APIError, NotFound
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


@pytest.mark.parametrize("status", ["exited", "dead"])
@pytest.mark.parametrize("already_removed", [False, True])
def test_claim_recovers_exited_container_on_same_volume(monkeypatch, status, already_removed):
    container = SimpleNamespace(
        attrs={"Mounts": [{"Name": "stage-projects"}], "State": {"Status": status}},
        remove=Mock(side_effect=NotFound("already removed") if already_removed else None),
    )
    containers = SimpleNamespace(list=Mock(return_value=[container]))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setattr(projects, "_claimed", set())
    monkeypatch.setattr(projects, "volume_name", lambda: "stage-projects")

    projects.claim(["a" * 32], "c" * 12)

    container.remove.assert_called_once_with()
    assert projects._claimed == {("c" * 12, "a" * 32)}


@pytest.mark.parametrize("status", ["running", "paused", "restarting", "created", "removing", "unknown"])
def test_claim_preserves_potentially_active_containers(monkeypatch, status):
    container = SimpleNamespace(
        attrs={"Mounts": [{"Name": "prod-projects"}], "State": {"Status": status}},
        remove=Mock(),
    )
    containers = SimpleNamespace(list=Mock(return_value=[container]))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setattr(projects, "_claimed", set())
    monkeypatch.setattr(projects, "volume_name", lambda: "prod-projects")

    with pytest.raises(ValueError, match="still in use"):
        projects.claim(["a" * 32], "c" * 12)

    container.remove.assert_not_called()
    assert projects._claimed == set()


@pytest.mark.parametrize("other_volume", [False, True])
def test_claim_preserves_exited_container_while_claimed_or_on_other_volume(monkeypatch, other_volume):
    claim = ("c" * 12, "a" * 32)
    container = SimpleNamespace(
        attrs={
            "Mounts": [{"Name": "stage-projects" if other_volume else "prod-projects"}],
            "State": {"Status": "exited"},
        },
        remove=Mock(),
    )
    containers = SimpleNamespace(list=Mock(return_value=[container]))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setattr(projects, "_claimed", set() if other_volume else {claim})
    monkeypatch.setattr(projects, "volume_name", lambda: "prod-projects")

    if other_volume:
        projects.claim([claim[1]], claim[0])
    else:
        with pytest.raises(ValueError, match="still in use"):
            projects.claim([claim[1]], claim[0])

    container.remove.assert_not_called()
    assert projects._claimed == {claim}


def test_claim_refuses_when_stopped_container_cannot_be_removed(monkeypatch):
    container = SimpleNamespace(
        attrs={"Mounts": [{"Name": "prod-projects"}], "State": {"Status": "exited"}},
        remove=Mock(side_effect=APIError("container restarted or removal denied")),
    )
    containers = SimpleNamespace(list=Mock(return_value=[container]))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setattr(projects, "_claimed", set())
    monkeypatch.setattr(projects, "volume_name", lambda: "prod-projects")

    with pytest.raises(APIError):
        projects.claim(["a" * 32], "c" * 12)

    container.remove.assert_called_once_with()
    assert projects._claimed == set()


def test_claim_refuses_active_and_surviving_containers(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    containers = SimpleNamespace(list=Mock(return_value=[]))
    monkeypatch.setattr(projects.daemon, "client", lambda: SimpleNamespace(containers=containers))
    monkeypatch.setattr(projects, "_claimed", set())
    monkeypatch.setattr(projects, "volume_name", lambda: "prod-projects")
    project_id = "a" * 32
    chat_id = "c" * 12
    other_chat = "d" * 12
    projects.claim([project_id], chat_id)
    with pytest.raises(ValueError):
        projects.claim([project_id], chat_id)
    projects.claim([project_id], other_chat)
    projects.release([project_id], other_chat)
    projects.release([project_id], chat_id)
    containers.list.return_value = [SimpleNamespace(attrs={"Mounts": [{"Name": "stage-projects"}]})]
    projects.claim([project_id], chat_id)
    projects.release([project_id], chat_id)
    containers.list.return_value = [SimpleNamespace(attrs={"Mounts": [{"Name": "prod-projects"}]})]
    with pytest.raises(ValueError):
        projects.claim([project_id], chat_id)
    assert projects._claimed == set()
    containers.list.assert_called_with(
        all=True, filters={"label": f"nautionette.project.{project_id}={chat_id}"}
    )
