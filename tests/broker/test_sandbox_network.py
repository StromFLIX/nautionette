from types import SimpleNamespace

import pytest
import yaml
from nautionette_docker_broker import agent_run, main


@pytest.mark.parametrize("job", [{"chat_id": "chat-a"}, {"chat_id": "chat-b", "internet_allowed": False}, {}])
def test_agents_start_without_a_network_decision(monkeypatch, job):
    calls = []
    container = SimpleNamespace(
        start=lambda: calls.append("start"),
        logs=lambda **kwargs: iter([]),
        wait=lambda **kwargs: {"StatusCode": 0},
        remove=lambda **kwargs: None,
        kill=lambda: None,
    )

    def create(image, **kwargs):
        expected = agent_run.AGENT_NETWORK if job.get("chat_id") else agent_run.TARGET_NETWORK
        assert kwargs["network"] == expected
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges:true"]
        return container

    # No network attach/detach or decision delivery API is needed to start.
    docker = SimpleNamespace(containers=SimpleNamespace(create=create))
    monkeypatch.setattr(agent_run.daemon, "client", lambda: docker)
    monkeypatch.setattr(agent_run.images, "discovered_agent_sets", lambda: ["default"])
    monkeypatch.setattr(agent_run.images, "has_image", lambda tag: True)
    monkeypatch.setattr(agent_run.images, "image_tag", lambda name: "test-image")
    events = list(agent_run.run(job))
    assert not any('"type": "error"' in event for event in events)
    assert calls == ["start"]


def test_chat_container_has_no_service_credential():
    environment = agent_run._environment({"chat_id": "chat-a"})
    assert "INTERNAL_TOKEN" not in environment
    assert "BACKEND_URL" not in environment


def test_compose_sandbox_network_has_outbound_access_without_control_plane_membership(repo_root):
    compose = yaml.safe_load((repo_root / "docker-compose.yaml").read_text())
    networks = compose["networks"]
    assert not networks["agents"].get("internal", False)
    assert not networks["internal"].get("internal", False)  # Workflow agents also have outbound access.
    assert "agent-egress" not in networks
    assert networks["agents"]["name"] == "${STACK_NAMESPACE:-nautionette}-agent-sandboxes"
    services = compose["services"]
    assert services["docker-broker"]["environment"]["AGENT_NETWORK"] == networks["agents"]["name"]
    assert "AGENT_EGRESS_NETWORK" not in services["docker-broker"]["environment"]
    assert "agents" in services["agentgateway"]["networks"]
    assert "agents" not in services["backend"]["networks"]
    assert "agents" not in services["docker-broker"]["networks"]
    assert networks["control"]["internal"] is True
    assert networks["data"]["internal"] is True


def test_retired_decision_endpoint_is_not_registered():
    assert "/agent/internet" not in {route.path for route in main.app.routes}
