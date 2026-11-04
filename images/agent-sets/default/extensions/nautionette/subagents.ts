/** Local wrappers: the turn capability is never model input or tool history. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { publicationRequestId } from "./outputs.ts";

type Rpc = (method: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<Record<string, any>>;
export const SUBAGENT_TOOLS = new Set([
  "list_subagents", "call_subagent", "wait_for_subagent",
  "backend_list_callable_agents", "backend_call_subagent", "backend_wait_for_subagent",
]);

export function registerSubagentTools(pi: ExtensionAPI, rpc: Rpc, grant: { grant: string } | null, allowed: Set<string> | null = null) {
  if (!grant) return;
  async function transport(name: string, body: Record<string, unknown>, signal?: AbortSignal) {
    signal?.throwIfAborted();
    const result = await rpc("tools/call", { name, arguments: { body: { ...body, ...grant } } }, signal);
    const text = (result.content || []).filter((part: any) => part.type === "text").map((part: any) => part.text).join("\n");
    if (result.isError) throw new Error(text || "Subagent request failed");
    return JSON.parse(text);
  }
  const response = (value: any) => ({ content: [{ type: "text" as const, text: JSON.stringify(value) }], details: value });
  if (allowed === null || allowed.has("list_subagents")) pi.registerTool({
    name: "list_subagents", label: "Allowed subagents",
    description: "List saved agents this turn may call and global tree limits. Allowlist is empty by default. Calls use the target's own model, tools, projects and instructions.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    async execute(_id, _params, signal) { return response(await transport("backend_list_callable_agents", {}, signal)); },
  });
  if (allowed === null || allowed.has("call_subagent")) pi.registerTool({
    name: "call_subagent", label: "Call agent",
    description: "Start an allowed agent asynchronously. Omit chat_id for a new conversation, or continue a chat previously opened by this caller chat with that agent. Returns a durable call ID and chat ID. Use wait_for_subagent for results. Depth, total children per turn and whole-tree parallelism are enforced server-side. At capacity, wait for an existing child before retrying; nested parents retain slots. Messages are attributed to the sending agent, not the human.",
    parameters: { type: "object", required: ["agent_id", "text"], additionalProperties: false, properties: {
      agent_id: { type: "string", minLength: 1, maxLength: 64 },
      text: { type: "string", minLength: 1, maxLength: 32000 },
      chat_id: { type: ["string", "null"] },
    } },
    async execute(id, params: any, signal) {
      return response(await transport("backend_call_subagent", { ...params, request_id: publicationRequestId(id) }, signal));
    },
  });
  if (allowed === null || allowed.has("wait_for_subagent")) pi.registerTool({
    name: "wait_for_subagent", label: "Wait for agent",
    description: "Wait for this turn's child call to finish, or poll with timeout_seconds=0. Defaults to 300 seconds; a timeout returns current state without cancelling the child. Repeat if still running. Returns completion, bounded answer text, and errors; a started call is not a successful result.",
    parameters: { type: "object", required: ["call_id"], additionalProperties: false, properties: {
      call_id: { type: "string", minLength: 1, maxLength: 64 },
      timeout_seconds: { type: "integer", minimum: 0, maximum: 300 },
    } },
    async execute(_id, params: any, signal) {
      const deadline = Date.now() + (params.timeout_seconds ?? 300) * 1000;
      let result: any;
      do {
        result = await transport("backend_wait_for_subagent", {
          call_id: params.call_id, timeout_seconds: Math.min(20, Math.max(0, Math.ceil((deadline - Date.now()) / 1000))),
        }, signal);
      } while (["running", "queued"].includes(result.state) && Date.now() < deadline);
      return response(result);
    },
  });
}
