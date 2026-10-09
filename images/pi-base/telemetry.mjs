/** Payload-free OTLP/HTTP for short-lived agents. No additional SDK dependencies. */
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const id = bytes => randomBytes(bytes).toString("hex");
const now = () => (BigInt(Date.now()) * 1000000n).toString();
const attribute = (key, value) => ({ key, value: typeof value === "number"
  ? { intValue: String(value) } : { stringValue: String(value).slice(0, 256) } });

export function createTelemetry(job = {}, env = process.env) {
  const configured = Boolean(env.OTEL_EXPORTER_OTLP_ENDPOINT) && String(env.OTEL_SDK_DISABLED ?? "true").toLowerCase() !== "true";
  const candidate = /^00-([a-f0-9]{32})-([a-f0-9]{16})-([a-f0-9]{2})$/.exec(env.TRACEPARENT || "");
  const parent = candidate && !/^0+$/.test(candidate[1]) && !/^0+$/.test(candidate[2]) ? candidate : null;
  const flags = parent?.[3] || "01";
  const enabled = configured && Boolean(parseInt(flags, 16) & 1);
  const traceId = parent?.[1] || id(16);
  const rootId = id(8);
  const traceparent = `00-${traceId}-${rootId}-${flags}`;
  const queue = [];
  const active = new Map();
  let finished = false;
  function start(name, attrs = {}, spanId = id(8), parentSpanId = rootId) {
    if (!enabled) return null;
    const span = { traceId, spanId, parentSpanId, name, kind: name === "gen_ai.chat" ? 3 : 1,
      flags: parseInt(flags, 16),
      startTimeUnixNano: now(), attributes: Object.entries(attrs).map(([k,v]) => attribute(k,v)) };
    active.set(spanId, span);
    return spanId;
  }
  function end(spanId, error = false, attrs = {}) {
    const span = active.get(spanId);
    if (!span) return;
    active.delete(spanId);
    span.endTimeUnixNano = now();
    span.status = { code: error ? 2 : 1 };
    span.attributes.push(...Object.entries(attrs).map(([k,v]) => attribute(k,v)));
    queue.push(span);
    if (queue.length > 256) queue.shift();
  }
  const root = start("agent.run", {
    "nautionette.chat_id": job.chat_id || "", "nautionette.turn_id": job.turn_id || "",
    "nautionette.agent_set": job.agent_set || "default", "gen_ai.request.model": job.model || "default",
  }, rootId, parent?.[2] || "");
  async function flush() {
    if (!enabled || !queue.length) return;
    const spans = queue.splice(0);
    const body = { resourceSpans: [{ resource: { attributes: [
      attribute("service.name", "nautionette-agent"), attribute("service.namespace", "nautionette"),
      attribute("service.version", env.APP_VERSION || "dev"),
      attribute("deployment.environment", env.APP_ENVIRONMENT || "production"),
      attribute("deployment.environment.name", env.APP_ENVIRONMENT || "production"),
    ] }, scopeSpans: [{ scope: { name: "nautionette-agent" }, spans }] }] };
    try {
      await fetch(`${env.OTEL_EXPORTER_OTLP_ENDPOINT.replace(/\/$/, "")}/v1/traces`, {
        method: "POST", headers: { "Content-Type": "application/json",
          ...Object.fromEntries((env.OTEL_EXPORTER_OTLP_HEADERS || "").split(",").filter(Boolean).map(item => {
            const at = item.indexOf("=");
            return [item.slice(0, at).trim(), decodeURIComponent(item.slice(at + 1).trim())];
          })),
        }, body: JSON.stringify(body),
        signal: AbortSignal.timeout(2000),
      });
    } catch { /* Telemetry must never interrupt agent work or its stdout protocol. */ }
  }
  const timer = enabled ? setInterval(() => void flush(), 5000) : null;
  timer?.unref();
  return { start, end, traceparent: configured ? traceparent : "", flush,
    fetchImport: fileURLToPath(new URL("./trace-fetch.mjs", import.meta.url)),
    finish(error = false) {
      if (finished) return;
      finished = true;
      clearInterval(timer);
      for (const spanId of [...active.keys()]) end(spanId, spanId === root ? error : true);
    },
  };
}
