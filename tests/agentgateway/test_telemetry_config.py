"""Gateway tracing remains optional and uses standard OTel environment settings."""

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "gateway_entrypoint", ROOT / "services/agentgateway/entrypoint.py"
)
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


def test_disabled_and_unconfigured_gateway_tracing():
    assert entrypoint.tracing_config({}) is None
    assert (
        entrypoint.tracing_config(
            {"OTEL_SDK_DISABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.test"}
        )
        is None
    )


def test_tracing_configuration_is_payload_safe():
    cfg = entrypoint.tracing_config(
        {"OTEL_SDK_DISABLED": "false", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318"}
    )
    assert cfg["otlpProtocol"] == "http"
    assert cfg["randomSampling"] is True
    assert "http.path" in cfg["fields"]["remove"]
    assert "jwt.sub" in cfg["fields"]["remove"]
    baseline = yaml.safe_load((ROOT / "services/agentgateway/config/config.yaml").read_text())
    baseline["config"]["tracing"] = cfg
    assert baseline["config"]["database"]["url"] == "sqlite:///data/agentgateway.db"
