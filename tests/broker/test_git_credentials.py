import io
import json
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import APIError
from fastapi.testclient import TestClient
from nautionette_docker_broker import agent_run, git_credentials, main

from ..conftest import INTERNAL_TOKEN
from .test_chat_recovery import agents as agents

CREDENTIALS = [{"full_name": "owner/repo", "token": "sensitive-token", "expires_at": "2099-01-01T00:00:00Z"}]
HEADERS = {"X-Internal-Token": INTERNAL_TOKEN}


@pytest.fixture
def target(agents, monkeypatch):
    add, _ = agents
    container = add()
    container.put_archive = Mock(return_value=True)
    container.exec_run.return_value = SimpleNamespace(exit_code=0)
    monkeypatch.setattr(agent_run, "_credential_scopes", {("chat", "turn"): {"owner/repo"}})
    monkeypatch.setattr(agent_run, "_credential_locks", {("chat", "turn"): threading.Lock()})
    agent_run._stopped[("chat", "turn")] = threading.Event()
    return container


def test_private_initial_file_is_not_agent_replaceable():
    container = Mock()
    git_credentials.prepare(container, CREDENTIALS, {"owner/repo"})
    path, archive = container.put_archive.call_args.args
    assert path == "/tmp"  # noqa: S108
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        directory, token = tar.getmembers()
        assert directory.isdir() and directory.uid == 0 and directory.mode == 0o711
        assert token.name == "nautionette-git-credentials/current.json"
        assert (token.uid, token.gid, token.mode) == (0, 10001, 0o440)
        assert json.loads(tar.extractfile(token).read()) == CREDENTIALS
    container.exec_run.assert_not_called()


def test_refresh_stages_private_data_then_atomically_renames_without_secrets_in_argv(target):
    calls = Mock()
    calls.attach_mock(target.put_archive, "copy")
    calls.attach_mock(target.exec_run, "rename")
    assert agent_run.refresh_project_credentials("chat", "turn", CREDENTIALS)
    path, archive = target.put_archive.call_args.args
    assert path == git_credentials.DIRECTORY
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        entry = tar.getmembers()[0]
        assert entry.name == "pending.json"
        assert (entry.uid, entry.gid, entry.mode) == (0, 10001, 0o440)
        assert json.loads(tar.extractfile(entry).read()) == CREDENTIALS
    assert "sensitive-token" not in str(target.exec_run.call_args)
    assert target.exec_run.call_args.kwargs == {"user": "0:0"}
    assert "renameSync" in target.exec_run.call_args.args[0][-1]
    assert [call[0] for call in calls.mock_calls] == ["copy", "rename"]


@pytest.mark.parametrize("condition", ["wrong-chat", "wrong-turn", "stopped", "retired", "orphan", "foreign"])
def test_refresh_only_targets_the_exact_live_owned_turn(target, condition):
    chat, turn = "chat", "turn"
    if condition == "wrong-chat":
        chat = "other"
    elif condition == "wrong-turn":
        turn = "other"
    elif condition == "stopped":
        agent_run._stopped[(chat, turn)].set()
    elif condition == "retired":
        agent_run._retired[(chat, turn)] = 100
    elif condition == "orphan":
        agent_run._stopped.clear()  # Broker restart never renews an orphan.
    else:
        target.attrs["Mounts"][0]["Name"] = "staging-workflows"
    assert not agent_run.refresh_project_credentials(chat, turn, CREDENTIALS)
    target.put_archive.assert_not_called()
    target.exec_run.assert_not_called()


def test_a_stalled_turn_does_not_block_other_chats(target, agents):
    add, _ = agents
    other = add(chat="other-chat")
    other.put_archive = Mock(return_value=True)
    other.exec_run.return_value = SimpleNamespace(exit_code=0)
    agent_run._stopped[("other-chat", "turn")] = threading.Event()
    agent_run._credential_scopes[("other-chat", "turn")] = {"owner/repo"}
    agent_run._credential_locks[("other-chat", "turn")] = threading.Lock()
    copying, release = threading.Event(), threading.Event()

    def copy(*args):
        copying.set()
        assert release.wait(2)
        return True

    target.put_archive.side_effect = copy
    with ThreadPoolExecutor() as executor:
        blocked = executor.submit(agent_run.refresh_project_credentials, "chat", "turn", CREDENTIALS)
        try:
            assert copying.wait(2)
            independent = executor.submit(
                agent_run.refresh_project_credentials, "other-chat", "turn", CREDENTIALS
            )
            assert independent.result(timeout=1)
        finally:
            release.set()
        assert blocked.result(timeout=2)


def test_rotation_cannot_add_or_replace_repository_scope(target):
    with pytest.raises(ValueError, match="scope"):
        agent_run.refresh_project_credentials("chat", "turn", [CREDENTIALS[0] | {"full_name": "owner/other"}])
    target.put_archive.assert_not_called()


@pytest.mark.parametrize("failure", ["copy", "rename", "docker"])
def test_delivery_errors_are_retryable_and_redacted(target, failure):
    if failure == "copy":
        target.put_archive.return_value = False
    elif failure == "rename":
        target.exec_run.return_value = SimpleNamespace(exit_code=1, output=b"sensitive-token")
    else:
        target.put_archive.side_effect = APIError("sensitive-token")
    response = TestClient(main.app).post(
        "/agent/project-credentials",
        headers=HEADERS,
        json={"chat_id": "chat", "turn_id": "turn", "credentials": CREDENTIALS},
    )
    assert response.status_code == 503
    assert "sensitive-token" not in response.text
    target.put_archive.side_effect = None
    target.put_archive.return_value = True
    target.exec_run.return_value = SimpleNamespace(exit_code=0)
    assert agent_run.refresh_project_credentials("chat", "turn", CREDENTIALS)


def test_credentials_endpoint_requires_internal_auth():
    client = TestClient(main.app)
    for headers in ({}, {"X-Internal-Token": "wrong"}):
        assert client.post("/agent/project-credentials", json={}, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "credentials",
    [
        None,
        [],
        CREDENTIALS * 21,
        CREDENTIALS * 2,
        [CREDENTIALS[0] | {"token": "secret\nusername=other"}],
        [CREDENTIALS[0] | {"expires_at": "secret-invalid-date"}],
        [CREDENTIALS[0] | {"expires_at": "2099-01-01T00:00:00"}],
        [CREDENTIALS[0] | {"full_name": "../owner/repo"}],
    ],
)
def test_invalid_batches_are_rejected_without_echoing_payload(credentials):
    response = TestClient(main.app).post(
        "/agent/project-credentials",
        headers=HEADERS,
        json={"chat_id": "chat", "turn_id": "turn", "credentials": credentials},
    )
    assert response.status_code == 422
    assert "secret" not in response.text
