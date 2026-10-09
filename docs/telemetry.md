# OpenTelemetry

Nautionette exports standard OTLP/HTTP traces, metrics and correlated logs. There is
**no bundled observability database or vendor-specific backend**. Use an existing
SigNoz, Grafana/Tempo, Honeycomb or other compatible OTLP receiver.

## Enable the optional relay

Set these deployment variables (keep authorization in your deployment secrets):

```dotenv
COMPOSE_PROFILES=observability
OTEL_SDK_DISABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_COLLECTOR_UPSTREAM_ENDPOINT=https://your-otlp-receiver.example
OTEL_COLLECTOR_UPSTREAM_AUTHORIZATION=Bearer <ingest-token>
APP_ENVIRONMENT=staging
APP_VERSION=<release-or-git-sha>
```

Then recreate the stack. The relay accepts HTTP on 4318 (and gRPC on 4317) only on
private Docker networks; it publishes no host ports. Its upstream uses HTTP/protobuf,
appending `/v1/traces`, `/v1/metrics` and `/v1/logs` to the upstream base URL. If your
receiver uses Basic authentication, set the authorization value to `Basic <base64>`.
An empty upstream deliberately points to an unused loopback port, not back to itself.

The default application configuration is disabled. A backend outage does not block
normal requests: Python exporters have bounded background queues and timeouts;
short-lived agents drain a bounded queue for at most three seconds before exiting.

The relay keeps the upstream credential out of dynamically launched agent containers.
You can bypass it by pointing `OTEL_EXPORTER_OTLP_ENDPOINT` directly at an OTLP/HTTP
receiver and supplying standard URL-encoded `OTEL_EXPORTER_OTLP_HEADERS` (for example
`Authorization=Bearer%20...`). Only use an endpoint that also accepts OTLP JSON for
agent spans. A normal OpenTelemetry Collector accepts both JSON and protobuf.
For gRPC-only/vendor-specific transports, keep the relay and change its exporter.

## Coverage and labels

- FastAPI inbound HTTP and HTTPX outbound requests: backend, broker, workflow MCP
  and workers, with W3C context propagation.
- Temporal workflow submissions, executions and activities, using the official
  Temporal tracing interceptor.
- Short-lived agents: `agent.run`, `gen_ai.chat` and `tool.call`, including model,
  input/output token counts, tool name, duration and failure status.
- Agentgateway: upstream request spans with parent context from the agent. Tracing
  is generated only when explicitly enabled; its persisted routing DB is untouched.
- HTTP latency/count metrics and severity/source-only log records from Python services.

Resources include `service.name`, `service.namespace=nautionette`, `service.version`,
`deployment.environment` and `deployment.environment.name`. Keep staging and production
labels distinct even when sending both to a single receiver.

The trusted-gateway fetch wrapper forwards `traceparent` only to the configured
agentgateway origin, never arbitrary external sites. It does not forward baggage.
Tool spans describe a tool invocation, not every internal operation of third-party
MCP servers. Browser UI code does not receive ingestion credentials.

## Privacy and operational limits

Python exports allowlisted attributes and exception **types**, removing exception
messages, status descriptions, request URLs/query strings and arbitrary log text.
Unmatched server request paths cannot become span names. Agent spans never contain
prompts, tool arguments/results or provider responses. The relay applies another
allowlist and strips known payload/header/exception fields before forwarding.
Free-form application logs remain in the normal container logs for manual debugging;
OTLP logs intentionally provide correlation and severity, not their full text.

These are protections against accidental capture, not a sandbox against application
code deliberately writing sensitive data into telemetry. Restrict ingestion credentials,
protect the telemetry UI with authentication, set suitable retention at the receiver,
and do not publish the private relay ports.

Collector queues are bounded and in-memory; queued telemetry can be lost on restart
or a prolonged outage. Exporting does not alter workflow results or implement incident
notifications, automatic rollbacks, or autonomous production changes.

## Coolify

Coolify may keep a saved Compose copy independently of Git. Update that copy when
adding the relay and telemetry environment mappings, preserving existing namespaces,
volumes, networks, domains and secrets. Ensure the observability profile is enabled,
or remove its `profiles` entry only in the always-on deployment copy. A Git push alone
is not sufficient if Coolify still uses an older saved Compose definition.

After deployment, make an authenticated backend request and check that its server
span and downstream spans appear at the receiver with the expected environment and
version. Test the receiver's unauthenticated ingestion rejection separately.

## Tests

```sh
uv run pytest tests/lib/test_telemetry.py tests/lib/test_telemetry_http.py tests/agentgateway/test_telemetry_config.py
node tests/agent/test_telemetry.mjs
```

The SDK test uses a local receiver to verify real traces, metrics, logs, privacy and
HTTP parent/child propagation. Agent checks cover parent IDs, payload-free export,
disabled behavior and trusted-origin-only fetch propagation. CI also builds the relay.
