import assert from "node:assert/strict";
import { test } from "node:test";
import internetExtension from "../../images/pi-base/internet.ts";

for (const status of ["blocked", "pending", "denied", "allowed"]) {
  test(`configured gateway tools execute while direct internet is ${status}`, async () => {
    const environment = {
      NAUTIONETTE_INTERNET_STATUS: status,
      NAUTIONETTE_TOOLS_JSON: "null",
      NAUTIONETTE_MODEL_API: "openai-completions",
      AGENT_MODEL: "test/model",
      AGENTGATEWAY_URL: "http://agentgateway.test:4000",
      MCP_URL: "http://agentgateway.test:4000/mcp",
    };
    const previous = Object.fromEntries(Object.keys(environment).map((key) => [key, process.env[key]]));
    const originalFetch = globalThis.fetch;
    const calls: unknown[] = [];
    const tools = new Map();
    Object.assign(process.env, environment);
    globalThis.fetch = async (url, options) => {
      assert.equal(url, environment.MCP_URL);
      const request = JSON.parse(options!.body as string);
      if (request.method === "tools/list") {
        return Response.json({ result: { tools: [{ name: "test_tool_a" }, { name: "custom_action_b" }] } });
      }
      if (request.method === "tools/call") {
        calls.push(request.params);
        return Response.json({ result: { content: [{ type: "text", text: "Tool result" }] } });
      }
      return Response.json({ result: {} });
    };
    try {
      const pi = {
        registerProvider() {}, on() {},
        registerTool(tool) {
          tools.set(tool.name, tool);
          if (tool.name === "request_internet_access") {
            tool.execute = () => assert.fail("Gateway calls must not request direct internet access");
          }
        },
      };
      internetExtension(pi as any);
      const url = new URL("../../images/agent-sets/default/extensions/nautionette/index.ts", import.meta.url);
      url.searchParams.set("internet", status);
      const { default: gatewayExtension } = await import(url.href);
      await gatewayExtension(pi);
      for (const name of ["test_tool_a", "custom_action_b"]) {
        const result = await tools.get(name).execute("call", { input: "test value" });
        assert.deepEqual(result.content, [{ type: "text", text: "Tool result" }]);
      }
      assert.deepEqual(calls, [
        { name: "test_tool_a", arguments: { input: "test value" } },
        { name: "custom_action_b", arguments: { input: "test value" } },
      ]);
    } finally {
      globalThis.fetch = originalFetch;
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    }
  });
}
