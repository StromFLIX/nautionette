"""The evidence workflow is read-only and fails closed before publishing a mixed snapshot."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nautionette_backend.dashboard_schema import Manifest, Recipe

from workflows import nautionette_git_release_status as evidence

SHA = "a" * 40
OTHER = "b" * 40


def test_saved_native_contracts_reuse_immutable_recipe_and_have_no_release_control():
    root = Path(__file__).resolve().parents[1] / "dashboards"
    manifest = Manifest.model_validate_json((root / "nautionette-git-release.json").read_text())
    recipe = Recipe.model_validate_json((root / "nautionette-source-revision.json").read_text())
    assert manifest.agent_id == recipe.agent_id == "d75b7c63a380"
    assert set(manifest.actions) == {"refresh-evidence", "prepare-notes"}
    assert manifest.actions["refresh-evidence"].workflow == evidence.MANIFEST["name"]
    assert manifest.actions["refresh-evidence"].bindings["expected_sha"].source == "main"
    assert manifest.actions["prepare-notes"].confirm
    assert sum(w.component == "recipe:nautionette-source-revision@1" for w in manifest.widgets) == 4
    assert all(s.kind != "literal" and s.data is None for s in manifest.sources.values())
    assert "diagnostic" not in manifest.sources
    schema = manifest.sources["verification"].output_schema
    assert schema["additionalProperties"] is False
    assert "main_sha" in schema["required"]
    assert "token" not in json.dumps(manifest.model_dump()).lower()


def ci_run(**overrides):
    return {
        "id": 10,
        "name": "CI",
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "repository": {"full_name": evidence.REPO},
        "head_repository": {"full_name": evidence.REPO},
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/StromFLIX/nautionette/actions/runs/10",
        **overrides,
    }


def app(**overrides):
    return {
        "git_commit_sha": SHA,
        "git_repository": evidence.REPO,
        "git_branch": "main",
        "status": "running:healthy",
        **overrides,
    }


def history(**overrides):
    return [{"commit": SHA, "status": "finished", "pull_request_id": 0, **overrides}]


@pytest.mark.parametrize(
    "override",
    [
        {"head_sha": OTHER},
        {"head_branch": "other"},
        {"event": "pull_request"},
        {"repository": {"full_name": "someone/fork"}},
        {"head_repository": {"full_name": "someone/fork"}},
        {"name": "Untrusted CI"},
        {"status": "in_progress"},
        {"conclusion": "failure"},
        {"conclusion": "skipped"},
    ],
)
def test_ci_never_accepts_other_commits_forks_events_or_incomplete_runs(override):
    assert not evidence.ci_evidence({"workflow_runs": [ci_run(**override)]}, SHA)["passed"]


def test_latest_qualifying_ci_run_wins_and_empty_is_not_success():
    assert evidence.ci_evidence({"workflow_runs": [ci_run()]}, SHA)["passed"]
    assert not evidence.ci_evidence({"workflow_runs": [ci_run(), ci_run(id=11, conclusion="failure")]}, SHA)[
        "passed"
    ]
    assert "Empty" in evidence.ci_evidence({"workflow_runs": []}, SHA)["value"]


@pytest.mark.parametrize(
    "configuration,deployments,sha",
    [
        (app(), [], SHA),
        (app(), history(status="in_progress"), SHA),
        (app(), history(status="failed"), SHA),
        (app(), history(commit=OTHER), SHA),
        (app(), history(pull_request_id=42), SHA),
        (app(status="running:unhealthy"), history(), SHA),
        (app(git_commit_sha=OTHER), history(), SHA),
        (app(git_repository="someone/fork"), history(), SHA),
        (app(git_branch="other"), history(), SHA),
        (app(git_commit_sha=""), history(commit=""), ""),
    ],
)
def test_deployment_requires_actual_success_health_repository_and_exact_sha(configuration, deployments, sha):
    assert not evidence.deployment_matches(configuration, deployments, sha)


def test_preview_does_not_count_and_older_success_does_not_hide_new_failure():
    assert evidence.deployment_matches(app(), history(pull_request_id=42) + history(), SHA)
    assert not evidence.deployment_matches(app(), history(status="failed") + history(), SHA)


def test_readiness_is_never_release_authorization():
    ci = {"passed": True}
    assert "Blocked" in evidence.assess(SHA, {"passed": False}, app(), history(), "")["value"]
    assert "Already deployed" in evidence.assess(SHA, ci, app(), history(), SHA)["value"]
    assert "authorization NOT verified" in evidence.assess(SHA, ci, app(), history(), OTHER)["value"]


class FakeEvidence(evidence.NautionetteGitReleaseStatus):
    def __init__(self):
        self.sha = SHA
        self.main_reads = 0
        self.stage_reads = 0
        self.stage_change = {}
        self.owner = "d75b7c63a380"
        self.http_status = 200
        self.release_sha = SHA
        self.malformed_mcp = False
        self.publish_conflict = False
        self.calls = []
        self.published = None

    async def activity(self, name, params):
        self.calls.append((name, deepcopy(params)))
        if name == "http_fetch":
            assert params["method"] == "GET"
            url = params["url"]
            assert url.startswith(evidence.API + "/")
            if "/actions/runs?" in url:
                payload = {"workflow_runs": [ci_run()]}
            elif url.endswith("/releases/latest"):
                payload = {"tag_name": "v1.0.0", "html_url": "https://github.com/StromFLIX/nautionette"}
            else:
                payload = {"sha": self.release_sha}
            return {"status": self.http_status, "json": payload}
        assert name == "mcp_call"
        tool = params["tool"]
        args = params["arguments"]
        if tool == "github_get_commit":
            self.main_reads += 1
            payload = {"sha": SHA if self.main_reads == 1 else self.sha}
        elif tool == "coolify_get_application":
            change = {}
            if args["uuid"] == evidence.STAGING:
                self.stage_reads += 1
                if self.stage_reads > 1:
                    change = self.stage_change
            payload = {"data": app(**change)}
        elif tool == "coolify_list_deployments":
            payload = {"data": history()}
        elif tool == "backend_system_status":
            payload = {"environment": "production", "version": SHA}
        elif tool == "backend_get_dashboard":
            payload = {"revision": 9, "manifest": {"agent_id": self.owner}}
        elif tool == "backend_publish_dashboard_data":
            assert args["body"]["expected_revision"] == 9
            if self.publish_conflict:
                raise ValueError("Dashboard changed; read its current revision")
            self.published = deepcopy(args["body"]["data"])
            payload = {"ok": True}
        else:
            pytest.fail("Unexpected tool, especially mutation: " + tool)
        return {"ok": True, "json": None if self.malformed_mcp else payload}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(evidence.workflow, "now", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    return FakeEvidence()


@pytest.mark.asyncio
async def test_success_only_publishes_revision_fenced_evidence_silently(fake):
    result = await fake.run({"expected_sha": SHA})
    assert result["notify"] is False
    assert fake.published["main_sha"] == SHA
    assert fake.published["ci"]["passed"]
    assert fake.published["production"]["value"] == "v1.0.0"
    assert fake.published["checks"]["production deployment verified"] is True
    assert fake.stage_reads == 2 and fake.main_reads == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403, 429, 500])
async def test_failed_or_denied_reads_raise_not_silent_success_or_fake_data(fake, code):
    fake.http_status = code
    with pytest.raises(ValueError, match="no success published"):
        await fake.run({"expected_sha": SHA})
    assert fake.published is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"status": "running:unhealthy"}, {"git_commit_sha": OTHER}])
async def test_staging_changes_during_collection_fail_closed(fake, change):
    fake.stage_change = change
    with pytest.raises(ValueError, match="no mixed snapshot"):
        await fake.run({"expected_sha": SHA})
    assert fake.published is None


@pytest.mark.asyncio
async def test_main_change_during_collection_fails_closed(fake):
    fake.sha = OTHER
    with pytest.raises(ValueError, match="no mixed snapshot"):
        await fake.run({"expected_sha": SHA})
    assert fake.published is None


@pytest.mark.asyncio
async def test_exact_input_is_rechecked_before_any_evidence(fake):
    with pytest.raises(ValueError, match="Main changed"):
        await fake.run({"expected_sha": OTHER})
    assert len(fake.calls) == 1
    assert fake.published is None


@pytest.mark.asyncio
@pytest.mark.parametrize("sha", ["main", "abc123", "", None])
async def test_invalid_input_never_calls_an_integration(fake, sha):
    with pytest.raises((TypeError, ValueError)):
        await fake.run({"expected_sha": sha})
    assert not fake.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["owner", "malformed_mcp", "publish_conflict"])
async def test_owner_structured_reads_and_publication_revision_are_required(fake, fault):
    setattr(fake, fault, "other-agent" if fault == "owner" else True)
    with pytest.raises(ValueError):
        await fake.run({"expected_sha": SHA})
    assert fake.published is None


@pytest.mark.asyncio
async def test_unrelated_release_is_not_presented_as_production_version(fake):
    fake.release_sha = OTHER
    await fake.run({"expected_sha": SHA})
    assert fake.published["production"]["value"].startswith("Unverified")
