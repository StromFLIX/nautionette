import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const extensionPath = require.resolve("../../images/agent-sets/default/extensions/nautionette/index.ts");

for (const [model, api] of [
  ["copilot/gpt-4o", "openai-responses"],
  ["copilot/gpt-5", "openai-responses"],
  ["openai/gpt-4o-mini", "openai-completions"],
  ["anthropic/claude-sonnet-4", "openai-completions"],
]) {
  test(`${model} uses ${api} through the gateway`, async () => {
    const originalModel = process.env.AGENT_MODEL;
    const originalFetch = globalThis.fetch;
    process.env.AGENT_MODEL = model;
    globalThis.fetch = async () => new Response(JSON.stringify({ result: { tools: [] } }));
    try {
      delete require.cache[extensionPath];
      const { default: extension } = require(extensionPath);
      let registration;
      await extension({
        registerProvider(name, config) {
          assert.equal(name, "nautionette");
          registration = config;
        },
        on() {},
      });
      assert.equal(registration.api, api);
      assert.equal(registration.models[0].id, model);
      assert.equal(registration.baseUrl.endsWith("/v1"), true);
      assert.equal(registration.apiKey, "gateway");
    } finally {
      globalThis.fetch = originalFetch;
      if (originalModel === undefined) delete process.env.AGENT_MODEL;
      else process.env.AGENT_MODEL = originalModel;
    }
  });
}