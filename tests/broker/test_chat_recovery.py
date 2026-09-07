import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import APIError, NotFound
from fastapi.testclient import TestClient
from nautionette_docker_broker import agent_run, chat_agents, main, projects

from ..conftest import INTERNAL_TOKEN


@pytest.fixture
def agents(monkeypatch):
    pool = []
    monkeypatch.setattr(agent_run, "_stopped", {})
    monkeypatch.setattr(agent_run, "_completed", {})
    monkeypatch.setattr(agent_run, "_retired", {})
    monkeypatch.setattr(projects, "_claimed", set())
    monkeypatch.setattr(projects, "volume_name", lambda: "production-projects")
    monkeypatch.setattr(
        agent_run.daemon,
        "client",
        lambda: SimpleNamespace(containers=SimpleNamespace(list=lambda **kwargs: list(pool))),
    )

    def add(chat="chat", turn="turn", volume=None, state="running"):
        container = SimpleNamespace(
            labels={"nautionette.chat": chat, "nautionette.turn": turn},
            attrs={
                "Mounts": [
                    {
                        "Type": "volume",
                        "Name": volume or chat_agents.WORKFLOWS_VOLUME,
                        "Destination": "/workflows",
                    },
                    {"Type": "volume", "Name": "production-projects", "Destination": "/projects"},
                ],
                "State": {"Status": state},
            },
            kill=Mock(),
            exec_run=Mock(),
            reload=Mock(),
        )
        container.remove = Mock(side_effect=lambda **kwargs: pool.remove(container))
        pool.append(container)
        return container

    return add, pool


@pytest.mark.parametrize("state", ["running", "created", "paused", "exited", "dead"])
def test_cleanup_after_broker_restart_removes_only_exact_deployment_chat_turn(agents, state):
    add, pool = agents
    orphan = add(state=state)
    staging = add(volume="staging-workflows")  # Copied chat AND turn IDs.
    other_turn = add(turn="active")
    other_chat = add(chat="other")
    workflow = add(chat="", turn="")
    agent_run.cleanup_chat("chat", "turn")
    orphan.remove.assert_called_once_with(force=True)
    assert pool == [staging, other_turn, other_chat, workflow]
    for untouched in pool:
        untouched.remove.assert_not_called()
    agent_run.cleanup_chat("chat", "turn")  # Already absent is a successful retry.


def test_scope_is_verified_by_mount_not_a_copied_label(agents):
    add, _ = agents
    foreign = add(volume="staging-workflows")
    foreign.labels["nautionette.deployment"] = chat_agents.WORKFLOWS_VOLUME
    assert agent_run.chat_inventory() == []
    assert not agent_run.control("chat", "turn", {"type": "stop"})
    assert not agent_run.decide_internet("chat", "turn", True)
    foreign.kill.assert_not_called()
    foreign.exec_run.assert_not_called()


def test_recovery_releases_surviving_docker_worktree_lock_without_deleting_edits(agents, tmp_path):
    add, _ = agents
    orphan = add(chat="c" * 12)
    project = "a" * 32
    orphan.labels[f"nautionette.project.{project}"] = "c" * 12
    saved = tmp_path / "unfinished.txt"
    saved.write_text("uncommitted changes")
    with pytest.raises(ValueError, match="still in use"):
        projects.claim([project], "c" * 12)
    agent_run.cleanup_chat("c" * 12, "turn")
    projects.claim([project], "c" * 12)
    assert saved.read_text() == "uncommitted changes"
    projects.release([project], "c" * 12)


def test_cleanup_waits_for_in_process_claim_release(agents, monkeypatch):
    add, _ = agents
    orphan = add()
    stopped, completed = threading.Event(), threading.Event()
    agent_run._stopped[("chat", "turn")] = stopped
    agent_run._completed[("chat", "turn")] = completed
    projects._claimed.add(("chat", "project"))
    original_remove = orphan.remove.side_effect

    def remove(**kwargs):
        assert stopped.is_set()
        original_remove(**kwargs)
        projects.release(["project"], "chat")
        completed.set()

    orphan.remove.side_effect = remove
    agent_run.cleanup_chat("chat", "turn")
    assert not projects._claimed
    assert completed.is_set()


def test_cleanup_timeout_does_not_bypass_in_process_claim(agents, monkeypatch):
    agent_run._stopped[("chat", "turn")] = threading.Event()
    agent_run._completed[("chat", "turn")] = threading.Event()
    projects._claimed.add(("chat", "project"))
    monkeypatch.setattr(agent_run, "CLEANUP_TIMEOUT", 0)
    with pytest.raises(RuntimeError, match="not released"):
        agent_run.cleanup_chat("chat", "turn")
    assert projects._claimed == {("chat", "project")}


def test_inventory_includes_image_waiters_but_not_workflows_or_other_deployments(agents):
    add, _ = agents
    add()
    add(volume="staging", chat="foreign")
    add(chat="", turn="")
    agent_run._stopped[("waiting", "image")] = threading.Event()
    assert agent_run.chat_inventory() == [
        {"chat_id": "chat", "turn_id": "turn"},
        {"chat_id": "waiting", "turn_id": "image"},
    ]
    assert agent_run.chat_inventory("waiting") == [{"chat_id": "waiting", "turn_id": "image"}]


def test_cleanup_fences_a_delayed_run_request(agents, monkeypatch):
    agent_run.cleanup_chat("chat", "turn")
    run = Mock()
    monkeypatch.setattr(agent_run, "_run", run)
    result = [json.loads(event) for event in agent_run.run({"chat_id": "chat", "turn_id": "turn"})]
    assert result == [{"type": "error", "message": "This turn was already cleaned up"}]
    run.assert_not_called()


def test_cleanup_racing_container_creation_prevents_a_late_start(agents, monkeypatch):
    add, pool = agents
    creating, release_create = threading.Event(), threading.Event()
    container = add()
    pool.clear()  # Docker has not made the container visible yet.
    container.start = Mock()

    def create(*args, **kwargs):
        creating.set()
        assert release_create.wait(2)
        pool.append(container)
        return container

    monkeypatch.setattr(
        agent_run.daemon,
        "client",
        lambda: SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: list(pool), create=create),
            networks=SimpleNamespace(get=lambda name: SimpleNamespace(attrs={"Internal": True})),
        ),
    )
    monkeypatch.setattr(agent_run.images, "discovered_agent_sets", lambda: ["default"])
    monkeypatch.setattr(agent_run.images, "has_image", lambda tag: True)
    monkeypatch.setattr(agent_run.images, "image_tag", lambda name: "test-image")
    with ThreadPoolExecutor() as executor:
        run = executor.submit(lambda: list(agent_run.run({"chat_id": "chat", "turn_id": "turn"})))
        try:
            assert creating.wait(2)
            cleanup = executor.submit(agent_run.cleanup_chat, "chat", "turn")
            assert agent_run._stopped[("chat", "turn")].wait(2)
            assert not cleanup.done()
        finally:
            release_create.set()
        run.result(timeout=2)
        cleanup.result(timeout=2)
    container.start.assert_not_called()
    assert pool == []
    assert not agent_run._stopped
    assert not agent_run._completed


def test_cleanup_failure_is_retryable_and_never_acknowledged_as_success(agents):
    add, _ = agents
    orphan = add()
    original_remove = orphan.remove.side_effect
    orphan.remove.side_effect = APIError("daemon unavailable")
    client = TestClient(main.app)
    payload = {"chat_id": "chat", "turn_id": "turn"}
    headers = {"X-Internal-Token": INTERNAL_TOKEN}
    assert client.post("/agent/cleanup", json=payload, headers=headers).status_code == 503
    assert agent_run.chat_inventory() == [payload]
    orphan.remove.side_effect = original_remove
    assert client.post("/agent/cleanup", json=payload, headers=headers).json() == {"ok": True}


def test_cleanup_tolerates_concurrent_removal_but_verifies_absence(agents):
    add, pool = agents
    orphan = add()

    def remove(**kwargs):
        pool.remove(orphan)
        raise NotFound("already removed")

    orphan.remove.side_effect = remove
    agent_run.cleanup_chat("chat", "turn")
    lingering = add()
    lingering.remove.side_effect = None
    with pytest.raises(RuntimeError, match="still present"):
        agent_run.cleanup_chat("chat", "turn")


@pytest.mark.parametrize("path", ["/agent/chats", "/agent/cleanup"])
def test_cleanup_and_inventory_require_internal_auth(path):
    client = TestClient(main.app)
    method = client.get if path.endswith("chats") else client.post
    kwargs = {} if path.endswith("chats") else {"json": {}}
    assert method(path, **kwargs).status_code == 401
    assert method(path, headers={"X-Internal-Token": "wrong"}, **kwargs).status_code == 401


@pytest.mark.parametrize("payload", [{}, {"chat_id": "chat"}, {"chat_id": "chat", "turn_id": 123}])
def test_cleanup_rejects_ambiguous_targets(payload):
    response = TestClient(main.app).post(
        "/agent/cleanup", json=payload, headers={"X-Internal-Token": INTERNAL_TOKEN}
    )
    assert response.status_code == 422
