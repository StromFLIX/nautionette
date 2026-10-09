"""Optional, non-blocking OpenTelemetry with a payload-free export boundary.

No prompts, tool arguments/results, URLs, headers or exception messages leave
this boundary. Instrumentation works without a collector; exporters are bounded.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from importlib.util import find_spec

from opentelemetry import metrics, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Status

_ALLOWED = {
    "http.method",
    "http.status_code",
    "http.status",
    "http.route",
    "http.request.method",
    "http.response.status_code",
    "server.address",
    "server.port",
    "network.protocol.version",
    "exception.type",
    "error.type",
    "rpc.system",
    "rpc.service",
    "rpc.method",
    "temporalWorkflowID",
    "temporalRunID",
    "temporalActivityID",
    "temporalActivityType",
    "temporalWorkflowType",
    "temporalAttempt",
    "nautionette.chat_id",
    "nautionette.turn_id",
    "gen_ai.request.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.operation.name",
    "gen_ai.tool.name",
    "nautionette.agent_set",
}
_providers: list = []
_initialized = False


def safe_span(span: ReadableSpan) -> ReadableSpan:
    """Allowlist attributes and drop exception text, status descriptions and baggage."""
    return ReadableSpan(
        name=(
            f"{(span.attributes or {}).get('http.method', 'HTTP')} "
            f"{(span.attributes or {}).get('http.route', 'request')}"
            if span.kind == trace.SpanKind.SERVER
            else span.name
        ),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes={key: value for key, value in (span.attributes or {}).items() if key in _ALLOWED},
        events=[
            Event("exception", {"exception.type": event.attributes["exception.type"]}, event.timestamp)
            for event in span.events
            if "exception.type" in (event.attributes or {})
        ],
        links=[],
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class SafeSpanExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter):
        self.delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]):
        return self.delegate.export([safe_span(span) for span in spans])

    def shutdown(self):
        self.delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self.delegate.force_flush(timeout_millis)


class SafeLoggingHandler(logging.Handler):
    """Correlated severity/source logs, deliberately not free-form application text."""

    def __init__(self, level: int, logger_provider: LoggerProvider):
        super().__init__(level)
        self.provider = logger_provider

    def emit(self, record: logging.LogRecord) -> None:
        # Do not export exporter failures back to the failed exporter.
        if record.name.startswith(("opentelemetry", "urllib3", "httpcore")):
            return
        severity = next(
            (
                value
                for threshold, value in (
                    (50, SeverityNumber.FATAL),
                    (40, SeverityNumber.ERROR),
                    (30, SeverityNumber.WARN),
                    (20, SeverityNumber.INFO),
                    (0, SeverityNumber.DEBUG),
                )
                if record.levelno >= threshold
            ),
            SeverityNumber.INFO,
        )
        attrs = {"code.function.name": record.funcName or "", "code.line.number": record.lineno}
        if record.exc_info and record.exc_info[0]:
            attrs["exception.type"] = record.exc_info[0].__name__
        self.provider.get_logger(record.name).emit(
            timestamp=int(record.created * 1_000_000_000),
            severity_number=severity,
            severity_text=record.levelname,
            body=f"{record.name} {record.levelname}",
            attributes=attrs,
        )


def configure(service: str) -> bool:
    global _initialized
    if _initialized:
        return True
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    if not endpoint or os.environ.get("OTEL_SDK_DISABLED", "false").lower() == "true":
        return False
    resource = Resource.create(
        {
            "service.name": service,
            "service.namespace": "nautionette",
            "service.version": os.environ.get("APP_VERSION", "dev"),
            "deployment.environment": os.environ.get("APP_ENVIRONMENT", "production"),
            "deployment.environment.name": os.environ.get("APP_ENVIRONMENT", "production"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            SafeSpanExporter(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=3)),
            max_queue_size=2048,
            max_export_batch_size=256,
        )
    )
    trace.set_tracer_provider(provider)
    meter = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=3),
                export_interval_millis=30000,
            )
        ],
    )
    metrics.set_meter_provider(meter)
    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=endpoint + "/v1/logs", timeout=3),
            max_queue_size=2048,
        )
    )
    logging.getLogger().addHandler(SafeLoggingHandler(level=logging.INFO, logger_provider=logs))
    # HTTPX is not installed in every service image (the Docker broker uses
    # requests). Do not turn an absent optional client into a false error alert.
    if find_spec("httpx") is not None:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    _providers.extend([provider, meter, logs])
    _initialized = True
    return True


def instrument_app(app, service: str) -> None:
    if configure(service):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])


def temporal_interceptors() -> list:
    if not _initialized:
        return []
    from temporalio.contrib.opentelemetry import TracingInterceptor

    return [TracingInterceptor(always_create_workflow_spans=True)]


def flush() -> None:
    for provider in _providers:
        provider.force_flush(timeout_millis=3000)
