"""Coolify hook, executed in the existing broker's Docker control boundary.

This is trusted deployment code, not a broker HTTP endpoint or a user-supplied
command API. The only Docker socket already present is used to launch a private,
short-lived maintenance controller. No credentials/configuration are printed.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import sys
import tarfile
import time

import docker

LABEL = "nautionette.temporal.project"


def state(container):
    result = container.exec_run(["python", "/opt/maintenance/temporal_deploy_job.py", "status"])
    if result.exit_code:
        raise RuntimeError("Cannot inspect durable Temporal maintenance state")
    return json.loads(result.output)


def hook(payload: dict):
    client = docker.from_env()
    app, commit, mode = payload["app"], payload["commit"], payload["mode"]
    own = client.containers.get(os.environ["HOSTNAME"])
    project = own.labels.get("com.docker.compose.project")
    if project != app or own.labels.get("com.docker.compose.service") != "docker-broker":
        raise RuntimeError("Temporal hook must run in the target application's broker")
    name = "nautionette-temporal-" + app
    try:
        controller = client.containers.get(name)
    except docker.errors.NotFound:
        controller = None
    if controller is not None and controller.labels.get(LABEL) != app:
        raise RuntimeError("Maintenance container name belongs to a different resource")
    if mode == "finish":
        if controller is None or controller.labels.get("nautionette.temporal.commit") != commit:
            raise RuntimeError("Missing candidate maintenance controller")
        result = controller.exec_run(["python", "/opt/maintenance/temporal_deploy_job.py", "finish"])
        if result.exit_code or state(controller).get("status") != "completed":
            raise RuntimeError("Temporal post-deployment verification failed")
        print("Temporal version, schemas, shard count and deployed revision verified.", flush=True)
        return
    if mode != "prepare":
        raise ValueError("Unknown Temporal hook operation")
    if controller is not None:
        previous = state(controller)
        if previous.get("commit") != commit or previous.get("safe_to_retry"):
            if previous.get("status") != "completed" and not previous.get("safe_to_retry"):
                raise RuntimeError("Unfinished Temporal migration; recover the existing controller first")
            controller.stop(timeout=30)
            controller.remove()
            controller = None
    if controller is None:
        context = io.BytesIO()
        with tarfile.open(fileobj=context, mode="w") as archive:
            for path, content in payload["files"].items():
                item = tarfile.TarInfo(path)
                data = content.encode()
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        context.seek(0)
        image, _ = client.images.build(
            fileobj=context,
            custom_context=True,
            dockerfile="context/Dockerfile.controller",
            rm=True,
            labels={LABEL: app, "nautionette.temporal.commit": commit},
        )
        run_id = commit[:12] + "-" + secrets.token_hex(4) + "-" + app
        controller = client.containers.run(
            image.id,
            name=name,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            labels={LABEL: app, "nautionette.temporal.commit": commit},
            environment={
                "MAINTENANCE_APP": app,
                "MAINTENANCE_COMMIT": commit,
                "MAINTENANCE_ENVIRONMENT": payload["target"],
                "MAINTENANCE_RUN_ID": run_id,
                "MAINTENANCE_SHARDS": payload["shards"],
            },
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                "/var/lib/nautionette-temporal": {"bind": "/var/lib/nautionette-temporal", "mode": "rw"},
                "/var/lib/nautionette-refresh": {"bind": "/var/lib/nautionette-refresh", "mode": "rw"},
            },
            security_opt=["no-new-privileges:true"],
            log_config=docker.types.LogConfig(type="json-file", config={"max-size": "10m", "max-file": "3"}),
        )
    deadline = time.monotonic() + 105 * 60
    while True:
        result = state(controller)
        if result.get("commit") != commit:
            raise RuntimeError("Maintenance controller has a different candidate revision")
        if result["status"] in {"prepared", "current", "completed"}:
            print("Temporal pre-deployment checks passed; ready for container replacement.", flush=True)
            return
        if result["status"] in {"failed", "needs_recovery"}:
            raise RuntimeError("Temporal migration failed; inspect private controller state/backups")
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Temporal migration still active; inspect existing controller, do not relaunch"
            )
        time.sleep(5)


# The deployment script wraps this trusted source with a candidate payload.
if __name__ == "__main__":
    try:
        hook(json.loads(sys.argv[1]))
    except Exception:
        print("Temporal deployment hook failed. Inspect the private maintenance controller.", file=sys.stderr)
        sys.exit(1)
