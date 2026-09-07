"""Everything this broker is told, and nothing it decides for itself."""

from __future__ import annotations

import os

AGENT_IMAGES_DIR = os.environ.get("AGENT_IMAGES_DIR", "/agent-images")
IMAGE_PREFIX = os.environ.get("IMAGE_PREFIX", "nautionette/pi-")
BASE_IMAGE = os.environ.get("BASE_IMAGE", "nautionette/pi-base:dev")
TARGET_NETWORK = os.environ.get("TARGET_NETWORK", "nautionette-internal")
AGENT_NETWORK = os.environ.get("AGENT_NETWORK", "nautionette-agents")
AGENT_EGRESS_NETWORK = os.environ.get("AGENT_EGRESS_NETWORK", "nautionette-agent-egress")
WORKFLOWS_VOLUME = os.environ.get("WORKFLOWS_VOLUME", "nautionette-workflows")
WORKER_LABEL = os.environ.get("WORKER_SERVICE_LABEL", "com.docker.compose.service=worker")
# Set only if this broker cannot read its own compose project label.
PROJECT_OVERRIDE = os.environ.get("COMPOSE_PROJECT", "").strip()
RUN_TIMEOUT = int(os.environ.get("AGENT_RUN_TIMEOUT_SECONDS", "900"))
# How long a call will wait for an image that has to be built before it can run.
IMAGE_BUILD_TIMEOUT = int(os.environ.get("AGENT_IMAGE_BUILD_TIMEOUT_SECONDS", "900"))
AGENT_MEMORY = os.environ.get("AGENT_MEMORY_LIMIT", "1g")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "").strip()
STOP_GRACE = int(os.environ.get("WORKER_STOP_GRACE_SECONDS", "60"))
RECONCILE_SECONDS = max(1, int(os.environ.get("CONTAINER_RECONCILE_SECONDS", "30")))
WORKER_REPLICAS = max(1, int(os.environ.get("WORKER_REPLICAS", "1")))
WORKER_IMAGE = os.environ.get("WORKER_IMAGE", "nautionette/worker:dev")
ARTIFACTS_VOLUME = os.environ.get("ARTIFACTS_VOLUME", "")
WORKER_ENVIRONMENT = {
    "INTERNAL_TOKEN": INTERNAL_TOKEN,
    "TEMPORAL_ADDRESS": os.environ.get("TEMPORAL_ADDRESS", "temporal:7233"),
    "TEMPORAL_NAMESPACE": os.environ.get("TEMPORAL_NAMESPACE", "default"),
    "TEMPORAL_TASK_QUEUE": os.environ.get("TEMPORAL_TASK_QUEUE", "nautionette"),
    "BACKEND_URL": os.environ.get("AGENT_BACKEND_URL", "http://backend:8080"),
    "MCP_URL": os.environ.get("WORKER_MCP_URL", "http://agentgateway:4000/mcp"),
    "WORKFLOWS_DIR": "/workflows",
    "ARTIFACTS_DIR": "/artifacts",
    "WORKER_GRACE_SECONDS": os.environ.get("WORKER_GRACE_SECONDS", "50"),
}

AGENT_ENVIRONMENT = {
    "AGENTGATEWAY_URL": os.environ.get("AGENT_AGENTGATEWAY_URL", "http://agentgateway:4000"),
    "BACKEND_URL": os.environ.get("AGENT_BACKEND_URL", "http://backend:8080"),
    "PI_OFFLINE": "1",
    "PI_SKIP_VERSION_CHECK": "1",
    "PI_TELEMETRY": "0",
}
