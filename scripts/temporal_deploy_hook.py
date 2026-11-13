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
    from nautionette.temporal_state import controller_state

    return controller_state(container)


def retire(container, read_receipt):
    # Never stop prepared, failed or ambiguous work. A durable, validated receipt
    # must exist first; health checks can read it without a privileged live process.
    if read_receipt(container) is None:
        raise RuntimeError("Cannot retire maintenance without its completion receipt")
    container.update(restart_policy={"Name": "no"})
    container.reload()
    if container.status == "running":
        container.stop(timeout=5)
    if read_receipt(container) is None:
        raise RuntimeError("Maintenance completion receipt disappeared")


def hook(payload: dict):
    # This hook initially runs in the *old* broker, which has not installed the
    # new reader yet. Use candidate-pinned standard-library source, not host code.
    readers = {"__name__": "temporal_state"}
    exec(compile(payload["files"]["temporal_state.py"], "temporal_state", "exec"), readers)  # noqa: S102 - candidate-pinned code
    read_state, read_receipt = readers["controller_state"], readers["completed_receipt"]
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
        controller.reload()
        if controller.status == "running":
            result = controller.exec_run(["python", "/opt/maintenance/temporal_deploy_job.py", "finish"])
            if result.exit_code:
                raise RuntimeError("Temporal post-deployment verification failed")
        if read_state(controller).get("status") != "completed":
            raise RuntimeError("Temporal post-deployment verification failed")
        retire(controller, read_receipt)
        print(
            "Temporal deployment verified; maintenance controller stopped and receipt retained.", flush=True
        )
        return
    if mode != "prepare":
        raise ValueError("Unknown Temporal hook operation")
    if controller is not None:
        previous = read_state(controller)
        if previous.get("commit") != commit or previous.get("safe_to_retry"):
            if previous.get("status") != "completed" and not previous.get("safe_to_retry"):
                raise RuntimeError("Unfinished Temporal migration; recover the existing controller first")
            controller.reload()
            if controller.status == "running":
                controller.stop(timeout=5)
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
        result = read_state(controller)
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
