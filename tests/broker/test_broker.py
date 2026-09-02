"""The only container with the Docker socket. Fixed verbs, no shell, no generic run."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from nautionette_docker_broker import agent_run, config, daemon, images, main, workers

from ..conftest import INTERNAL_TOKEN

HEADERS = {"X-Internal-Token": INTERNAL_TOKEN}


class FakeContainer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.restarts: list[int] = []

    def restart(self, timeout: int = 0) -> None:
        self.restarts.append(timeout)


class FakeDocker:
    """Just enough of the Docker API for the verbs this broker exposes."""

    def __init__(self) -> None:
        self.tags: set[str] = set()
        self.built: list[tuple[str, str]] = []
        self.listed: list[FakeContainer] = []
        self.own_labels: dict[str, str] = {"com.docker.compose.project": "nautionette"}
        self.last_filters: dict | None = None
        self.up = True
        broker = self

        class Images:
            def get(self, tag):
                if tag not in broker.tags:
                    from docker.errors import ImageNotFound

                    raise ImageNotFound(tag)
                return SimpleNamespace(
                    tag=lambda repository, alias: broker.tags.add(f"{repository}:{alias}")
                )

            def build(self, path, tag, **_kwargs):
                broker.built.append((path, tag))
                broker.tags.add(tag)
                return None, []

        class Containers:
            def get(self, _name):
                if not broker.up:
                    raise RuntimeError("docker is unreachable")
                return SimpleNamespace(labels=broker.own_labels)

            def list(self, filters=None):
                broker.last_filters = filters
                return list(broker.listed)

            def run(self, *_args, **_kwargs):
                raise AssertionError("no test should really start a container")

        self.images = Images()
        self.containers = Containers()

    def ping(self):
        if not self.up:
            raise RuntimeError("docker is unreachable")
        return True


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(daemon, "client", lambda: fake)
    monkeypatch.setattr(
        images, "image_state", {"status": "pending", "images": {}, "log": [], "error": None}
    )
    return fake


@pytest.fixture
def agent_images(tmp_path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "agent-images"
    (root / "pi-base").mkdir(parents=True)
    (root / "pi-base" / "Dockerfile").write_text("FROM scratch\n")
    (root / "agent-sets" / "default").mkdir(parents=True)
    (root / "agent-sets" / "default" / "Dockerfile").write_text("FROM base\n")
    (root / "agent-sets" / "notes").mkdir(parents=True)  # no Dockerfile, so not an agent set
    monkeypatch.setattr(images, "AGENT_IMAGES_DIR", str(root))
    return root


@pytest.fixture
def client(docker) -> TestClient:
    return TestClient(main.app)


# --------------------------------------------------------------------- images


def test_only_a_directory_with_a_dockerfile_is_an_agent_set(agent_images):
    assert images.discovered_agent_sets() == ["default"]


def test_an_image_is_tagged_by_the_hash_of_what_built_it(agent_images):
    first = images.image_tag("default")
    assert first == images.image_tag("default")
    (agent_images / "agent-sets" / "default" / "Dockerfile").write_text("FROM base\nRUN echo new\n")
    assert images.image_tag("default") != first


def test_a_change_to_the_base_retags_every_agent_set(agent_images):
    first = images.image_tag("default")
    (agent_images / "pi-base" / "Dockerfile").write_text("FROM scratch\nRUN echo new\n")
    assert images.image_tag("default") != first


def test_an_agent_set_that_is_not_there_has_no_hash_to_take(agent_images):
    assert images.image_tag("missing").endswith(":missing")


def test_images_are_built_once_and_then_left_alone(agent_images, docker):
    images.ensure_images()
    assert [tag for _path, tag in docker.built] == [
        f"{config.IMAGE_PREFIX}base:{images.base_hash()}",
        images.image_tag("default"),
    ]
    docker.built.clear()
    images.ensure_images()
    assert docker.built == []


def test_a_build_that_fails_is_reported_never_fatal(agent_images, docker, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("no space left on device")

    monkeypatch.setattr(docker.images, "build", explode)
    images.ensure_images(force=True)
    state = images.snapshot()
    assert state["status"] == "failed"
    assert "no space left" in state["error"]


# --------------------------------------------------------------------- health


def test_health_reports_the_images_it_holds(client, agent_images, docker):
    images.ensure_images()
    payload = client.get("/healthz").json()
    assert payload["docker"] is True
    assert payload["image_status"] == "ready"
    assert payload["images"]["default"] == images.image_tag("default")


def test_a_docker_that_is_not_there_degrades_rather_than_crashes(client, docker):
    docker.up = False
    assert client.get("/healthz").json() == {
        "status": "degraded",
        "docker": False,
        "error": "docker is unreachable",
    }


# ----------------------------------------------------------------------- auth


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/agent-sets"), ("post", "/images/rebuild"), ("post", "/worker/restart")],
)
def test_every_verb_needs_the_internal_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"X-Internal-Token": "wrong"}).status_code == 401


def test_health_is_the_one_thing_that_is_open(client, docker):
    assert client.get("/healthz").status_code == 200


def test_the_agent_sets_report_whether_their_image_is_there(client, agent_images, docker):
    assert client.get("/agent-sets", headers=HEADERS).json() == {
        "agent_sets": [{"name": "default", "image": images.image_tag("default"), "ready": False}]
    }
    images.ensure_images()
    assert client.get("/agent-sets", headers=HEADERS).json()["agent_sets"][0]["ready"] is True


# ------------------------------------------------------------------ agent run


def frames(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_an_agent_set_nobody_declared_is_refused(client, agent_images):
    response = client.post("/agent/run", headers=HEADERS, json={"agent_set": "made-up"})
    assert frames(response) == [{"type": "error", "message": "unknown agent set 'made-up'"}]


def test_an_image_that_vanished_is_built_while_the_caller_is_told_what_is_happening(
    client, agent_images, docker, monkeypatch
):
    monkeypatch.setattr(agent_run, "BUILD_POLL_SECONDS", 0.01)
    response = client.post("/agent/run", headers=HEADERS, json={"agent_set": "default"})
    statuses = [frame for frame in frames(response) if frame["type"] == "status"]
    assert statuses and statuses[0]["state"] == "building"
    assert statuses[0]["message"] == agent_run.BUILD_STAGES[0]
    # The call waited for the build rather than handing back a "try again".
    assert images.image_tag("default") in [tag for _path, tag in docker.built]


def test_a_build_that_will_never_finish_says_so_instead_of_hanging(
    client, agent_images, docker, monkeypatch
):
    monkeypatch.setattr(agent_run, "BUILD_POLL_SECONDS", 0.01)
    monkeypatch.setattr(images, "start_build", lambda force=False: False)
    images.image_state["status"] = "failed"
    images.image_state["error"] = "no space left on device"
    response = client.post("/agent/run", headers=HEADERS, json={"agent_set": "default"})
    assert frames(response) == [
        {"type": "error", "message": "the agent image failed to build: no space left on device"}
    ]


# -------------------------------------------------------------- worker restart


def test_a_broker_that_cannot_place_itself_refuses_to_restart_anything(client, docker):
    docker.up = False
    response = client.post("/worker/restart", headers=HEADERS)
    assert response.status_code == 503
    assert "cannot tell which stack it belongs to" in response.json()["detail"]


def test_a_restart_is_scoped_to_this_broker_own_stack(client, docker):
    docker.listed = [FakeContainer("nautionette-worker-1")]
    result = client.post("/worker/restart", headers=HEADERS).json()
    assert result["restarted"] == ["nautionette-worker-1"]
    assert docker.last_filters == {
        "label": [config.WORKER_LABEL, "com.docker.compose.project=nautionette"]
    }


def test_a_restart_lets_in_flight_activities_drain(client, docker):
    container = FakeContainer("nautionette-worker-1")
    docker.listed = [container]
    client.post("/worker/restart", headers=HEADERS)
    assert container.restarts == [config.STOP_GRACE]


def test_nothing_to_restart_is_reported_not_an_error(client, docker):
    result = client.post("/worker/restart", headers=HEADERS).json()
    assert result["restarted"] == []
    assert "no container matched" in result["detail"]


def test_an_override_lets_a_broker_that_cannot_read_its_own_label_still_work(docker, monkeypatch):
    docker.up = False
    monkeypatch.setattr(workers, "PROJECT_OVERRIDE", "explicit")
    assert workers.worker_filters() == {
        "label": [config.WORKER_LABEL, "com.docker.compose.project=explicit"]
    }
