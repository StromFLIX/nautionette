import assert from "node:assert/strict";
import { test } from "node:test";

for (const [model, api, images] of [
  ["copilot/gpt-4o", "openai-completions", undefined],
  ["copilot/claude-sonnet-5", "openai-completions", "true"],
  ["copilot/gemini-3.5-flash", "openai-completions", "true"],
  ["copilot/gpt-5", "openai-responses", "true"],
  ["copilot/gpt-6-astra", "openai-responses", "true"],
  ["copilot/future-responses-model", "openai-responses", "true"],
  ["openai/gpt-4o-mini", "openai-completions", "true"],
  ["anthropic/claude-sonnet-4", "openai-completions", undefined],
  ["text-only/model", "openai-completions", "false"],
]) {
  test(`${model} uses ${api} through the gateway with image support ${images}`, async () => {
    const originalModel = process.env.AGENT_MODEL;
    const originalImages = process.env.NAUTIONETTE_MODEL_IMAGES;
    const originalFetch = globalThis.fetch;
    process.env.AGENT_MODEL = model;
    if (images === undefined) delete process.env.NAUTIONETTE_MODEL_IMAGES;
    else process.env.NAUTIONETTE_MODEL_IMAGES = images;
    globalThis.fetch = async (url) => {
      if (String(url).endsWith("/models")) {
        return Response.json({ data: [{
          id: model.slice("copilot/".length),
          supported_endpoints: api === "openai-responses" ? ["/responses"] : ["/chat/completions"],
        }] });
      }
      return Response.json({ result: { tools: [] } });
    };
    try {
      // Give each env configuration a fresh ESM module, including with Node's native TS loader.
      const url = new URL("../../images/agent-sets/default/extensions/nautionette/index.ts", import.meta.url);
      url.searchParams.set("model", model);
      const { default: extension } = await import(url.href);
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
      assert.deepEqual(registration.models[0].input, images === "false" ? ["text"] : ["text", "image"]);
      assert.equal(registration.baseUrl.endsWith("/v1"), true);
      assert.equal(registration.apiKey, "gateway");
    } finally {
      globalThis.fetch = originalFetch;
      if (originalModel === undefined) delete process.env.AGENT_MODEL;
      else process.env.AGENT_MODEL = originalModel;
      if (originalImages === undefined) delete process.env.NAUTIONETTE_MODEL_IMAGES;
      else process.env.NAUTIONETTE_MODEL_IMAGES = originalImages;
    }
  });
}
