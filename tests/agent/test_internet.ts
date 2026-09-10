import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import extension, { waitForInternetDecision } from "../../images/pi-base/internet.ts";

function assertGatewayAccessGuidance(text: string) {
  const normalized = text.replace(/\s+/g, " ");
  assert.match(normalized, /tools exposed through agentgateway do not require chat internet approval/);
  assert.match(normalized, /regardless of tool name or service/);
  assert.match(normalized, /Use them normally even when direct internet access is blocked, pending, or denied/);
  assert.match(normalized, /Do not tunnel arbitrary shell commands or direct network requests/);
}

test("default agent instructions distinguish direct egress from configured gateway tools", () => {
  const instructions = readFileSync(new URL("../../images/agent-sets/default/AGENTS.md", import.meta.url), "utf8");
  assertGatewayAccessGuidance(instructions);
  assert.match(instructions, /approval controls only direct connections from the agent container/);
  assert.match(instructions, /Git clone\/fetch\/pull\/push, direct HTTP\/API/);
  assert.doesNotMatch(instructions, /Do not bypass a pending or denied request using MCP/);
});

test("the approval waiter ignores missing and invalid decisions", async () => {
  let attempts = 0;
  const decision = await waitForInternetDecision(async () => {
    attempts++;
    if (attempts === 1) throw Object.assign(new Error("missing"), { code: "ENOENT" });
    return attempts === 2 ? "true" : "allowed";
  });
  assert.equal(decision, "allowed");
  assert.equal(attempts, 3);
});

test("the approval waiter stops when its turn is cancelled", async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(waitForInternetDecision(async () => "allowed", controller.signal), { name: "AbortError" });
});

test("the approval waiter fails closed on filesystem errors", async () => {
  await assert.rejects(waitForInternetDecision(async () => { throw new Error("unreadable"); }), /unreadable/);
});

test("a session decision is reused and workflow agents do not get a chat approval tool", async () => {
  const original = process.env.NAUTIONETTE_INTERNET_STATUS;
  try {
    for (const status of ["", "allowed", "denied"]) {
      process.env.NAUTIONETTE_INTERNET_STATUS = status;
      let tool;
      extension({ registerTool(value) { tool = value; }, on() {} });
      if (!status) {
        assert.equal(tool, undefined);
      } else if (status === "allowed") {
        for (let attempt = 0; attempt < 2; attempt++) {
          assert.equal((await tool.execute("call", {})).details.internet_status, "allowed");
        }
      } else {
        await assert.rejects(tool.execute("call", {}), (error: Error) => {
          assert.match(error.message, /Direct internet access was denied/);
          assertGatewayAccessGuidance(error.message);
          return true;
        });
      }
    }
  } finally {
    if (original === undefined) delete process.env.NAUTIONETTE_INTERNET_STATUS;
    else process.env.NAUTIONETTE_INTERNET_STATUS = original;
  }
});

test("failed Git DNS lookup points to approval without masking or retrying the command", async () => {
  const original = process.env.NAUTIONETTE_INTERNET_STATUS;
  const event = {
    toolName: "bash", isError: true,
    content: [{ type: "text", text: "fatal: unable to access 'https://github.com/StromFLIX/nautionette.git/': Could not resolve host: github.com" }],
  };
  try {
    for (const status of ["blocked", "pending", "denied", "allowed", ""]) {
      process.env.NAUTIONETTE_INTERNET_STATUS = status;
      let handler;
      let tool;
      extension({
        on(name, callback) { assert.equal(name, "tool_result"); handler = callback; },
        registerTool(value) { tool = value; },
      });
      if (!status) {
        assert.equal(handler, undefined);
        continue;
      }
      assert.match(tool.description, /only for direct internet connections from the agent container/);
      assert.match(tool.description, /Git clone\/fetch\/pull\/push/);
      assertGatewayAccessGuidance(tool.description);
      // A gateway tool's upstream DNS failure is not a request for container egress.
      for (const toolName of ["test_tool_a", "custom_action_b", "another_tool_c"]) {
        assert.equal(await handler({ ...event, toolName }), undefined);
      }
      const result = await handler(event);
      if (status === "allowed") {
        assert.equal(result, undefined);
        continue;
      }
      assert.equal(result.content[0], event.content[0]);
      assert.equal(result.isError, undefined);
      assert.equal(event.content.length, 1);
      const guidance = result.content[1].text;
      assertGatewayAccessGuidance(guidance);
      if (status === "denied") {
        assert.match(guidance, /denied/);
        assert.doesNotMatch(guidance, /Call request_internet_access/);
      } else {
        assert.match(guidance, /Call request_internet_access/);
        assert.match(guidance, /does not establish missing GitHub write permission/);
      }
      assert.equal(await handler({ ...event, isError: false }), undefined);
      assert.equal(await handler({ ...event, toolName: "mcp_github" }), undefined);
      assert.equal(await handler({ ...event, content: [{ type: "text", text: "remote: Write access to repository not granted. HTTP 403" }] }), undefined);
    }
  } finally {
    if (original === undefined) delete process.env.NAUTIONETTE_INTERNET_STATUS;
    else process.env.NAUTIONETTE_INTERNET_STATUS = original;
  }
});