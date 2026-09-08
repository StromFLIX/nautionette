import assert from "node:assert/strict";
import { test } from "node:test";
import { reasoningConfig, reasoningPayload } from "../../images/agent-sets/default/extensions/nautionette/reasoning.ts";

for (const api of ["openai-completions", "openai-responses"]) {
  test(`${api}: default removes Pi's generated none/disabled overrides without mutating the request`, () => {
    const payload = { model: "reasoner", reasoning: { effort: "none", enabled: false }, reasoning_effort: "none", input: [] };
    const config = reasoningConfig({ NAUTIONETTE_MODEL_REASONING: JSON.stringify({ supported: true }) });
    const result = reasoningPayload(payload, api, config);
    assert.deepEqual(result, { model: "reasoner", input: [] });
    assert.equal(payload.reasoning.effort, "none");
    assert.equal(config.reasoning, true);
    assert.equal(config.level, "off");
  });
}

test("unadvertised choices fail instead of silently falling back", () => {
  for (const effort of ["max", "typo", "none"]) {
    assert.throws(() => reasoningConfig({
      NAUTIONETTE_MODEL_REASONING: JSON.stringify({ supported: true, efforts: ["low", "high"] }),
      NAUTIONETTE_REASONING_EFFORT: effort,
    }), /not supported/);
  }
  assert.equal(reasoningConfig({}).reasoning, false);
});

test("the Pi level map is model-specific and preserves max", () => {
  const config = reasoningConfig({
    NAUTIONETTE_MODEL_REASONING: JSON.stringify({ efforts: ["low", "high", "max"] }),
    NAUTIONETTE_REASONING_EFFORT: "max",
  });
  assert.equal(config.reasoning, true);
  assert.equal(config.level, "max");
  assert.deepEqual(config.thinkingLevelMap, {
    off: "none", minimal: null, low: "low", medium: null, high: "high", xhigh: null, max: "max",
  });
});
