import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createTelemetry } from "../../images/pi-base/telemetry.mjs";

const received = [];
const server = createServer((req, res) => {
  let body = "";
  req.on("data", data => body += data);
  req.on("end", () => { received.push(JSON.parse(body)); res.end("{}"); });
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
try {
  const traceId = "a".repeat(32);
  const parentId = "b".repeat(16);
  const telemetry = createTelemetry({ chat_id: "chat", turn_id: "turn", prompt: "secret prompt", model: "test" }, {
    TRACEPARENT: `00-${traceId}-${parentId}-01`,
    OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${server.address().port}`,
    OTEL_SDK_DISABLED: "false", APP_ENVIRONMENT: "staging", APP_VERSION: "sha",
  });
  const span = telemetry.start("tool.call", { "gen_ai.tool.name": "bash" });
  telemetry.end(span, true);
  telemetry.finish(false);
  await telemetry.flush();
  assert.equal(received.length, 1);
  const spans = received[0].resourceSpans[0].scopeSpans[0].spans;
  const root = spans.find(span => span.name === "agent.run");
  const tool = spans.find(span => span.name === "tool.call");
  assert.equal(root.traceId, traceId);
  assert.equal(root.parentSpanId, parentId);
  assert.equal(tool.parentSpanId, root.spanId);
  assert.equal(tool.status.code, 2);
  assert.ok(!JSON.stringify(received).includes("secret prompt"));
  assert.ok(JSON.stringify(received).includes("staging"));
  telemetry.finish(true);
  await telemetry.flush();
  assert.equal(received.length, 1);
  const disabled = createTelemetry({}, { OTEL_SDK_DISABLED: "true", OTEL_EXPORTER_OTLP_ENDPOINT: "http://unused" });
  assert.equal(disabled.traceparent, "");
  disabled.start("ignored");
  disabled.finish();
  await disabled.flush();

  const unsampled = createTelemetry({}, {
    OTEL_SDK_DISABLED: "false", OTEL_EXPORTER_OTLP_ENDPOINT: "http://unused",
    TRACEPARENT: `00-${traceId}-${parentId}-00`,
  });
  assert.equal(unsampled.start("ignored"), null);
  assert.ok(unsampled.traceparent.endsWith("-00"));
  unsampled.finish();
  await unsampled.flush();

  // Fetch propagation only reaches the trusted gateway, never arbitrary sites.
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (input, init) => { calls.push({ input, init }); return new Response("ok"); };
  process.env.TRACEPARENT = `00-${traceId}-${parentId}-01`;
  process.env.AGENTGATEWAY_URL = "http://gateway.test:4000";
  await import("../../images/pi-base/trace-fetch.mjs");
  await fetch("http://gateway.test:4000/v1/chat/completions", { headers: { Accept: "application/json" } });
  await fetch("https://external.test", {});
  assert.equal(calls[0].init.headers.get("traceparent"), process.env.TRACEPARENT);
  assert.equal(calls[0].init.headers.get("Accept"), "application/json");
  assert.equal(calls[1].init.headers, undefined);
  globalThis.fetch = original;
  delete process.env.TRACEPARENT;
  delete process.env.AGENTGATEWAY_URL;
} finally {
  await new Promise(resolve => server.close(resolve));
}
console.log("Agent telemetry privacy and propagation checks passed");
