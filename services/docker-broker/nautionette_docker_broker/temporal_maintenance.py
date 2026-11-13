"""Read-only deployment fence: a swallowed Coolify post-hook error is not success."""

import json
import os

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
            # Explicitly initialized fresh/manual installations need no controller.
            return {"status": "ready", "phase": "not_required"}
        if len(jobs) != 1 or jobs[0].status != "running":
            return {"status": "degraded", "phase": "controller_unavailable"}
        result = jobs[0].exec_run(["python", "/opt/maintenance/temporal_deploy_job.py", "status"])
        state = json.loads(result.output) if result.exit_code == 0 else {}
        ready = state.get("status") == "completed" and state.get("commit") == os.environ.get("APP_VERSION")
        phase = "completed" if ready else "verification_pending"
        return {"status": "ready" if ready else "degraded", "phase": phase}
    except Exception:
        # Never return Docker output, execution IDs or private state/credentials.
        return {"status": "degraded", "phase": "inspection_failed"}
