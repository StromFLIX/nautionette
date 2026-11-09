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


def manual_event(**fields):
    return event(ref="refs/heads/main", inputs={"release_tag": "v1.2.3"}, **fields)


@pytest.fixture
def manual_api(monkeypatch):
    state = {
        "release": {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-01-01T00:00:00Z",
            "target_commitish": "main",
        },
        "commit": {"sha": COMMIT},
        "calls": [],
    }

    def get(url, token, method="GET", payload=None):
        assert token == "test-github-token"
        assert method == "GET" and payload is None
        state["calls"].append(url)
        if url == f"https://api.github.com/repos/{REPO}/releases/tags/v1.2.3":
            return state["release"]
        assert url == f"https://api.github.com/repos/{REPO}/commits/v1.2.3"
        return state["commit"]

    def resolve(args, **kwargs):
        assert args == ["/usr/bin/git", "rev-parse", "--verify", "refs/tags/v1.2.3^{commit}"]
        return COMMIT + "\n"

    monkeypatch.setenv("GITHUB_TOKEN", "test-github-token")
    monkeypatch.setattr(release, "request", get)
    monkeypatch.setattr(release.subprocess, "check_output", resolve)
    return state


def test_manual_release_resolves_published_tag_not_main(manual_api):
    assert release.candidate("workflow_dispatch", manual_event(), REPO) == ("production", COMMIT)
    assert len(manual_api["calls"]) == 2


@pytest.mark.parametrize("ref", ["main", "refs/heads/main"])
def test_manual_event_accepts_main_ref_formats(manual_api, ref):
    data = manual_event()
    data["ref"] = ref
    assert release.candidate("workflow_dispatch", data, REPO) == ("production", COMMIT)


@pytest.mark.parametrize("ref", ["feature", "refs/heads/feature", "refs/tags/v1.2.3", None])
def test_manual_release_rejects_non_main_refs_before_network(manual_api, ref):
    data = manual_event()
    data["ref"] = ref
    with pytest.raises(ValueError, match="from main"):
        release.candidate("workflow_dispatch", data, REPO)
    assert manual_api["calls"] == []


@pytest.mark.parametrize("tag", ["v1.2.3-rc.1", "v01.2.3", "main", "v1.2", "v1.2.3;echo bad", "", None])
def test_invalid_manual_tags_fail_before_network(manual_api, tag):
    data = manual_event()
    data["inputs"] = {"release_tag": tag}
    with pytest.raises(ValueError, match="stable vX.Y.Z"):
        release.candidate("workflow_dispatch", data, REPO)
    assert manual_api["calls"] == []


@pytest.mark.parametrize(
    "override",
    [{"draft": True}, {"prerelease": True}, {"published_at": None}, {"tag_name": "v1.2.4"}],
)
def test_manual_release_requires_published_stable_metadata(manual_api, override):
    manual_api["release"].update(override)
    with pytest.raises(ValueError, match="published stable release"):
        release.candidate("workflow_dispatch", manual_event(), REPO)
    assert len(manual_api["calls"]) == 1


def test_manual_release_fails_closed_when_release_lookup_fails(manual_api, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Deployment API returned HTTP 404")

    monkeypatch.setattr(release, "request", unavailable)
    with pytest.raises(RuntimeError, match="HTTP 404"):
        release.candidate("workflow_dispatch", manual_event(), REPO)


@pytest.mark.parametrize("remote", [{"sha": "b" * 40}, {}, []])
def test_manual_release_rejects_changed_or_unverified_tag(manual_api, remote):
    manual_api["commit"] = remote
    with pytest.raises(ValueError, match="tag moved"):
        release.candidate("workflow_dispatch", manual_event(), REPO)


def manual_main(tmp_path, monkeypatch):
    path = tmp_path / "manual-event.json"
    path.write_text(json.dumps(manual_event()))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)  # Dispatch points at main, not the release tag.
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)


def test_manual_main_uses_tag_sha_and_existing_staging_gate(tmp_path, monkeypatch, manual_api, api):
    manual_main(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="exact commit currently deployed successfully in staging"):
        release.main()
    assert all(method == "GET" for _, method, _ in api.calls)
    api.already_deployed.add("stage")
    release.main()
    pins = [
        payload for path, method, payload in api.calls if path == "/applications/prod" and method == "PATCH"
    ]
    assert pins == [{"git_commit_sha": COMMIT, "is_auto_deploy_enabled": False}]


def test_manual_main_checks_runtime_ref_before_network(tmp_path, monkeypatch, manual_api, api):
    manual_main(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v1.2.3")
    with pytest.raises(ValueError, match="from main"):
        release.main()
    assert manual_api["calls"] == []
    assert api.calls == []


def test_automatic_release_still_checks_event_sha(tmp_path, monkeypatch, api):
    path = tmp_path / "release-event.json"
    path.write_text(json.dumps(event()))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "release")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setattr(release, "candidate", lambda *args: ("production", COMMIT))
    with pytest.raises(ValueError, match="tag moved since the event"):
        release.main()
    assert api.calls == []


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
            self.accept_version = True
            self.release_tag = ""
            self.accept_release_tag = True
            self.description = ""
            self.application_status = "running:healthy"
            self.output_envs = [
                {"key": "MAXIO_ACCESS_KEY", "value": "existing-test-access-key"},
                {"key": "MAXIO_SECRET_KEY", "value": "existing-test-secret-key"},
            ]
            self.accept_output_credentials = True

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
            if path.endswith("/envs/bulk"):
                if self.accept_release_tag:
                    for row in payload["data"]:
                        if row["key"] == "APP_RELEASE_TAG":
                            self.release_tag = row["value"]
                if self.accept_output_credentials:
                    for row in payload["data"]:
                        if row["key"] in {"MAXIO_ACCESS_KEY", "MAXIO_SECRET_KEY"}:
                            self.output_envs = [
                                item
                                for item in self.output_envs
                                if item["key"] != row["key"] or item.get("is_preview", False)
                            ] + [row]
                return {}
            if path.endswith("/envs"):
                return [
                    {"key": "APP_VERSION", "value": COMMIT if self.accept_version else "stale"},
                    {"key": "APP_RELEASE_TAG", "value": self.release_tag},
                    *self.output_envs,
                ]
            return {
                "description": self.description,
                "git_branch": "main",
                "build_pack": "dockercompose",
                "git_repository": REPO,
                "git_commit_sha": COMMIT if self.accept_pin else "HEAD",
                "status": self.application_status,
            }

    fake = Fake()
    monkeypatch.setattr(release, "Coolify", lambda: fake)
    monkeypatch.setattr(release, "healthy", lambda target: None)
    monkeypatch.setattr(release, "staging_verified", lambda repository, commit: True)
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


def test_failed_staging_smoke_blocks_promotion_before_any_writes(api, monkeypatch):
    api.already_deployed.add("stage")
    monkeypatch.setattr(release, "staging_verified", lambda repository, commit: False)
    with pytest.raises(RuntimeError, match="requires successful staging"):
        release.deploy("production", COMMIT)
    assert all(method == "GET" for _, method, _ in api.calls)


def test_duplicate_deploy_is_a_noop(api):
    api.already_deployed.add("prod")
    assert release.deploy("production", COMMIT) is None
    assert api.calls == [
        ("/applications/prod/envs", "GET", None),
        ("/applications/prod", "GET", None),
    ]


def test_duplicate_deploy_requires_coolify_healthy_status(api, monkeypatch):
    api.already_deployed.add("prod")
    api.application_status = "running:unknown"
    monkeypatch.setenv("DEPLOY_HEALTH_TIMEOUT_SECONDS", "1")
    times = iter([0, 1])
    monkeypatch.setattr(release.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="running:healthy"):
        release.deploy("production", COMMIT)


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
        (
            "/applications/prod/envs/bulk",
            "PATCH",
            {
                "data": [
                    {
                        "key": "APP_VERSION",
                        "value": COMMIT,
                        "is_buildtime": True,
                        "is_runtime": True,
                        "is_literal": True,
                    },
                    {
                        "key": "APP_RELEASE_TAG",
                        "value": "",
                        "is_buildtime": True,
                        "is_runtime": True,
                        "is_literal": True,
                    },
                ]
            },
        ),
        ("/deploy", "POST", {"uuid": "prod", "force": False}),
    ]


def test_deploy_provisions_missing_output_credentials_before_queueing(api, capsys):
    api.already_deployed.add("stage")
    api.output_envs = []
    assert release.deploy("production", COMMIT) == "deploy123"
    provision = next(
        item
        for item in api.calls
        if item[1] == "PATCH"
        and item[0].endswith("/envs/bulk")
        and any(row["key"] == "MAXIO_SECRET_KEY" for row in item[2]["data"])
    )
    assert api.calls.index(provision) < next(
        index for index, item in enumerate(api.calls) if item[0] == "/deploy"
    )
    rows = provision[2]["data"]
    assert {row["key"] for row in rows} == {"MAXIO_ACCESS_KEY", "MAXIO_SECRET_KEY"}
    output = capsys.readouterr().out
    for row in rows:
        assert row["is_literal"] is row["is_runtime"] is row["is_buildtime"] is True
        assert row["value"] not in output
        assert len(row["value"]) == (64 if row["key"] == "MAXIO_SECRET_KEY" else 32)
        int(row["value"], 16)


def test_existing_output_credentials_are_never_rotated(api, capsys):
    before = [dict(row) for row in api.output_envs]
    release.ensure_output_storage_credentials(api, "prod")
    assert api.output_envs == before
    assert all(method == "GET" for _, method, _ in api.calls)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("missing_value", [None, "", "  "])
def test_only_blank_output_credential_is_generated(api, missing_value):
    api.output_envs[1]["value"] = missing_value
    release.ensure_output_storage_credentials(api, "prod")
    writes = [item for item in api.calls if item[1] != "GET"]
    assert len(writes) == 1
    assert [row["key"] for row in writes[0][2]["data"]] == ["MAXIO_SECRET_KEY"]
    assert api.output_envs[0]["value"] == "existing-test-access-key"
    # Retrying after an ambiguous deployment response reuses the persisted secret.
    release.ensure_output_storage_credentials(api, "prod")
    assert sum(method != "GET" for _, method, _ in api.calls) == 1


def test_preview_credentials_are_not_reused_for_the_main_deployment(api):
    api.output_envs = [{**row, "is_preview": True} for row in api.output_envs]
    release.ensure_output_storage_credentials(api, "prod")
    live = [row for row in api.output_envs if not row.get("is_preview", False)]
    assert len(live) == 2
    assert all(row["value"] not in {"existing-test-access-key", "existing-test-secret-key"} for row in live)


def test_failed_output_credential_persistence_never_queues_or_logs_secrets(api, capsys):
    api.already_deployed.add("stage")
    api.output_envs = []
    api.accept_output_credentials = False
    with pytest.raises(RuntimeError, match="output storage credentials") as caught:
        release.deploy("production", COMMIT)
    assert not any(path == "/deploy" for path, _, _ in api.calls)
    output = str(caught.value) + capsys.readouterr().out
    for _, method, payload in api.calls:
        if method == "PATCH" and "data" in payload:
            for row in payload["data"]:
                if row["key"] in {"MAXIO_ACCESS_KEY", "MAXIO_SECRET_KEY"}:
                    assert row["value"] not in output


def test_rejected_commit_pin_never_queues(api):
    api.already_deployed.add("stage")
    api.accept_pin = False
    with pytest.raises(RuntimeError, match="commit pin"):
        release.deploy("production", COMMIT)
    assert not any(path == "/deploy" for path, _, _ in api.calls)


def test_rejected_source_version_never_queues(api):
    api.already_deployed.add("stage")
    api.accept_version = False
    with pytest.raises(RuntimeError, match="source version"):
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
        assert token == "test-token"
        if url == "https://app.example.test/api/settings/storage":
            return {"health": {"status": "ok"}}
        assert url == "https://app.example.test/api/system"
        return state

    monkeypatch.setattr(release, "request", request)
    return state


def test_health_requires_authenticated_environment_and_all_components(health_state):
    release.healthy("production")


@pytest.mark.parametrize(
    "storage",
    [
        None,
        [],
        {},
        {"health": None},
        {"health": {"status": "unavailable", "error": "credential-do-not-log"}},
        {"health": {"status": "credential-do-not-log"}},
    ],
)
def test_unhealthy_output_storage_fails_the_release_without_echoing_details(
    health_state, monkeypatch, storage
):
    def request(url, token):
        assert token == "test-token"
        return health_state if url.endswith("/api/system") else storage

    monkeypatch.setattr(release, "request", request)
    with pytest.raises(RuntimeError, match="Output storage health check failed") as caught:
        release.healthy("production")
    assert "credential-do-not-log" not in str(caught.value)


@pytest.mark.parametrize(
    "error,diagnostic",
    [
        (json.JSONDecodeError("credential-do-not-log", "secret", 0), "invalid JSON"),
        (TimeoutError("credential-do-not-log"), "request timed out"),
        (RuntimeError("Deployment API returned HTTP 503"), "HTTP 503"),
    ],
)
def test_output_storage_endpoint_failures_fail_verification(health_state, monkeypatch, error, diagnostic):
    def request(url, token):
        if url.endswith("/api/system"):
            return health_state
        raise error

    monkeypatch.setattr(release, "request", request)
    with pytest.raises(RuntimeError, match=diagnostic) as caught:
        release.healthy("production")
    assert "credential-do-not-log" not in str(caught.value)


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


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "failed_run",
        "pending_run",
        "wrong_commit",
        "production_event",
        "fork",
        "failed_smoke",
        "missing_artifact_check",
        "newer_failure",
    ],
)
def test_only_complete_successful_staging_verification_can_authorize_production(monkeypatch, failure):
    monkeypatch.setenv("GITHUB_TOKEN", "test-github-token")
    run = {
        "id": 123,
        "head_sha": COMMIT,
        "event": "workflow_run",
        "head_branch": "main",
        "head_repository": {"full_name": REPO},
        "status": "completed",
        "conclusion": "success",
    }
    steps = [
        {"name": name, "conclusion": "success"}
        for name in (
            "Pin, deploy, and verify",
            "Verify isolated mini-app lifecycle (staging only)",
            "Creative-app artifact readiness diagnostics (staging only)",
        )
    ]
    if failure == "failed_run":
        run["conclusion"] = "failure"
    elif failure == "pending_run":
        run["status"] = "in_progress"
    elif failure == "wrong_commit":
        run["head_sha"] = "b" * 40
    elif failure == "production_event":
        run["event"] = "release"
    elif failure == "fork":
        run["head_repository"] = {"full_name": "other/repo"}
    elif failure == "failed_smoke":
        steps[1]["conclusion"] = "failure"
    elif failure == "missing_artifact_check":
        steps.pop()
    runs = [run]
    if failure == "newer_failure":
        runs.insert(0, {**run, "id": 124, "conclusion": "failure"})

    def get(url, token, method="GET", payload=None):
        assert token == "test-github-token" and method == "GET" and payload is None
        assert url.startswith(f"https://api.github.com/repos/{REPO}/actions/")
        if "/workflows/" in url:
            assert f"head_sha={COMMIT}" in url and "event=workflow_run" in url
            return {"workflow_runs": runs}
        assert url.endswith("/runs/123/jobs?per_page=100")
        return {"jobs": [{"name": "deploy", "conclusion": "success", "steps": steps}]}

    monkeypatch.setattr(release, "request", get)
    assert release.staging_verified(REPO, COMMIT) is (failure is None)


def test_manual_release_wires_explicit_tag_but_keeps_raw_revision(tmp_path, monkeypatch, manual_api, api):
    manual_main(tmp_path, monkeypatch)
    api.already_deployed.add("stage")
    release.main()
    metadata = next(
        payload["data"]
        for path, method, payload in api.calls
        if path.endswith("/envs/bulk") and method == "PATCH"
    )
    assert {row["key"]: row["value"] for row in metadata} == {
        "APP_VERSION": COMMIT,
        "APP_RELEASE_TAG": "v1.2.3",
    }


def test_automatic_release_wires_explicit_tag(tmp_path, monkeypatch, api):
    path = tmp_path / "release-event.json"
    path.write_text(
        json.dumps(
            event(
                action="published",
                release={
                    "tag_name": "v1.2.3",
                    "draft": False,
                    "prerelease": False,
                },
            )
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "release")
    monkeypatch.setenv("GITHUB_SHA", COMMIT)
    monkeypatch.setattr(release.subprocess, "check_output", lambda *a, **kw: COMMIT)
    api.already_deployed.add("stage")
    release.main()
    assert api.release_tag == "v1.2.3"


def test_staging_event_clears_release_even_if_its_commit_has_a_tag(tmp_path, monkeypatch, api):
    path = tmp_path / "staging-event.json"
    path.write_text(
        json.dumps(
            event(
                action="completed",
                workflow_run={
                    "head_sha": COMMIT,
                    "conclusion": "success",
                    "event": "push",
                    "head_branch": "main",
                    "head_repository": {"full_name": REPO},
                },
            )
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    monkeypatch.setattr(release.subprocess, "check_output", lambda *a, **kw: COMMIT)
    api.release_tag = "v1.2.3"
    release.main()
    assert api.release_tag == ""


def test_same_commit_with_changed_release_metadata_redeploys(api):
    api.already_deployed.update({"prod", "stage"})
    assert release.deploy("production", COMMIT, "v1.2.3") == "deploy123"
    assert api.release_tag == "v1.2.3"
    # The next delivery is a no-op, still verifying the full source revision.
    api.calls.clear()
    assert release.deploy("production", COMMIT, "v1.2.3") is None
    assert all(method == "GET" for _, method, _ in api.calls)


def test_same_commit_staging_deployment_clears_stale_release_metadata(api):
    api.already_deployed.add("prod")
    api.release_tag = "v1.2.3"
    assert release.deploy("staging", COMMIT) == "deploy123"
    assert api.release_tag == ""


def test_duplicate_with_wrong_raw_revision_cannot_bypass_version_verification(api):
    api.already_deployed.update({"prod", "stage"})
    api.accept_version = False
    with pytest.raises(RuntimeError, match="source version"):
        release.deploy("production", COMMIT)
    assert not any(path == "/deploy" for path, _, _ in api.calls)


def test_rejected_release_metadata_never_queues(api):
    api.already_deployed.add("stage")
    api.accept_release_tag = False
    with pytest.raises(RuntimeError, match="release metadata"):
        release.deploy("production", COMMIT, "v1.2.3")
    assert not any(path == "/deploy" for path, _, _ in api.calls)


@pytest.mark.parametrize("target,tag", [("staging", "v1.2.3"), ("production", "v1.2.3-rc.1")])
def test_invalid_deployment_release_intent_is_rejected_before_io(api, target, tag):
    with pytest.raises(ValueError, match="production stable"):
        release.deploy(target, COMMIT, tag)
    assert api.calls == []
