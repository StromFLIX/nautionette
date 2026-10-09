"""The export boundary must not disclose application data or affect normal work."""

import logging

from nautionette.telemetry import SafeLoggingHandler, SafeSpanExporter, configure, safe_span
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode


def test_exporter_drops_payloads_and_exception_text():
    exported = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SafeSpanExporter(exported)))
    with provider.get_tracer("test").start_as_current_span("GET") as span:
        span.set_attribute("http.url", "https://example.com?token=secret")
        span.set_attribute("http.method", "GET")
        span.set_attribute("gen_ai.input.messages", "private prompt")
        span.set_attribute("gen_ai.tool.call.arguments", "password=secret")
        span.record_exception(ValueError("private provider response secret"))
        span.set_status(Status(StatusCode.ERROR, "authorization=secret"))
    result = exported.get_finished_spans()[0]
    assert result.attributes == {"http.method": "GET"}
    assert result.status.status_code == StatusCode.ERROR
    assert not result.status.description
    assert result.events[0].attributes == {"exception.type": "ValueError"}
    provider.shutdown()


def test_server_names_never_contain_unmatched_paths_or_query_strings():
    raw = ReadableSpan(
        "GET /secret-token?password=secret", kind=SpanKind.SERVER, attributes={"http.method": "GET"}
    )
    assert safe_span(raw).name == "GET request"
    matched = ReadableSpan(
        "ignored",
        kind=SpanKind.SERVER,
        attributes={"http.method": "GET", "http.route": "/api/chats/{chat_id}"},
    )
    assert safe_span(matched).name == "GET /api/chats/{chat_id}"
    assert safe_span(ReadableSpan("no attrs")).attributes == {}


def test_temporal_server_spans_keep_operation_names():
    for name in ("RunWorkflow:verification", "RunActivity:http_fetch"):
        raw = ReadableSpan(
            name,
            kind=SpanKind.SERVER,
            attributes={"temporalWorkflowID": "verification-123"},
        )
        assert safe_span(raw).name == name
    http = ReadableSpan(
        "GET /private?token=secret",
        kind=SpanKind.SERVER,
        attributes={"http.request.method": "GET"},
    )
    assert safe_span(http).name == "GET request"


def test_logging_never_formats_untrusted_text():
    records = []

    class Provider:
        def get_logger(self, name):
            return self

        def emit(self, **record):
            records.append(record)

    handler = SafeLoggingHandler(logging.INFO, Provider())
    record = logging.LogRecord(
        "backend", logging.ERROR, __file__, 1, "Bearer secret: %s", ("private prompt",), None
    )
    record.authorization = "secret"
    handler.emit(record)
    assert records[0]["body"] == "backend ERROR"
    assert "secret" not in str(records[0])
    handler.emit(
        logging.LogRecord("opentelemetry.exporter", logging.ERROR, __file__, 1, "export failed", (), None)
    )
    assert len(records) == 1  # no exporter feedback loop


def test_disabled_configuration_does_not_install_exporters(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://unused:4318")
    assert configure("test-disabled") is False
