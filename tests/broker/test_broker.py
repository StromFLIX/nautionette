"""The only container with the Docker socket. Fixed verbs, no shell, no generic run."""

from __future__ import annotations

import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.models.containers import ContainerCollection
from fastapi.testclient import TestClient
from nautionette_docker_broker import agent_run, config, daemon, images, main, monitor, workers

from ..conftest import INTERNAL_TOKEN

HEADERS = {"X-Internal-Token": INTERNAL_TOKEN}


class FakeContainer:
    def __init__(self, name: str, status: str = "running", health: str = "healthy") -> None:
        self.name = name
        self.restarts: list[int] = []
        self.starts = 0
        self.attrs = {"State": {"Status": status, "Health": {"Status": health}}}

    def start(self) -> None:
        self.starts += 1
        self.attrs["State"]["Status"] = "running"

    def restart(self, timeout: int = 0) -> None:
        self.restarts.append(timeout)
        self.attrs["State"]["Health"]["Status"] = "starting"


class FakeDocker:
    """Just enough of the Docker API for the verbs this broker exposes."""

    def __init__(self) -> None:
        self.tags: set[str] = set()
        self.built: list[tuple[str, str]] = []
        self.listed: list[FakeContainer] = []
        self.own_labels: dict[str, str] = {"com.docker.compose.project": "nautionette"}
        self.last_filters: dict | None = None
        self.up = True
        self.runs: list[dict] = []
        broker = self

        class Images:
            def get(self, tag):
                if tag not in broker.tags:
                    from docker.errors import ImageNotFound

                    raise ImageNotFound(tag)
                return SimpleNamespace(tag=lambda repository, alias: broker.tags.add(f"{repository}:{alias}"))

            def build(self, path, tag, **_kwargs):
                broker.built.append((path, tag))
                broker.tags.add(tag)
                return None, []

        class Containers:
            def get(self, _name):
                if not broker.up:
                    raise RuntimeError("docker is unreachable")
                return SimpleNamespace(labels=broker.own_labels)

            def list(self, filters=None, all=False):
                if not broker.up:
                    raise RuntimeError("docker is unreachable")
                broker.last_filters = filters
                return list(broker.listed)

            def run(self, image, **kwargs):
                if image != workers.WORKER_IMAGE:
                    raise AssertionError("no test should really start an agent container")
                broker.runs.append({"image": image, **kwargs})
                container = FakeContainer(kwargs["name"])
                broker.listed.append(container)
                return container

        self.images = Images()
        self.containers = Containers()

    def ping(self):
        if not self.up:
            raise RuntimeError("docker is unreachable")
        return True


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(workers, "last_error", None)
    monkeypatch.setattr(workers, "PROJECT_OVERRIDE", "")
    monkeypatch.setattr(workers, "WORKER_REPLICAS", 1)
    monkeypatch.setattr(daemon, "client", lambda: fake)
    monkeypatch.setattr(images, "image_state", {"status": "pending", "images": {}, "log": [], "error": None})
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


def test_pruned_images_are_detected_and_rebuilt(agent_images, docker, monkeypatch):
    images.ensure_images()
    tag = images.image_tag("default")
    docker.tags.remove(tag)
    docker.built.clear()
    assert images.snapshot()["status"] == "missing"
    monkeypatch.setattr(images, "start_build", lambda: images.ensure_images())
    images.reconcile()
    assert images.snapshot()["status"] == "ready"
    assert [built_tag for _path, built_tag in docker.built] == [tag]


def test_a_pruned_base_alias_is_restored_without_rebuilding(agent_images, docker, monkeypatch):
    images.ensure_images()
    docker.tags.remove(config.BASE_IMAGE)
    docker.built.clear()
    monkeypatch.setattr(images, "start_build", lambda: images.ensure_images())
    images.reconcile()
    assert config.BASE_IMAGE in docker.tags
    assert docker.built == []


# --------------------------------------------------------------------- health


def test_health_reports_the_images_it_holds(client, agent_images, docker):
    images.ensure_images()
    payload = client.get("/healthz").json()
    assert payload["docker"] is True
    assert payload["image_status"] == "ready"
    assert payload["images"]["default"] == images.image_tag("default")
    assert payload["status"] == "degraded"
    workers.reconcile()
    assert client.get("/healthz").json()["status"] == "ok"
    docker.tags.remove(images.image_tag("default"))
    payload = client.get("/healthz").json()
    assert payload["status"] == "degraded"
    assert payload["image_status"] == "missing"
    assert payload["workers"]["ready"] == 1


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


def test_projects_require_an_api_that_enforces_volume_subpaths(client, agent_images, docker, monkeypatch):
    images.ensure_images()
    monkeypatch.setattr(docker, "api", SimpleNamespace(_version="1.44"), raising=False)
    response = client.post(
        "/agent/run",
        headers=HEADERS,
        json={"agent_set": "default", "chat_id": "c" * 12, "project_ids": ["a" * 32]},
    )
    assert any(
        event.get("message") == "Project isolation requires Docker Engine 26+ with API 1.45+"
        for event in frames(response)
    )


@pytest.mark.parametrize("exit_code", [0, 1])
def test_agent_creation_uses_valid_sdk_arguments_and_streams_logs(
    client, agent_images, docker, monkeypatch, exit_code
):
    images.ensure_images()
    container = Mock()
    container.attrs = {"State": {"OOMKilled": False}}
    container.logs.side_effect = [iter([b'{"type":"text","text":"hello"}\n'])]
    if exit_code:
        container.logs.side_effect = [
            iter([b'{"type":"text","text":"hello"}\n']),
            b"agent failed",
        ]
    container.wait.return_value = {"StatusCode": exit_code}
    api = SimpleNamespace(_version="1.45", create_container=Mock(return_value={"Id": "test-agent"}))
    containers = ContainerCollection(client=SimpleNamespace(api=api))
    monkeypatch.setattr(containers, "get", Mock(return_value=container))
    monkeypatch.setattr(docker, "containers", containers)

    response = client.post("/agent/run", headers=HEADERS, json={"agent_set": "default"})
    events = frames(response)

    assert events[0]["type"] == "started"
    assert events[1] == {"type": "text", "text": "hello"}
    if exit_code:
        assert events[2] == {
            "type": "error",
            "message": "agent container exited 1: agent failed",
        }
        container.logs.assert_any_call(stdout=False, stderr=True, tail=100)
    else:
        assert len(events) == 3
    assert events[-1] == {"type": "closed"}
    api.create_container.assert_called_once()
    container.start.assert_called_once_with()
    container.logs.assert_any_call(stream=True, follow=True, stdout=True, stderr=False)
    container.remove.assert_called_once_with(force=True)


@pytest.fixture
def running_agent(agent_images, docker, monkeypatch):
    images.ensure_images()
    container = Mock()
    container.attrs = {"State": {"OOMKilled": False}}
    container.logs.return_value = iter([])
    container.wait.return_value = {"StatusCode": 0}
    monkeypatch.setattr(docker.containers, "create", Mock(return_value=container), raising=False)
    timers = []

    class Timer:
        def __init__(self, seconds, callback):
            self.seconds = seconds
            self.callback = callback
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def join(self):
            pass

    monkeypatch.setattr(agent_run.threading, "Timer", Timer)
    return container, timers


def test_large_jobs_are_copied_before_start_not_put_in_environment(client, running_agent, docker):
    container, _ = running_agent
    job = {"prompt": "look", "images": [{"type": "image", "mimeType": "image/png", "data": "x" * 200000}]}
    events = frames(client.post("/agent/run", headers=HEADERS, json=job))
    assert not any(event["type"] == "error" for event in events)
    environment = docker.containers.create.call_args.kwargs["environment"]
    assert "AGENT_JOB" not in environment and environment["AGENT_JOB_FILE"].endswith("nautionette-job.json")
    directory, archive = container.put_archive.call_args.args
    assert directory == "/tmp"  # noqa: S108
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        entry = tar.getmembers()[0]
        assert entry.name == "nautionette-job.json" and entry.mode == 0o600
        assert json.loads(tar.extractfile(entry).read()) == job
    calls = [call[0] for call in container.mock_calls]
    assert calls.index("put_archive") < calls.index("start")


def test_failed_job_copy_does_not_start_the_agent(client, running_agent):
    container, _ = running_agent
    container.put_archive.return_value = False
    events = frames(client.post("/agent/run", headers=HEADERS, json={"prompt": "x" * 200000}))
    assert any(event.get("message") == "Could not deliver the agent job" for event in events)
    container.start.assert_not_called()
    container.remove.assert_called_once_with(force=True)


@pytest.mark.parametrize("has_output", [False, True])
def test_watchdog_reports_timeout_even_when_log_stream_closes_without_another_chunk(
    client, running_agent, has_output
):
    container, timers = running_agent

    def logs(**kwargs):
        assert kwargs == {"stream": True, "follow": True, "stdout": True, "stderr": False}
        if has_output:
            yield b'{"type":"delta","text":"still working"}\n'
        timers[0].callback()  # SIGKILL closes the stream; no final chunk to check a deadline on.

    container.logs.side_effect = logs
    container.wait.return_value = {"StatusCode": 137}
    events = frames(client.post("/agent/run", headers=HEADERS, json={"timeout_seconds": 12}))
    errors = [event for event in events if event["type"] == "error"]
    assert errors == [agent_run._timeout_error(12)]
    assert container.kill.call_count == 1
    assert timers[0].cancelled
    assert events[-1] == {"type": "closed"}
    container.remove.assert_called_once_with(force=True)


@pytest.mark.parametrize(("oom", "reason"), [(True, "oom_killed"), (False, "sigkill")])
def test_exit_137_is_not_assumed_to_be_oom(client, running_agent, oom, reason):
    container, timers = running_agent
    container.attrs["State"]["OOMKilled"] = oom
    container.wait.return_value = {"StatusCode": 137}
    events = frames(client.post("/agent/run", headers=HEADERS, json={}))
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["reason"] == reason
    assert errors[0]["exit_code"] == 137
    if oom:
        assert agent_run.AGENT_MEMORY in errors[0]["message"]
    container.reload.assert_called_once_with()
    container.kill.assert_not_called()
    assert timers[0].cancelled


@pytest.mark.parametrize(("requested", "expected"), [(None, 3600), (900, 900), (1800, 1800), (7200, 3600)])
def test_broker_honours_long_calls_and_preserves_its_ceiling(
    client, running_agent, monkeypatch, requested, expected
):
    _, timers = running_agent
    monkeypatch.setattr(agent_run, "RUN_TIMEOUT", 3600)
    job = {} if requested is None else {"timeout_seconds": requested}
    events = frames(client.post("/agent/run", headers=HEADERS, json=job))
    assert not any(event["type"] == "error" for event in events)
    assert timers[0].seconds == expected
    assert timers[0].cancelled


def test_stop_does_not_report_a_container_failure(running_agent):
    import threading

    container, timers = running_agent
    stopped = threading.Event()

    def logs(**_kwargs):
        stopped.set()
        return iter([])

    container.logs.side_effect = logs
    container.wait.return_value = {"StatusCode": 137}
    events = [json.loads(line) for line in agent_run._run({}, stopped)]
    assert [event["type"] for event in events] == ["started", "closed"]
    assert timers[0].cancelled
    container.remove.assert_called_once_with(force=True)


def test_timeout_stays_explicit_if_kill_or_log_stream_races_with_container_exit(client, running_agent):
    container, timers = running_agent
    container.kill.side_effect = RuntimeError("container already exited")

    def logs(**_kwargs):
        timers[0].callback()
        raise RuntimeError("stream closed")

    container.logs.side_effect = logs
    events = frames(client.post("/agent/run", headers=HEADERS, json={"timeout_seconds": 12}))
    assert events[-2] == agent_run._timeout_error(12)
    assert events[-1] == {"type": "closed"}
    assert timers[0].cancelled
    container.remove.assert_called_once_with(force=True)


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


def test_a_build_that_will_never_finish_says_so_instead_of_hanging(client, agent_images, docker, monkeypatch):
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
    assert docker.last_filters == {"label": [config.WORKER_LABEL, "com.docker.compose.project=nautionette"]}


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
    assert workers.worker_filters() == {"label": [config.WORKER_LABEL, "com.docker.compose.project=explicit"]}


def test_missing_workers_are_recreated_once_with_the_shared_workflows(docker, monkeypatch):
    monkeypatch.setattr(workers, "WORKER_REPLICAS", 2)
    assert workers.snapshot()["ready"] == 0
    workers.reconcile()
    workers.reconcile()
    assert len(docker.runs) == 2
    assert workers.snapshot()["ready"] == 2
    assert docker.runs[0]["volumes"][config.WORKFLOWS_VOLUME]["bind"] == "/workflows"
    assert docker.runs[0]["labels"]["com.docker.compose.project"] == "nautionette"
    docker.listed.pop()
    assert workers.snapshot()["status"] == "degraded"
    workers.reconcile()
    assert len(docker.runs) == 3
    assert workers.snapshot()["status"] == "ready"


def test_stopped_and_unhealthy_workers_are_repaired_in_place(docker, monkeypatch):
    monkeypatch.setattr(workers, "WORKER_REPLICAS", 2)
    stopped = FakeContainer("nautionette-worker-1", status="exited")
    unhealthy = FakeContainer("nautionette-worker-2", health="unhealthy")
    docker.listed = [stopped, unhealthy]
    workers.reconcile()
    workers.reconcile()
    assert stopped.starts == 1
    assert unhealthy.restarts == [config.STOP_GRACE]
    assert docker.runs == []
    assert workers.snapshot()["status"] == "degraded"


def test_worker_reconciliation_fails_closed_and_recovers_after_docker_returns(docker):
    docker.up = False
    workers.reconcile()
    assert workers.snapshot()["status"] == "degraded"
    assert docker.runs == []
    docker.up = True
    workers.reconcile()
    assert workers.snapshot()["status"] == "ready"


def test_a_worker_that_cannot_start_does_not_create_unbounded_replacements(docker, monkeypatch):
    stopped = FakeContainer("nautionette-worker-1", status="exited")
    docker.listed = [stopped]

    def fail():
        raise RuntimeError("out of memory")

    monkeypatch.setattr(stopped, "start", fail)
    workers.reconcile()
    workers.reconcile()
    assert docker.runs == []
    assert "out of memory" in workers.snapshot()["error"]


def test_worker_creation_failure_is_reported_and_retried(docker, monkeypatch):
    run = docker.containers.run

    def fail(*_args, **_kwargs):
        raise RuntimeError("worker image missing")

    monkeypatch.setattr(docker.containers, "run", fail)
    workers.reconcile()
    assert "worker image missing" in workers.snapshot()["error"]
    monkeypatch.setattr(docker.containers, "run", run)
    workers.reconcile()
    assert workers.snapshot()["status"] == "ready"


def test_a_worker_without_a_readiness_probe_is_not_reported_as_ready(docker):
    container = FakeContainer("nautionette-worker-1")
    del container.attrs["State"]["Health"]
    docker.listed = [container]
    assert workers.snapshot()["ready"] == 0


def test_a_dead_worker_is_removed_and_replaced(docker, monkeypatch):
    container = FakeContainer("nautionette-worker-1", status="dead")
    docker.listed = [container]
    monkeypatch.setattr(container, "remove", lambda: docker.listed.remove(container), raising=False)
    workers.reconcile()
    assert len(docker.runs) == 1
    assert workers.snapshot()["status"] == "ready"


def test_monitor_keeps_reconciling_workers_when_image_inspection_fails(monkeypatch):
    import threading

    stopping = threading.Event()
    calls = []

    def broken_images():
        raise RuntimeError("cannot inspect images")

    def reconcile_workers():
        calls.append("workers")
        stopping.set()

    monkeypatch.setattr(images, "reconcile", broken_images)
    monkeypatch.setattr(workers, "reconcile", reconcile_workers)
    monitor.run(stopping)
    assert calls == ["workers"]


def test_lifespan_starts_and_stops_the_monitor(monkeypatch, docker):
    import threading

    started = threading.Event()
    stopped = threading.Event()

    def run(stopping):
        started.set()
        stopping.wait()
        stopped.set()

    monkeypatch.setattr(monitor, "run", run)
    with TestClient(main.app):
        assert started.wait(timeout=2)
    assert stopped.is_set()


def test_monitor_restores_resources_removed_between_passes(agent_images, docker, monkeypatch):
    passes = []

    def wait(interval):
        assert interval == monitor.RECONCILE_SECONDS
        assert images.snapshot()["status"] == "ready"
        assert workers.snapshot()["status"] == "ready"
        passes.append(True)
        if len(passes) == 1:
            docker.tags.clear()
            docker.listed.clear()
            return False
        return True

    monkeypatch.setattr(images, "start_build", lambda: images.ensure_images())
    monitor.run(SimpleNamespace(is_set=lambda: False, wait=wait))
    assert len(passes) == 2
    assert len(docker.built) == 4
    assert len(docker.runs) == 2
