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
    return fake


def test_untested_release_never_patches_or_deploys(api):
    with pytest.raises(RuntimeError, match="staging"):
        release.deploy("production", COMMIT)
    assert api.calls == []


def test_duplicate_deploy_is_a_noop(api):
    api.already_deployed.add("prod")
    assert release.deploy("production", COMMIT) is None
    assert api.calls == []


def test_active_deployment_is_not_reconfigured(api):
    api.already_deployed.add("stage")
    api.history_rows = [{"status": "queued"}]
    with pytest.raises(RuntimeError, match="active"):
        release.deploy("production", COMMIT)
    assert api.calls == []


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
