"""Exercise real SDK export/propagation without modifying pytest's global providers."""

import os
import subprocess
import sys


def test_real_otlp_export_and_http_propagation():
    script = """
import asyncio, logging, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from fastapi import FastAPI
from starlette.testclient import TestClient
import httpx
from nautionette.telemetry import instrument_app, flush, temporal_interceptors
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

received = {}
propagated = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        received.setdefault(self.path, []).append(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
    def do_GET(self):
        propagated.append(self.headers.get('traceparent'))
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
import os
os.environ['OTEL_EXPORTER_OTLP_ENDPOINT'] = f'http://127.0.0.1:{server.server_port}'
app = FastAPI()
@app.get('/echo/{item}')
async def echo(item: str):
    logging.getLogger('nautionette.test').warning('private-token %s', 'private-prompt')
    async with httpx.AsyncClient() as client:
        await client.get(f'http://127.0.0.1:{server.server_port}/downstream?token=private-token')
    return {'ok': True}
instrument_app(app, 'test-backend')
with TestClient(app) as client:
    assert client.get('/echo/test?token=private-token').status_code == 200
flush()
assert temporal_interceptors()
assert {'/v1/traces', '/v1/metrics', '/v1/logs'} <= received.keys(), received.keys()
for bodies in received.values():
    assert b'private-token' not in b''.join(bodies)
    assert b'private-prompt' not in b''.join(bodies)
spans = []
for body in received['/v1/traces']:
    req = ExportTraceServiceRequest.FromString(body)
    for resource in req.resource_spans:
        for scope in resource.scope_spans: spans.extend(scope.spans)
server_span = next(span for span in spans if span.kind == 2)
client_span = next(span for span in spans if span.kind == 3)
assert server_span.name == 'GET /echo/{item}'
assert client_span.parent_span_id == server_span.span_id
assert client_span.trace_id == server_span.trace_id
assert propagated[0].split('-')[1] == server_span.trace_id.hex()
assert propagated[0].split('-')[2] == client_span.span_id.hex()
server.shutdown()
"""
    result = subprocess.run(  # noqa: S603 - fixed test code, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "OTEL_SDK_DISABLED": "false", "OTEL_EXPORTER_OTLP_HEADERS": ""},
    )
    assert result.returncode == 0, result.stderr


def test_service_without_httpx_does_not_emit_false_instrumentation_errors():
    script = """
from nautionette import telemetry
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

def unexpected(*args, **kwargs):
    raise AssertionError('Must not instrument a missing optional HTTP client')

telemetry.find_spec = lambda name: None
HTTPXClientInstrumentor.instrument = unexpected
assert telemetry.configure('test-broker')
assert telemetry.temporal_interceptors()
"""
    result = subprocess.run(  # noqa: S603 - fixed test code, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        env={
            **os.environ,
            "OTEL_SDK_DISABLED": "false",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
            "OTEL_EXPORTER_OTLP_HEADERS": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "no installed version was found" not in result.stderr
