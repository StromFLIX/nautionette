/** Gate before dispatch, never retry a tool after an authentication failure. */
import { createHash } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
export const AUTH_TRANSPORT_TOOLS = new Set(["backend_mcp_oauth_gate", "backend_mcp_oauth_tools"]);
type Rpc = (method: string, params: Record<string, any>, signal?: AbortSignal) => Promise<Record<string, any>>;

export async function authControl(rpc: Rpc, name: string, body: Record<string, any>, signal?: AbortSignal) {
  const result = await rpc("tools/call", { name, arguments: { body } }, signal);
  const text = (result.content || []).filter((item: any) => item.type === "text").map((item: any) => item.text).join("\n");
  // Private capabilities must never appear in tool errors/model context.
  if (result.isError) throw new Error("MCP authentication gate unavailable; tool was not called.");
  try { return JSON.parse(text); }
  catch { throw new Error("Invalid MCP authentication gate response; tool was not called."); }
}

export async function waitForMcpAuth(rpc: Rpc, grant: { grant?: string }, tool: string, callId: string, signal?: AbortSignal) {
  const bounded = signal ? AbortSignal.any([signal, AbortSignal.timeout(185000)]) : AbortSignal.timeout(185000);
  // Provider IDs may include long opaque signatures. The gate needs only a
  // stable turn-local identity, not the original ID (bounded to 256 by its API).
  const gateCallId = createHash("sha256").update(callId).digest("hex");
  let waiting = false;
  try {
    for (;;) {
      bounded.throwIfAborted();
      const result = await authControl(rpc, "backend_mcp_oauth_gate", { ...grant, tool, tool_call_id: gateCallId }, bounded);
      waiting = result.status === "waiting";
      if (result.status === "ready") return;
      if (!waiting) throw new Error("MCP sign-in skipped, snoozed or timed out; tool was not called.");
      await delay(1000, undefined, { signal: bounded });
    }
  } catch (error) {
    // Best-effort cleanup of this gate only; never retry the actual operation.
    if (waiting || bounded.aborted) await authControl(rpc, "backend_mcp_oauth_gate", { ...grant, tool, tool_call_id: gateCallId, cancel: true },
      AbortSignal.timeout(5000)).catch(() => {});
    throw error;
  }
}
