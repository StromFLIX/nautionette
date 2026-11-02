"""Release preparation must not pin deploy-source hashes or weaken real gates."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.exceptions import ApplicationError

PATH = Path(__file__).resolve().parents[2] / "workflows" / "prepare_nautionette_release.py"
spec = importlib.util.spec_from_file_location("prepare_release", PATH)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

SHA = "a" * 40
BASE = "b" * 40
MESSAGE = "Add useful release preparation regression coverage"
CONTROL_PATHS = ("docs/github-pipelines.md", "scripts/coolify_release.py", ".github/workflows/deploy.yml")


class Preparation(release.PrepareNautionetteRelease):
    def __init__(self):
        self.app = {
            "git_repository": release.REPO,
            "git_branch": "main",
            "git_commit_sha": SHA,
            "status": "running:healthy",
            "description": "Staging",
        }
        self.deployment = {
            "deployment_uuid": "tested-deployment",
            "commit": SHA,
            "status": "finished",
        }
        self.releases = [{"tag_name": "v1.7.1", "draft": False, "prerelease": False}]
        self.tags = [{"name": "v1.7.1", "sha": BASE}]
        self.comparison_status = "ahead"
        self.ancestry_status = "ahead"
        self.changes = []
        self.docs = {}
        self.calls = []
        self.artifacts = {}
        self.snapshot_count = 0
        self.after_notes = lambda: None
        self.report = {
            "changes": [
                {
                    "category": "Maintenance",
                    "summary": MESSAGE,
                    "source": f"{release.WEB}/commit/{SHA}",
                    "quote": MESSAGE,
                }
            ],
            "blockers": [],
            "warnings": [],
        }

    async def mcp(self, tool, arguments):
        self.calls.append((tool, arguments))
        if tool == "coolify_get_application":
            self.snapshot_count += 1
            return {"data": dict(self.app)}
        if tool == "coolify_list_deployments":
            return {"data": [dict(self.deployment)]}
        if tool == "github_list_releases":
            return [dict(row) for row in self.releases]
        if tool == "github_list_tags":
            return [dict(row) for row in self.tags]
        assert tool == "backend_get_agent"
        return {"resolved": {"model": "openai/test-notes"}}

    async def get(self, url):
        self.calls.append(("get", url))
        if url == f"{release.API}/compare/{SHA}...main?per_page=1":
            return {"json": {"status": self.ancestry_status}}
        if url == f"{release.API}/commits/v1.7.1":
            return {"json": {"sha": BASE}}
        if url == f"{release.API}/compare/{BASE}...{SHA}?per_page=100&page=1":
            return {
                "json": {
                    "status": self.comparison_status,
                    "files": self.changes,
                    "commits": [{"sha": SHA, "commit": {"message": MESSAGE}}],
                    "total_commits": 1,
                }
            }
        prefix = f"https://raw.githubusercontent.com/{release.REPO}/{SHA}/"
        assert url.startswith(prefix), "Preparation must not fetch/hash moving deployment controls"
        path = url.removeprefix(prefix)
        if path == "services/frontend/package.json":
            return {"body": json.dumps({"version": "0.1.0"})}
        if path in {"libs/nautionette/pyproject.toml", "pyproject.toml"}:
            return {"body": "# Component version is not a release-tag constraint"}
        return {"body": self.docs[path]}

    async def activity(self, name, params, minutes=3):
        self.calls.append((name, params))
        if name == "save_artifact":
            self.artifacts[params["name"]] = params["content"]
            return {}
        assert name == "http_fetch"
        assert params["method"] == "POST"
        assert params["url"] == "http://agentgateway:4000/v1/chat/completions"
        assert "tools" not in params["json"]
        self.after_notes()
        return {
            "status": 200,
            "json": {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.report)}}]},
        }


@pytest.fixture
def preparation(monkeypatch):
    monkeypatch.setattr(release.workflow, "info", lambda: SimpleNamespace(workflow_id="test-release"))
    monkeypatch.setattr(release.workflow, "now", lambda: datetime(2026, 11, 2, tzinfo=UTC))
    return Preparation()


@pytest.mark.parametrize("path", CONTROL_PATHS)
@pytest.mark.parametrize(
    "content", ["# Edited policy documentation", "# New manual promotion implementation"]
)
async def test_control_edits_do_not_block_preparation(preparation, path, content):
    preparation.changes = [{"filename": path, "patch": "+" + content, "status": "modified"}]
    preparation.docs[path] = content
    result = await preparation.run({"version": "minor"})
    summary = result["summary"]
    assert "Preparation passed" in summary
    assert "v1.8.0" in summary
    assert f"git tag -a v1.8.0 {SHA}" in summary
    assert "does not audit or certify deployment-control code" in summary
    assert "Component/package versions are advisory" in summary
    assert "nothing has been tagged, published, or deployed" in summary
    assert preparation.snapshot_count == 2
    assert set(preparation.artifacts) == {
        "release-review-test-release.txt",
        "release-preparation-test-release-notes.md",
        "release-preparation-test-release.md",
    }
    assert {name for name, _ in preparation.calls} <= {
        "coolify_get_application",
        "coolify_list_deployments",
        "github_list_releases",
        "github_list_tags",
        "backend_get_agent",
        "get",
        "http_fetch",
        "save_artifact",
    }


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"status": "running:unhealthy"}, "not healthy"),
        ({"git_commit_sha": "main"}, "full commit SHA"),
        ({"git_branch": "feature"}, "repository or branch"),
        ({"git_repository": "other/repo"}, "repository or branch"),
        ({"description": "[nautionette-refresh:run] Staging"}, "maintenance guard"),
    ],
)
async def test_real_staging_guards_remain(preparation, override, error):
    preparation.app.update(override)
    with pytest.raises(ApplicationError, match=error) as exc:
        await preparation.run({"version": "minor"})
    assert exc.value.non_retryable
    assert not any("git tag -a" in value for value in preparation.artifacts.values())


@pytest.mark.parametrize("status", ["queued", "in_progress", "failed"])
async def test_unfinished_staging_deployment_blocks(preparation, status):
    preparation.deployment["status"] = status
    with pytest.raises(ApplicationError, match="did not finish"):
        await preparation.run({})


@pytest.mark.parametrize("field", ["git_commit_sha", "deployment_uuid"])
async def test_staging_freshness_is_rechecked_after_notes(preparation, field):
    def advance():
        if field == "git_commit_sha":
            preparation.app[field] = "c" * 40
            preparation.deployment["commit"] = "c" * 40
        else:
            preparation.deployment[field] = "new-deployment"

    preparation.after_notes = advance
    with pytest.raises(ApplicationError, match="Staging changed during preparation"):
        await preparation.run({})


async def test_tag_inventory_is_rechecked_after_notes(preparation):
    preparation.after_notes = lambda: preparation.tags.append({"name": "v1.8.0", "sha": SHA})
    with pytest.raises(ApplicationError, match="Releases or tags changed"):
        await preparation.run({"version": "minor"})


@pytest.mark.parametrize("tag", ["v1.7.1", "v1.0.0", "v01.8.0", "v1.8.0-rc.1", "v1.8.0;echo bad"])
async def test_invalid_duplicate_or_old_versions_still_block(preparation, tag):
    with pytest.raises(ApplicationError):
        await preparation.run({"version": tag})
    assert not any("git tag -a" in value for value in preparation.artifacts.values())


async def test_unreleased_occupied_tag_cannot_be_reused(preparation):
    preparation.tags.append({"name": "v1.8.0", "sha": "c" * 40})
    with pytest.raises(ApplicationError, match="already exists"):
        await preparation.run({"version": "minor"})


async def test_staging_must_belong_to_main(preparation):
    preparation.ancestry_status = "diverged"
    with pytest.raises(ApplicationError, match="not an ancestor"):
        await preparation.run({})


async def test_staging_must_have_new_release_commits(preparation):
    preparation.comparison_status = "identical"
    with pytest.raises(ApplicationError, match="no new commits"):
        await preparation.run({})


async def test_evidenced_release_blockers_still_block(preparation):
    preparation.report["blockers"] = ["Evidenced migration safety problem"]
    with pytest.raises(ApplicationError, match="Release review blocked"):
        await preparation.run({})


async def test_fabricated_evidence_still_blocks(preparation):
    preparation.report["changes"][0]["quote"] = "This quote is absent from the source"
    with pytest.raises(ApplicationError, match="verifiable verbatim evidence"):
        await preparation.run({})


async def test_repository_read_failures_are_not_silenced(monkeypatch):
    async def fail(*args, **kwargs):
        return {"status": 503}

    workflow = release.PrepareNautionetteRelease()
    monkeypatch.setattr(workflow, "activity", fail)
    with pytest.raises(ValueError, match="HTTP 503"):
        await workflow.get(release.API)
