"""Configuration. Everything comes from the environment, nothing from a file."""

from __future__ import annotations

import os


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.temporal_address = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
        self.temporal_namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
        self.temporal_task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "nautionette")

        self.gateway_url = os.environ.get("AGENTGATEWAY_URL", "http://agentgateway:4000")
        self.llm_base_url = os.environ.get("LLM_BASE_URL", f"{self.gateway_url}/v1")
        self.mcp_url = os.environ.get("MCP_URL", f"{self.gateway_url}/mcp")
        self.backend_mcp_url = os.environ.get("BACKEND_MCP_URL", "http://backend:8080/mcp/")
        self.broker_url = os.environ.get("BROKER_URL", "http://docker-broker:9100")
        self.workflow_mcp_url = os.environ.get("WORKFLOW_MCP_URL", "http://workflow-mcp:8000")
        self.frontend_web_url = os.environ.get("FRONTEND_WEB_URL", "http://frontend-web:80")

        self.workflows_dir = os.environ.get("WORKFLOWS_DIR", "/workflows")
        self.seed_dir = os.environ.get("WORKFLOWS_SEED_DIR", "/seed")
        self.data_dir = os.environ.get("DATA_DIR", "/data")
        self.artifacts_dir = os.environ.get("ARTIFACTS_DIR", "/data/artifacts")

        # Server-only S3 credentials. Agent tools receive only narrowly scoped,
        # short-lived binary transfer URLs, never these values.
        self.output_s3_endpoint = os.environ.get("OUTPUT_S3_ENDPOINT", "http://maxio:9000").strip()
        self.output_s3_bucket = os.environ.get("OUTPUT_S3_BUCKET", "nautionette-outputs").strip()
        self.output_s3_region = os.environ.get("OUTPUT_S3_REGION", "us-east-1").strip()
        self.output_s3_access_key = os.environ.get("OUTPUT_S3_ACCESS_KEY", "").strip()
        self.output_s3_secret_key = os.environ.get("OUTPUT_S3_SECRET_KEY", "").strip()
        # Read-only mount of MaxIO's actual backing volume, not the container's
        # overlay filesystem. Leave empty for an independently hosted S3 service.
        self.output_storage_disk_path = os.environ.get("OUTPUT_STORAGE_DISK_PATH", "").strip()
        self.public_url = os.environ.get("APP_URL", "").strip().rstrip("/")

        # Auth. Empty token means open mode, which is only sane on a laptop.
        self.app_token = os.environ.get("APP_TOKEN", "").strip()
        self.internal_token = os.environ.get("INTERNAL_TOKEN", "").strip()

        # Interactive turns use the broker's wall-clock ceiling. Workflow calls
        # keep their separate, explicit per-activity timeout (default 900s).
        self.agent_run_timeout_seconds = max(1, int(os.environ.get("AGENT_RUN_TIMEOUT_SECONDS", "3600")))
        # Keep HTTP/MCP serving during this bounded chat drain. The orchestrator
        # must allow additional time for connection shutdown and agent cleanup.
        self.backend_chat_drain_seconds = max(0, int(os.environ.get("BACKEND_CHAT_DRAIN_SECONDS", "3600")))
        self.default_agent_set = os.environ.get("DEFAULT_AGENT_SET", "default")
        self.agent_model = os.environ.get("AGENT_MODEL", "openai/gpt-4o-mini")
        self.model_key_present = bool(os.environ.get("MODEL_KEY_PRESENT", "").strip())
        self.public_demo = _flag("PUBLIC_DEMO", False)
        self.version = os.environ.get("APP_VERSION", "dev")
        self.environment = os.environ.get("APP_ENVIRONMENT", "production").strip().lower()
        if self.environment not in {"production", "staging", "development"}:
            raise ValueError("APP_ENVIRONMENT must be production, staging, or development")

        # Origins the packaged app is served from. Add your own with CORS_ORIGINS.
        self.cors_origins = [
            "capacitor://localhost",
            "http://localhost",
            "https://localhost",
            *[origin.strip() for origin in os.environ.get("CORS_ORIGINS", "").split(",") if origin.strip()],
        ]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_token)


settings = Settings()
