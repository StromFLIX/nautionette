import assert from "node:assert/strict";
import { test } from "node:test";
import { modelLimits } from "../../images/agent-sets/default/extensions/nautionette/model-limits.ts";

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
    const capabilityKeys = ['NAUTIONETTE_MODEL_API', 'NAUTIONETTE_MODEL_LIMITS', 'NAUTIONETTE_MODEL_REASONING', 'NAUTIONETTE_REASONING_EFFORT'];
    const originalCapabilities = Object.fromEntries(capabilityKeys.map((key) => [key, process.env[key]]));
    for (const key of capabilityKeys) delete process.env[key];
    process.env.AGENT_MODEL = model;
    process.env.NAUTIONETTE_MODEL_LIMITS = JSON.stringify({
      context_window: 1050000, max_input_tokens: 922000, max_output_tokens: 128000,
    });
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
        registerTool() {}, // Native first-party tools are independent of provider selection.
      });
      assert.equal(registration.api, api);
      assert.equal(registration.models[0].id, model);
      assert.equal(registration.models[0].contextWindow, 922000);
      assert.equal(registration.models[0].maxTokens, 128000);
      assert.deepEqual(registration.models[0].input, images === "false" ? ["text"] : ["text", "image"]);
      assert.equal(registration.baseUrl.endsWith("/v1"), true);
      assert.equal(registration.apiKey, "gateway");
    } finally {
      globalThis.fetch = originalFetch;
      for (const [key, value] of Object.entries(originalCapabilities)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
      if (originalModel === undefined) delete process.env.AGENT_MODEL;
      else process.env.AGENT_MODEL = originalModel;
      if (originalImages === undefined) delete process.env.NAUTIONETTE_MODEL_IMAGES;
      else process.env.NAUTIONETTE_MODEL_IMAGES = originalImages;
    }
  });
}

test('unknown limits retain conservative compatibility defaults', () => {
  assert.deepEqual(modelLimits({}), { contextWindow: 200000, maxTokens: 8192 });
  assert.deepEqual(modelLimits({ NAUTIONETTE_MODEL_LIMITS: '{"context_window":null,"max_output_tokens":null}' }),
    { contextWindow: 200000, maxTokens: 8192 });
});

test('smaller advertised ceilings are respected too', () => {
  const limits = modelLimits({ NAUTIONETTE_MODEL_LIMITS: JSON.stringify({
    context_window: 16000, max_input_tokens: 12000, max_output_tokens: 2048,
  }) });
  assert.deepEqual(limits, { contextWindow: 12000, maxTokens: 2048 });
  assert.deepEqual(modelLimits({ NAUTIONETTE_MODEL_LIMITS: '{"context_window":4096}' }),
    { contextWindow: 4096, maxTokens: 4096 });
});

for (const value of [false, true, 0, -1, 1.5, '128000', 2 ** 53, [], {}]) {
  test(`invalid pinned token budget fails explicitly: ${JSON.stringify(value)}`, () => {
    assert.throws(() => modelLimits({ NAUTIONETTE_MODEL_LIMITS: JSON.stringify({ max_output_tokens: value }) }),
      /Invalid pinned model limit/);
  });
}
for (const raw of ['null', '[]', 'true', 'invalid-json']) {
  test(`invalid model limits container fails explicitly: ${raw}`, () => {
    assert.throws(() => modelLimits({ NAUTIONETTE_MODEL_LIMITS: raw }));
  });
}
