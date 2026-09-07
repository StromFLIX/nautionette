import assert from "node:assert/strict";
import { test } from "node:test";
import extension, { waitForInternetDecision } from "../../images/pi-base/internet.ts";

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
        await assert.rejects(tool.execute("call", {}), /denied/);
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
      assert.match(tool.description, /Git clone\/fetch\/pull\/push/);
      const result = await handler(event);
      if (status === "allowed") {
        assert.equal(result, undefined);
        continue;
      }
      assert.equal(result.content[0], event.content[0]);
      assert.equal(result.isError, undefined);
      assert.equal(event.content.length, 1);
      const guidance = result.content[1].text;
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