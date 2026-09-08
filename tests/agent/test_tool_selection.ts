import assert from "node:assert/strict";
import { test } from "node:test";
import { allowedTools } from "../../images/agent-sets/default/extensions/nautionette/tool-selection.ts";

test("tool selection distinguishes every tool, none, and an explicit list", () => {
  assert.equal(allowedTools("null"), null);
  assert.deepEqual([...allowedTools("[]")!], []);
  assert.deepEqual([...allowedTools('["search","read","search"]')!], ["search", "read"]);
  assert.deepEqual([...allowedTools('["comma,in,name"]')!], ["comma,in,name"]);
});

test("JSON takes precedence over legacy CSV and malformed values fail closed", () => {
  assert.deepEqual([...allowedTools("[]", "search,read")!], []);
  assert.equal(allowedTools("null", "search"), null);
  assert.equal(allowedTools(undefined), null);
  assert.deepEqual([...allowedTools(undefined, "read, search")!], ["read", "search"]);
  for (const value of ["", "bad", '"all"', "{}", "false", "42", '["read",null]', '[""]']) {
    assert.throws(() => allowedTools(value), /Invalid MCP tool selection/);
  }
});

for (const selection of [null, [], ["search"], ["unavailable"], ["search", "read"]]) {
  test(`the actual extension registers only ${JSON.stringify(selection)} MCP tools`, async () => {
    const keys = ["NAUTIONETTE_TOOLS_JSON", "NAUTIONETTE_TOOLS", "NAUTIONETTE_MODEL_API", "AGENT_MODEL"];
    const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
    const originalFetch = globalThis.fetch;
    process.env.NAUTIONETTE_TOOLS_JSON = JSON.stringify(selection);
    process.env.NAUTIONETTE_TOOLS = "search,read";
    process.env.NAUTIONETTE_MODEL_API = "openai-completions";
    process.env.AGENT_MODEL = "test/model";
    globalThis.fetch = async () => Response.json({ result: { tools: [{ name: "search" }, { name: "read" }] } });
    try {
      const url = new URL("../../images/agent-sets/default/extensions/nautionette/index.ts", import.meta.url);
      url.searchParams.set("tools", JSON.stringify(selection));
      const { default: extension } = await import(url.href);
      const registered: string[] = [];
      await extension({
        registerProvider() {}, on() {},
        registerTool(tool: { name: string }) { registered.push(tool.name); },
      } as any);
      assert.deepEqual(registered, ["search", "read"].filter((name) => selection === null || selection.includes(name)));
    } finally {
      globalThis.fetch = originalFetch;
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    }
  });
}
