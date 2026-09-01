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
        self.broker_url = os.environ.get("BROKER_URL", "http://docker-broker:9100")
        self.workflow_mcp_url = os.environ.get("WORKFLOW_MCP_URL", "http://workflow-mcp:8000")
        self.frontend_web_url = os.environ.get("FRONTEND_WEB_URL", "http://frontend-web:80")

        self.workflows_dir = os.environ.get("WORKFLOWS_DIR", "/workflows")
        self.seed_dir = os.environ.get("WORKFLOWS_SEED_DIR", "/seed")
        self.data_dir = os.environ.get("DATA_DIR", "/data")
        self.artifacts_dir = os.environ.get("ARTIFACTS_DIR", "/data/artifacts")

        # Auth. Empty token means open mode, which is only sane on a laptop.
        self.app_token = os.environ.get("APP_TOKEN", "").strip()
        self.internal_token = os.environ.get("INTERNAL_TOKEN", "").strip()

        self.default_agent_set = os.environ.get("DEFAULT_AGENT_SET", "default")
        self.agent_model = os.environ.get("AGENT_MODEL", "openai/gpt-4o-mini")
        self.model_key_present = bool(os.environ.get("MODEL_KEY_PRESENT", "").strip())
        self.public_demo = _flag("PUBLIC_DEMO", False)
        self.version = os.environ.get("APP_VERSION", "dev")

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_token)


settings = Settings()
