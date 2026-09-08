import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const cases = JSON.parse(readFileSync(new URL('./model_api_cases.json', import.meta.url), 'utf8'));
import { fallbackModelApi, modelApi } from "../../images/agent-sets/default/extensions/nautionette/model-api.ts";

for (const [model, expected] of [
  ["copilot/claude-sonnet-5", "openai-completions"],
  ["copilot/claude-opus-4.6", "openai-completions"],
  ["copilot/gemini-3.5-flash", "openai-completions"],
  ["copilot/gpt-4o", "openai-completions"],
  ["copilot/gpt-4.1", "openai-completions"],
  ["copilot/gpt-5-mini", "openai-responses"],
  ["copilot/gpt-5.3-codex", "openai-responses"],
  ["copilot/gpt-6-astra", "openai-responses"],
  ["copilot/gpt-10", "openai-responses"],
  ["openai/gpt-5", "openai-completions"],
]) {
  test(`catalog fallback for ${model}`, () => assert.equal(fallbackModelApi(model), expected));
}

test("advertised endpoints take precedence over model-name fallbacks", async (t) => {
  let endpoints: unknown = ["/v1/messages", "/chat/completions"];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "http://gateway/_nautionette/integrations/copilot/models");
    assert.ok(options.signal instanceof AbortSignal);
    return Response.json({ data: [
      { id: "different-model", supported_endpoints: ["/responses"] },
      { id: "gpt-5", supported_endpoints: endpoints },
    ] });
  });
  assert.equal(await modelApi("copilot/gpt-5", "http://gateway"), "openai-completions");
  for (const supported of [["/responses"], ["/v1/responses"], ["/chat/completions", "/responses"]]) {
    endpoints = supported;
    assert.equal(await modelApi("copilot/gpt-5", "http://gateway"), "openai-responses");
  }
  endpoints = ["/v1/chat/completions"];
  assert.equal(await modelApi("copilot/gpt-5", "http://gateway"), "openai-completions");
  endpoints = ["/v1/messages"];
  await assert.rejects(modelApi("copilot/gpt-5", "http://gateway"), /No supported OpenAI-compatible API/);
});

for (const payload of [{}, { data: [] }, { data: null }, { data: [{ id: "claude-sonnet-5" }] }]) {
  test(`incomplete catalog uses safe fallback: ${JSON.stringify(payload)}`, async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json(payload));
    assert.equal(await modelApi("copilot/claude-sonnet-5", "http://gateway"), "openai-completions");
  });
}

for (const failure of ["http", "network", "json", "timeout"]) {
  test(`${failure} catalog failure does not break known models or log response bodies`, async (t) => {
    const warnings: string[] = [];
    t.mock.method(console, "error", (message) => warnings.push(message));
    t.mock.method(globalThis, "fetch", async () => {
      if (failure === "http") return new Response("sensitive response body", { status: 503 });
      if (failure === "json") return new Response("sensitive response body");
      if (failure === "timeout") throw new DOMException("sensitive response body", "TimeoutError");
      throw new Error("sensitive response body");
    });
    assert.equal(await modelApi("copilot/claude-sonnet-5", "http://gateway"), "openai-completions");
    assert.equal(await modelApi("copilot/gpt-6-astra", "http://gateway"), "openai-responses");
    assert.equal(warnings.length, 2);
    assert.ok(warnings.every((message) => !message.includes("sensitive")));
  });
}

for (const entry of cases) {
  test(`shared backend/agent API policy: ${JSON.stringify(entry)}`, async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ data: [
      { id: entry.model.slice(8), supported_endpoints: entry.endpoints }
    ] }));
    if (entry.images === false) {
      await assert.rejects(modelApi(entry.model, "http://gateway"), /No supported OpenAI-compatible API/);
    } else {
      assert.equal(await modelApi(entry.model, "http://gateway"), entry.api);
    }
  });
}

for (const api of ['openai-completions', 'openai-responses', 'invalid']) {
  test(`backend selection is pinned, not rediscovered: ${api}`, async (t) => {
    t.mock.method(globalThis, 'fetch', () => { throw new Error('must not fetch'); });
    const previous = process.env.NAUTIONETTE_MODEL_API;
    try {
      process.env.NAUTIONETTE_MODEL_API = api;
      if (api === 'invalid') await assert.rejects(modelApi('copilot/gpt-5', 'http://gateway'), /Invalid selected/);
      else assert.equal(await modelApi('copilot/gpt-5', 'http://gateway'), api);
    } finally {
      if (previous === undefined) delete process.env.NAUTIONETTE_MODEL_API;
      else process.env.NAUTIONETTE_MODEL_API = previous;
    }
  });
}

test("non-Copilot models need no extra discovery request", async (t) => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("must not fetch"); });
  assert.equal(await modelApi("anthropic/claude-sonnet-4", "http://gateway"), "openai-completions");
});
