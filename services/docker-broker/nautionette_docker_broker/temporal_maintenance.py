"""Read-only deployment fence: a swallowed Coolify post-hook error is not success."""

import os

from nautionette.temporal_state import controller_state

from . import daemon, workers


def snapshot() -> dict:
    try:
        filters = workers.worker_filters()
        if filters is None:
            return {"status": "degraded", "phase": "unknown_project"}
        project = next(
            value.split("=", 1)[1]
            for value in filters["label"]
            if value.startswith("com.docker.compose.project=")
        )
        jobs = daemon.client().containers.list(
            all=True, filters={"label": "nautionette.temporal.project=" + project}
        )
        if not jobs:
            # Automatic deployments require a receipt; only explicitly initialized
            # fresh/manual installations may operate without a controller.
            if os.environ.get("TEMPORAL_MAINTENANCE_APP"):
                return {"status": "degraded", "phase": "receipt_missing"}
            return {"status": "ready", "phase": "not_required"}
        if len(jobs) != 1:
            return {"status": "degraded", "phase": "controller_unavailable"}
        state = controller_state(jobs[0])
        ready = state.get("status") == "completed" and state.get("commit") == os.environ.get("APP_VERSION")
        if ready and os.environ.get("TEMPORAL_MAINTENANCE_APP"):
            policy = jobs[0].attrs.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
            if jobs[0].status != "exited" or policy != "no":
                return {"status": "degraded", "phase": "cleanup_pending"}
        phase = "completed" if ready else "verification_pending"
        return {"status": "ready" if ready else "degraded", "phase": phase}
    except Exception:
        # Never return Docker output, execution IDs or private state/credentials.
        return {"status": "degraded", "phase": "inspection_failed"}
