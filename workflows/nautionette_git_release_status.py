"""Deterministic, read-only release evidence. No model, release, tag or deployment calls."""

import re
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

MANIFEST = {
    "schema": 1,
    "name": "nautionette_git_release_status",
    "title": "Refresh Nautionette Git/release evidence",
    "description": (
        "Read exact main CI, staging and production evidence; "
        "publish dashboard data silently. Never releases or deploys."
    ),
    "inputs": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"expected_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"}},
        "required": ["expected_sha"],
    },
    "outputs": {
        "type": "object",
        "properties": {"notify": {"type": "boolean"}, "reason": {"type": "string"}},
    },
    "agent_set": "default",
    "source": "chat",
    "timeout_minutes": 10,
    "tags": ["nautionette", "dashboard", "github", "read-only"],
}
REPO = "StromFLIX/nautionette"
API = "https://api.github.com/repos/" + REPO
DASHBOARD = "nautionette-git-release"
STAGING = "tstmjujqyyl3zkvtwb0sx8nc"
PRODUCTION = "88veejhhzve4rjcnxzlyjzbo"
SHA = re.compile(r"[0-9a-f]{40}")


def ci_evidence(payload, sha):
    """Only a genuine main push/manual CI run on this repository's exact SHA qualifies."""
    runs = [
        r
        for r in payload.get("workflow_runs", [])
        if (
            r.get("name") == "CI"
            and r.get("head_sha") == sha
            and r.get("head_branch") == "main"
            and r.get("event") in {"push", "workflow_dispatch"}
            and (r.get("repository") or {}).get("full_name") == REPO
            and (r.get("head_repository") or {}).get("full_name") == REPO
        )
    ]
    if not runs:
        return {"value": "Empty — no qualifying CI run", "tone": "warning", "detail": sha, "passed": False}
    run = max(runs, key=lambda r: r["id"])
    passed = run.get("status") == "completed" and run.get("conclusion") == "success"
    value = run.get("conclusion") if run.get("status") == "completed" else run.get("status")
    return {
        "value": value or "unknown",
        "tone": "success" if passed else "warning",
        "detail": "Exact SHA: " + sha,
        "passed": passed,
        "href": run["html_url"],
    }


def deployment_matches(app, history, sha):
    rows = [r for r in history if not r.get("pull_request_id")]
    return bool(
        rows
        and isinstance(sha, str)
        and SHA.fullmatch(sha)
        and app.get("git_repository") == REPO
        and app.get("git_branch") == "main"
        and app.get("git_commit_sha") == sha
        and app.get("status") == "running:healthy"
        and rows[0].get("commit") == sha
        and rows[0].get("status") == "finished"
    )


def assess(sha, ci, stage, history, prod_sha):
    matched = deployment_matches(stage, history, sha)
    if not ci["passed"] or not matched:
        value = "Blocked — CI or staging does not match main"
    elif prod_sha == sha:
        value = "Already deployed — no new candidate"
    else:
        value = "Evidence matches — authorization NOT verified"
    return {
        "value": value,
        "tone": "warning" if not ci["passed"] or not matched else "neutral",
        "detail": (
            "Read-only evidence, never a production authorization. Authenticated staging component health "
            "and release permissions must be checked independently at execution."
        ),
    }


@workflow.defn(name="nautionette_git_release_status")
class NautionetteGitReleaseStatus:
    async def activity(self, name, params):
        return await workflow.execute_activity(
            name,
            params,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

    async def mcp(self, tool, arguments):
        result = await self.activity("mcp_call", {"tool": tool, "arguments": arguments})
        if result.get("ok") is not True or result.get("json") is None:
            raise ValueError("Structured read unavailable: " + tool)
        return result["json"]

    async def get(self, path):
        result = await self.activity(
            "http_fetch",
            {
                "url": API + path,
                "method": "GET",
                "headers": {
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Nautionette-release-evidence",
                },
            },
        )
        if result.get("status") != 200 or not isinstance(result.get("json"), dict):
            code = result.get("status")
            state = "denied" if code in {401, 403} else "failed"
            raise ValueError(f"GitHub evidence read {state} (HTTP {code}); no success published")
        return result["json"]

    async def main_sha(self):
        commit = await self.mcp(
            "github_get_commit",
            {"owner": "StromFLIX", "repo": "nautionette", "sha": "main", "detail": "none"},
        )
        sha = commit.get("sha", "")
        if not SHA.fullmatch(sha):
            raise ValueError("Main did not resolve to an exact SHA")
        return sha

    @workflow.run
    async def run(self, params: dict) -> dict:
        expected = params.get("expected_sha", "")
        if not SHA.fullmatch(expected):
            raise ValueError("An exact expected_sha is required")
        sha = await self.main_sha()
        if sha != expected:
            raise ValueError("Main changed since action review; refresh and review again")
        # Public GitHub API supplies Actions evidence without exporting an integration credential.
        ci = ci_evidence(await self.get(f"/actions/runs?head_sha={sha}&per_page=100"), sha)
        stage = (await self.mcp("coolify_get_application", {"uuid": STAGING}))["data"]
        history = (
            await self.mcp(
                "coolify_list_deployments", {"application_uuid": STAGING, "page": 1, "per_page": 20}
            )
        )["data"]
        prod = (await self.mcp("coolify_get_application", {"uuid": PRODUCTION}))["data"]
        prod_history = (
            await self.mcp(
                "coolify_list_deployments", {"application_uuid": PRODUCTION, "page": 1, "per_page": 20}
            )
        )["data"]
        runtime = await self.mcp("backend_system_status", {})
        release = await self.get("/releases/latest")
        release_sha = (await self.get("/commits/" + release["tag_name"]))["sha"]
        prod_sha = prod.get("git_commit_sha", "")
        # A source pin is intent, not proof: require successful non-preview deployment history too.
        prod_verified = deployment_matches(prod, prod_history, prod_sha)
        version_matches = (
            prod_verified
            and runtime.get("environment") == "production"
            and runtime.get("version") == prod_sha
            and release_sha == prod_sha
            and not release.get("draft")
            and not release.get("prerelease")
        )
        version = (
            release["tag_name"]
            if version_matches
            else "Unverified — latest release differs from deployed/runtime SHA"
        )
        readiness = assess(sha, ci, stage, history, prod_sha if prod_verified else "")
        # Re-read after the evidence calls. Never publish a mixed candidate after a concurrent deployment.
        stage_now = (await self.mcp("coolify_get_application", {"uuid": STAGING}))["data"]
        history_now = (
            await self.mcp(
                "coolify_list_deployments", {"application_uuid": STAGING, "page": 1, "per_page": 20}
            )
        )["data"]
        if (
            await self.main_sha() != sha
            or any(
                stage_now.get(key) != stage.get(key)
                for key in ("git_commit_sha", "git_repository", "git_branch", "status")
            )
            or history_now != history
        ):
            raise ValueError("Main/staging changed while collecting evidence; no mixed snapshot published")
        data = {
            "main_sha": sha,
            "observed_at": workflow.now().isoformat(),
            "ci": ci,
            "readiness": readiness,
            "production": {
                "value": version,
                "detail": "Runtime SHA: " + str(runtime.get("version", "unknown")),
                "tone": "neutral",
                "href": release["html_url"],
            },
            "checks": {
                "main SHA": sha,
                "CI": ci["value"],
                "staging pin": stage.get("git_commit_sha"),
                "staging successful deployment matches main": deployment_matches(stage, history, sha),
                "staging container health (Coolify)": stage.get("status"),
                "authenticated staging component health": (
                    "Not available through the configured backend integration"
                ),
                "production deployment verified": prod_verified,
                "production runtime SHA": runtime.get("version"),
                "production release": version,
                "release write permissions": "Not verified; dashboard has NO publishing/deployment control",
                "release idempotency": "No mutation here. Existing release tags must never be moved/reused",
                "production gate": (
                    "No release action registered; CI and runtime checks must be fresh at execution"
                ),
                "checked at": workflow.now().isoformat(),
            },
        }
        current = await self.mcp("backend_get_dashboard", {"dashboard_id": DASHBOARD})
        if current["manifest"].get("agent_id") != "d75b7c63a380":
            raise ValueError("Dashboard owner changed; refusing publication")
        await self.mcp(
            "backend_publish_dashboard_data",
            {
                "dashboard_id": DASHBOARD,
                "source_id": "verification",
                "body": {"expected_revision": current["revision"], "data": data},
            },
        )
        return {
            "notify": False,
            "reason": "Read-only Git/release evidence published; no release or deployment started",
        }
