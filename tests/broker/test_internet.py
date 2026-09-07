from types import SimpleNamespace

import pytest
from nautionette_docker_broker import agent_run


@pytest.mark.parametrize("allowed", [False, True, "true"])
def test_chat_network_requires_explicit_grant(monkeypatch, allowed):
    calls = []
    container = SimpleNamespace(
        start=lambda: calls.append("start"),
        logs=lambda **kwargs: iter([]),
        wait=lambda **kwargs: {"StatusCode": 0},
        remove=lambda **kwargs: None,
        kill=lambda: None,
    )

    def create(image, **kwargs):
        assert kwargs["network"] == agent_run.AGENT_NETWORK
        assert kwargs["cap_drop"] == ["ALL"]
        return container

    docker = SimpleNamespace(
        containers=SimpleNamespace(create=create),
        networks=SimpleNamespace(
            get=lambda name: SimpleNamespace(
                attrs={"Internal": True}, connect=lambda target: calls.append("connect")
            )
        ),
    )
    monkeypatch.setattr(agent_run.daemon, "client", lambda: docker)
    monkeypatch.setattr(agent_run.images, "discovered_agent_sets", lambda: ["default"])
    monkeypatch.setattr(agent_run.images, "has_image", lambda tag: True)
    monkeypatch.setattr(agent_run.images, "image_tag", lambda name: "test-image")
    list(agent_run.run({"chat_id": "chat-a", "internet_allowed": allowed}))
    assert calls == (["connect", "start"] if allowed is True else ["start"])


@pytest.mark.parametrize("allowed", [False, True])
def test_decision_targets_only_the_requesting_turn(monkeypatch, allowed):
    calls = []
    container = SimpleNamespace(
        reload=lambda: None,
        labels={"nautionette.chat": "chat-a", "nautionette.turn": "turn-a"},
        attrs={
            "NetworkSettings": {"Networks": {}},
            "Mounts": [{"Type": "volume", "Name": agent_run.WORKFLOWS_VOLUME, "Destination": "/workflows"}],
        },
        exec_run=lambda command: calls.append(command) or SimpleNamespace(exit_code=0),
    )

    def list_containers(all, filters):
        assert all is False
        assert filters == {"label": ["nautionette.chat=chat-a", "nautionette.turn=turn-a"]}
        return [container]

    docker = SimpleNamespace(
        containers=SimpleNamespace(list=list_containers),
        networks=SimpleNamespace(
            get=lambda name: SimpleNamespace(connect=lambda target: calls.append("connect"))
        ),
    )
    monkeypatch.setattr(agent_run.daemon, "client", lambda: docker)
    assert agent_run.decide_internet("chat-a", "turn-a", allowed)
    assert ("connect" in calls) is allowed
    assert calls[-1][:2] == ["node", "-e"]
    assert ('"allowed"' if allowed else '"denied"') in calls[-1][-1]


def test_chat_container_has_no_service_credential():
    environment = agent_run._environment({"chat_id": "chat-a"})
    assert "INTERNAL_TOKEN" not in environment
    assert "BACKEND_URL" not in environment


def test_misconfigured_chat_network_fails_closed(monkeypatch):
    docker = SimpleNamespace(
        networks=SimpleNamespace(get=lambda name: SimpleNamespace(attrs={"Internal": False}))
    )
    monkeypatch.setattr(agent_run.daemon, "client", lambda: docker)
    monkeypatch.setattr(agent_run.images, "discovered_agent_sets", lambda: ["default"])
    monkeypatch.setattr(agent_run.images, "has_image", lambda tag: True)
    monkeypatch.setattr(agent_run.images, "image_tag", lambda name: "test-image")
    events = list(agent_run.run({"chat_id": "chat-a"}))
    assert any("egress disabled" in event for event in events)
