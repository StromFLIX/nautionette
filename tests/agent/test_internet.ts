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
      extension({ registerTool(value) { tool = value; } });
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