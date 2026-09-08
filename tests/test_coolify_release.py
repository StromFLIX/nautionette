from __future__ import annotations

import json

import pytest

from scripts import coolify_release as release

REPO = "StromFLIX/nautionette"
COMMIT = "a" * 40


def event(**fields):
    return {"repository": {"full_name": REPO}, **fields}


@pytest.mark.parametrize("tag", ["v1.2.3-rc.1", "v01.2.3", "main", "v1.2", "v1.2.3;echo bad"])
def test_non_release_tags_are_silent(tag):
    assert (
        release.candidate(
            "release",
            event(
                action="published",
                release={
                    "draft": False,
                    "prerelease": False,
                    "tag_name": tag,
                },
            ),
            REPO,
        )
        is None
    )


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_drafts_and_prereleases_are_silent(field):
    data = {"draft": False, "prerelease": False, "tag_name": "v1.2.3", field: True}
    assert release.candidate("release", event(action="published", release=data), REPO) is None


def test_release_resolves_the_tag_not_target_commitish(monkeypatch):
    calls = []

    def resolve(args, **kwargs):
        calls.append(args)
        return COMMIT + "\n"

    monkeypatch.setattr(release.subprocess, "check_output", resolve)
    data = {"draft": False, "prerelease": False, "tag_name": "v1.2.3", "target_commitish": "main"}
    assert release.candidate("release", event(action="published", release=data), REPO) == (
        "production",
        COMMIT,
    )
    assert calls[0][-1] == "refs/tags/v1.2.3^{commit}"


@pytest.mark.parametrize(
    "override",
    [
        {"event": "pull_request"},
        {"conclusion": "failure"},
        {"head_branch": "other"},
        {"head_repository": {"full_name": "other/repo"}},
    ],
)
def test_only_successful_main_push_ci_can_deploy_staging(override):
    run = {
        "event": "push",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": COMMIT,
        "head_repository": {"full_name": REPO},
    }
    assert release.candidate("workflow_run", event(action="completed", workflow_run=run), REPO) == (
        "staging",
        COMMIT,
    )
    run.update(override)
    assert release.candidate("workflow_run", event(action="completed", workflow_run=run), REPO) is None


def test_successful_manual_main_ci_can_initialize_staging():
    run = {
        "event": "workflow_dispatch",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": COMMIT,
        "head_repository": {"full_name": REPO},
    }
    assert release.candidate("workflow_run", event(action="completed", workflow_run=run), REPO) == (
        "staging",
        COMMIT,
    )


def test_irrelevant_event_needs_no_secrets_or_network(tmp_path, monkeypatch, capsys):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event(action="ping")))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "ping")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    release.main()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "url", ["http://coolify.test", "https://secret@coolify.test", "https://coolify.test?token=secret"]
)
def test_rejects_insecure_or_credential_bearing_urls(url):
    with pytest.raises(ValueError):
        release.https_url(url)


@pytest.fixture
def api(monkeypatch):
    class Fake:
        def __init__(self):
            self.calls = []
            self.history_rows = []
            self.already_deployed = set()
            self.final_status = "finished"
            self.final_commit = COMMIT
            self.accept_pin = True
            self.description = ""

        def deployed(self, app, commit):
            return app in self.already_deployed

        def history(self, app):
            return self.history_rows

        def call(self, path, method="GET", payload=None):
            self.calls.append((path, method, payload))
            if path == "/deploy":
                return {"deployments": [{"resource_uuid": "prod", "deployment_uuid": "deploy123"}]}
            if path.startswith("/deployments/"):
                return {"status": self.final_status, "commit": self.final_commit}
            return {
                "description": self.description,
                "git_branch": "main",
                "build_pack": "dockercompose",
                "git_repository": REPO,
                "git_commit_sha": COMMIT if self.accept_pin else "HEAD",
            }

    fake = Fake()
    monkeypatch.setattr(release, "Coolify", lambda: fake)
    monkeypatch.setattr(release, "healthy", lambda target: None)
    monkeypatch.setenv("COOLIFY_APPLICATION_UUID", "prod")
    monkeypatch.setenv("COOLIFY_STAGING_APPLICATION_UUID", "stage")
    monkeypatch.setenv("APP_URL", "https://app.nautionette.stromflix.com")
    monkeypatch.setenv("APP_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.delenv("DEPLOY_HEALTH_TIMEOUT_SECONDS", raising=False)
    return fake


def test_untested_release_never_patches_or_deploys(api):
    with pytest.raises(RuntimeError, match="staging"):
        release.deploy("production", COMMIT)
    assert all(method == "GET" for _, method, _ in api.calls)


def test_duplicate_deploy_is_a_noop(api):
    api.already_deployed.add("prod")
    assert release.deploy("production", COMMIT) is None
    assert api.calls == []


def test_active_deployment_is_not_reconfigured(api):
    api.already_deployed.add("stage")
    api.history_rows = [{"status": "queued"}]
    with pytest.raises(RuntimeError, match="active"):
        release.deploy("production", COMMIT)
    assert all(method == "GET" for _, method, _ in api.calls)


@pytest.mark.parametrize("target", ["production", "staging"])
def test_refresh_guard_prevents_deployment_writes(api, target):
    api.already_deployed.add("stage")
    api.description = "[nautionette-refresh:1234-1] Original description"
    with pytest.raises(RuntimeError, match="maintenance guard"):
        release.deploy(target, COMMIT)
    assert all(method == "GET" for _, method, _ in api.calls)


def test_pins_before_queueing_and_waits_for_verified_result(api):
    api.already_deployed.add("stage")
    assert release.deploy("production", COMMIT) == "deploy123"
    writes = [item for item in api.calls if item[1] != "GET"]
    assert writes == [
        ("/applications/prod", "PATCH", {"git_commit_sha": COMMIT, "is_auto_deploy_enabled": False}),
        ("/deploy", "POST", {"uuid": "prod", "force": False}),
    ]


def test_rejected_commit_pin_never_queues(api):
    api.already_deployed.add("stage")
    api.accept_pin = False
    with pytest.raises(RuntimeError, match="commit pin"):
        release.deploy("production", COMMIT)
    assert not any(path == "/deploy" for path, _, _ in api.calls)


@pytest.mark.parametrize("status,commit", [("failed", COMMIT), ("finished", "b" * 40)])
def test_failed_or_wrong_commit_is_never_reported_as_success(api, status, commit):
    api.already_deployed.add("stage")
    api.final_status, api.final_commit = status, commit
    with pytest.raises(RuntimeError):
        release.deploy("production", COMMIT)


@pytest.fixture
def health_state(monkeypatch):
    state = {
        "environment": "production",
        "auth_enabled": True,
        "components": [
            {"name": name, "status": "ok"} for name in ("temporal", "broker", "agentgateway", "workflow-mcp")
        ],
    }
    monkeypatch.setenv("APP_URL", "https://app.example.test")
    monkeypatch.setenv("APP_TOKEN", "test-token")

    def request(url, token):
        assert url == "https://app.example.test/api/system"
        assert token == "test-token"
        return state

    monkeypatch.setattr(release, "request", request)
    return state


def test_health_requires_authenticated_environment_and_all_components(health_state):
    release.healthy("production")


@pytest.mark.parametrize(
    "field,value,diagnostic",
    [
        ("environment", "staging", "environment does not match"),
        ("auth_enabled", False, "auth_enabled is not true"),
        ("auth_enabled", "true", "auth_enabled is not true"),
        ("components", None, "components is not a list"),
        ("components", [None], "components is not a list"),
        ("components", [], "broker: missing"),
    ],
)
def test_health_reports_failed_fields(health_state, field, value, diagnostic):
    health_state[field] = value
    with pytest.raises(RuntimeError, match=diagnostic):
        release.healthy("production")


@pytest.mark.parametrize("name", ["temporal", "broker", "agentgateway", "workflow-mcp"])
@pytest.mark.parametrize("status", ["down", "degraded"])
def test_health_reports_each_failed_component(health_state, name, status):
    next(item for item in health_state["components"] if item["name"] == name)["status"] = status
    with pytest.raises(RuntimeError, match=f"{name}: {status}"):
        release.healthy("production")


def test_health_reports_all_problems_without_echoing_sensitive_response(health_state):
    secret = "credential-do-not-log"
    health_state.update(environment=secret, auth_enabled=secret)
    health_state["components"] = [
        {"name": "broker", "status": secret, "detail": secret},
        {"name": "temporal", "status": "ok"},
        {"name": "temporal", "status": "ok"},
        {"name": {"malformed": secret}, "status": "ok"},
    ]
    with pytest.raises(RuntimeError) as error:
        release.healthy("production")
    message = str(error.value)
    assert secret not in message
    for expected in (
        "environment does not match",
        "auth_enabled is not true",
        "unexpected component name",
        "broker: missing or unrecognized status",
        "temporal: duplicate entries",
        "agentgateway: missing",
        "workflow-mcp: missing",
    ):
        assert expected in message


@pytest.mark.parametrize("response", [None, [], "credential-do-not-log"])
def test_health_rejects_non_object_json(health_state, monkeypatch, response):
    monkeypatch.setattr(release, "request", lambda *args: response)
    with pytest.raises(RuntimeError, match="non-object response"):
        release.healthy("production")


@pytest.mark.parametrize(
    "error,diagnostic",
    [
        (json.JSONDecodeError("secret", "secret", 0), "invalid JSON"),
        (TimeoutError("secret"), "request timed out"),
        (RuntimeError("Deployment API returned HTTP 401"), "HTTP 401"),
        (RuntimeError("Deployment API returned HTTP 503"), "HTTP 503"),
    ],
)
def test_health_endpoint_errors(health_state, monkeypatch, error, diagnostic):
    def fail(*args):
        raise error

    monkeypatch.setattr(release, "request", fail)
    with pytest.raises(RuntimeError, match=diagnostic) as caught:
        release.healthy("production")
    assert "secret" not in str(caught.value)


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 0

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(release.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(release.time, "sleep", clock.sleep)
    return clock


@pytest.mark.parametrize("duplicate", [False, True])
def test_slow_startup_gets_grace_without_redeploying(api, clock, monkeypatch, capsys, duplicate):
    api.already_deployed.add("prod" if duplicate else "stage")

    def healthy(target):
        assert target == "production"
        if clock.now < 400:
            raise RuntimeError("Health checks failed: broker: degraded")

    monkeypatch.setattr(release, "healthy", healthy)
    assert release.deploy("production", COMMIT) == (None if duplicate else "deploy123")
    assert clock.now == 400  # Beyond the old five-minute retry loop.
    assert sum(method == "POST" for _, method, _ in api.calls) == (0 if duplicate else 1)
    output = capsys.readouterr().out
    assert "broker: degraded" in output
    assert "attempt 2, 10s elapsed" in output


def test_persistent_health_failure_stays_failed_with_last_diagnostic(api, clock, monkeypatch, capsys):
    api.already_deployed.add("stage")
    monkeypatch.setenv("DEPLOY_HEALTH_TIMEOUT_SECONDS", "25")

    def unhealthy(target):
        component = "broker: degraded" if clock.now < 20 else "workflow-mcp: down"
        raise RuntimeError("Health checks failed: " + component)

    monkeypatch.setattr(release, "healthy", unhealthy)
    with pytest.raises(
        RuntimeError, match="Post-deployment health verification timed out after 25s"
    ) as error:
        release.deploy("production", COMMIT)
    assert "Last check: Health checks failed: workflow-mcp: down" in str(error.value)
    assert clock.now == 25
    assert sum(method == "POST" for _, method, _ in api.calls) == 1
    assert "Coolify deployment deploy123 finished; verifying production health" in capsys.readouterr().out


def test_health_budget_counts_request_time(clock, monkeypatch):
    def unhealthy(target):
        clock.now += 30
        raise RuntimeError("Health endpoint request timed out")

    monkeypatch.setattr(release, "healthy", unhealthy)
    with pytest.raises(RuntimeError, match="timed out after 70s"):
        release.wait_for_health("production", 60)
    assert clock.now == 70


@pytest.mark.parametrize("value", ["0", "-1", "1801", "ten", "1.5", "inf", "²"])
def test_invalid_health_timeout_is_rejected_before_api_writes(api, monkeypatch, value):
    monkeypatch.setenv("DEPLOY_HEALTH_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="DEPLOY_HEALTH_TIMEOUT_SECONDS"):
        release.deploy("production", COMMIT)
    assert api.calls == []


@pytest.mark.parametrize("value,expected", [("", 600), ("  ", 600), ("1", 1), ("1800", 1800)])
def test_health_timeout_configuration(monkeypatch, value, expected):
    monkeypatch.setenv("DEPLOY_HEALTH_TIMEOUT_SECONDS", value)
    assert release.health_timeout() == expected
