"""GitHub Actions -> Coolify, pinning the tested commit. No chat notifications.

Requires repository/environment configuration documented in docs/staging.md.
Non-release events, prereleases and successful duplicate deliveries are silent.
Never retry a deployment POST blindly: its response may have been lost.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

STABLE_TAG = re.compile(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
SHA = re.compile(r"[0-9a-f]{40}")


def candidate(event_name: str, event: dict, repository: str) -> tuple[str, str] | None:
    if event.get("repository", {}).get("full_name") != repository:
        raise ValueError("Event repository does not match this repository")
    if event_name == "release":
        release = event.get("release", {})
        tag = release.get("tag_name", "")
        if (
            event.get("action") != "published"
            or release.get("draft") is not False
            or release.get("prerelease") is not False
            or not STABLE_TAG.fullmatch(tag)
        ):
            return None
        # Resolve annotated/lightweight tags to a commit, never target_commitish
        # (which can simply be a moving branch name).
        commit = subprocess.check_output(  # noqa: S603 - tag strictly validated above; no shell
            ["/usr/bin/git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
            text=True,
        ).strip()
        target = "production"
    elif event_name == "workflow_run":
        run = event.get("workflow_run", {})
        if (
            event.get("action") != "completed"
            or run.get("conclusion") != "success"
            or run.get("event") not in {"push", "workflow_dispatch"}
            or run.get("head_branch") != "main"
            or run.get("head_repository", {}).get("full_name") != repository
        ):
            return None
        commit, target = run.get("head_sha", ""), "staging"
    else:
        return None
    if not SHA.fullmatch(commit):
        raise ValueError("Expected a full commit SHA")
    return target, commit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward service credentials to a redirect destination.
        return None


def https_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Configure a direct HTTPS URL without credentials/query/fragment")
    return value.rstrip("/")


def request(url: str, token: str, method: str = "GET", payload: dict | None = None):
    if urlsplit(url).scheme != "https":
        raise ValueError("Only HTTPS is permitted")
    req = urllib.request.Request(  # noqa: S310 - HTTPS only, redirects disabled
        url,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Coolify bodies can contain logs/configuration. Never echo them.
        raise RuntimeError(f"Deployment API returned HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("Deployment API unreachable; inspect before retrying a deployment") from None


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Configure {name} in GitHub Actions first")
    return value


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError("Invalid Coolify resource identifier")
    return value


class Coolify:
    def __init__(self):
        self.url = https_url(required("COOLIFY_URL")) + "/api/v1"
        self.token = required("COOLIFY_TOKEN")

    def call(self, path: str, method: str = "GET", payload: dict | None = None):
        return request(self.url + path, self.token, method, payload)

    def history(self, app: str) -> list[dict]:
        result = self.call(f"/deployments/applications/{app}?take=100")
        rows = result["deployments"]
        # PR previews must never qualify as the staging/main deployment.
        return [row for row in rows if not row.get("pull_request_id")]

    def deployed(self, app: str, commit: str) -> bool:
        details = self.call(f"/applications/{app}")
        history = self.history(app)
        return bool(
            details.get("git_commit_sha") == commit
            and str(details.get("status", "")).startswith("running")
            and history
            and history[0].get("status") == "finished"
            and history[0].get("commit") == commit
        )


def healthy(target: str) -> None:
    url = https_url(required("APP_URL"))
    state = request(url + "/api/system", required("APP_TOKEN"))
    expected = {"temporal", "broker", "agentgateway", "workflow-mcp"}
    components = state.get("components", [])
    if (
        state.get("environment") != target
        or state.get("auth_enabled") is not True
        or {item.get("name") for item in components} != expected
        or any(item.get("status") != "ok" for item in components)
    ):
        raise RuntimeError("Deployment has not passed authenticated environment/component health checks")


def deploy(target: str, commit: str) -> str | None:
    api = Coolify()
    app = identifier(required("COOLIFY_APPLICATION_UUID"))
    # Preflight health configuration before making changes.
    https_url(required("APP_URL"))
    required("APP_TOKEN")
    if api.deployed(app, commit):
        healthy(target)
        return None
    staging = identifier(required("COOLIFY_STAGING_APPLICATION_UUID")) if target == "production" else app
    if str(api.call(f"/applications/{staging}").get("description") or "").startswith("[nautionette-refresh:"):
        raise RuntimeError("Staging refresh maintenance guard is active; finish/recover the refresh first")
    if target == "production":
        if staging == app or not api.deployed(staging, commit):
            raise RuntimeError("Release must be the exact commit currently deployed successfully in staging")
    if any(row.get("status") in {"queued", "in_progress"} for row in api.history(app)):
        raise RuntimeError("Another deployment is active; refusing to change its commit")
    details = api.call(f"/applications/{app}")
    if details.get("git_branch") != "main" or details.get("build_pack") != "dockercompose":
        raise RuntimeError("Expected the main-branch Docker Compose application")
    if details.get("git_repository", "").removesuffix(".git").removeprefix("https://github.com/") != required(
        "GITHUB_REPOSITORY"
    ):
        raise RuntimeError("Coolify application uses a different repository")
    api.call(
        f"/applications/{app}",
        "PATCH",
        {
            "git_commit_sha": commit,
            "is_auto_deploy_enabled": False,
        },
    )
    if api.call(f"/applications/{app}").get("git_commit_sha") != commit:
        raise RuntimeError("Coolify did not accept the commit pin; nothing was deployed")
    queued = api.call("/deploy", "POST", {"uuid": app, "force": False})
    deployments = queued.get("deployments", [])
    if len(deployments) != 1 or deployments[0].get("resource_uuid") != app:
        raise RuntimeError("Unexpected deployment acknowledgement; inspect Coolify before retrying")
    deployment = identifier(deployments[0].get("deployment_uuid", ""))
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        state = api.call(f"/deployments/{deployment}")
        status = state.get("status")
        if status == "finished":
            if state.get("commit") != commit:
                raise RuntimeError("Coolify deployed a different commit! Inspect production immediately")
            break
        if status not in {"queued", "in_progress"}:
            raise RuntimeError("Coolify deployment unsuccessful; inspect its deployment history")
        time.sleep(10)
    else:
        raise RuntimeError("Deployment timed out; it may still be active in Coolify")
    # Worker images can still be initializing after Compose reports finished.
    for attempt in range(30):
        try:
            healthy(target)
            return deployment
        except RuntimeError:
            if attempt == 29:
                raise
            time.sleep(10)
    return None


def main() -> None:
    event = json.loads(Path(required("GITHUB_EVENT_PATH")).read_text())
    selection = candidate(required("GITHUB_EVENT_NAME"), event, required("GITHUB_REPOSITORY"))
    if selection is None:
        return
    target, commit = selection
    if target == "production" and commit != required("GITHUB_SHA"):
        raise ValueError("Release tag moved since the event; refusing to deploy")
    if target == "staging":
        current = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "refs/remotes/origin/main"],
            text=True,
        ).strip()
        if commit != current:
            return  # A late CI result must not roll staging back.
    deployment = deploy(target, commit)
    if deployment:
        print(f"Deployed {commit} to {target}; deployment {deployment}; health checks passed.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        sys.exit(1)
