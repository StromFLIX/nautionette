/** Propagate ONLY to the trusted gateway, never external websites or Git. */
const original = globalThis.fetch;
const gateway = process.env.AGENTGATEWAY_URL || "http://agentgateway:4000";
let origin;
try { origin = new URL(gateway).origin; } catch { /* invalid config: leave fetch alone */ }
const parent = process.env.TRACEPARENT;
const context = /^00-([a-f0-9]{32})-([a-f0-9]{16})-[a-f0-9]{2}$/.exec(parent || "");
if (origin && context && !/^0+$/.test(context[1]) && !/^0+$/.test(context[2])) {
  globalThis.fetch = (input, init = {}) => {
    try {
      const url = input instanceof Request ? input.url : String(input);
      if (new URL(url).origin === origin) {
        const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
        headers.set("traceparent", parent);
        init = { ...init, headers };
      }
    } catch { /* Let original fetch report malformed inputs. */ }
    return original(input, init);
  };
}
