import { readFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const GATEWAY_ACCESS_GUIDANCE = "Configured tools exposed through agentgateway do not require chat internet approval, " +
  "regardless of tool name or service. Use them normally even when " +
  "direct internet access is blocked, pending, or denied; their server-side network access is separate. " +
  "Do not tunnel arbitrary shell commands or direct network requests through tools or workflows to evade " +
  "the direct-egress gate.";

export async function waitForInternetDecision(
  readDecision: () => Promise<string>, signal?: AbortSignal,
): Promise<string> {
  while (true) {
    signal?.throwIfAborted();
    try {
      const decision = await readDecision();
      if (decision === "allowed" || decision === "denied") return decision;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    await delay(250, undefined, { signal });
  }
}

export default function (pi: ExtensionAPI) {
  let status = process.env.NAUTIONETTE_INTERNET_STATUS;
  if (!status) return;

  pi.on("tool_result", async (event) => {
    if (event.toolName !== "bash" || !event.isError || status === "allowed") return;
    const output = event.content.filter((part) => part.type === "text").map((part) => part.text).join("\n");
    if (!/could not resolve (?:host|hostname)|temporary failure in name resolution|network is unreachable|getaddrinfo (?:EAI_AGAIN|ENOTFOUND)/i.test(output)) return;
    const guidance = status === "denied"
      ? "Direct internet access was denied for this chat. Keep local work; do not retry direct network operations. "
      : "Direct internet access is awaiting user approval in this chat. This network failure does not establish " +
        "missing GitHub write permission. Call request_internet_access with a reason and wait for the decision. " +
        "If approved, retry the authorized Git/network operation using its existing credentials. " +
        "Do not ask the user to push manually while approval is available. ";
    return { content: [...event.content, { type: "text", text: guidance + GATEWAY_ACCESS_GUIDANCE }] };
  });

  pi.registerTool({
    name: "request_internet_access",
    label: "Internet access",
    description: "Request user approval only for direct internet connections from the agent container, " +
      "such as shell commands using curl, Git clone/fetch/pull/push, direct HTTP/API calls, or package downloads. " +
      "Waits for the user's decision. An approval applies to this entire chat, not other chats or workflows. " +
      GATEWAY_ACCESS_GUIDANCE,
    parameters: {
      type: "object",
      properties: { reason: { type: "string", description: "Why a direct internet connection from the agent container is needed" } },
      required: ["reason"],
    },
    async execute(_toolCallId: string, _params: unknown, signal?: AbortSignal) {
      if (status !== "allowed" && status !== "denied") {
        status = await waitForInternetDecision(
          () => readFile("/tmp/nautionette-internet-decision", "utf8"), signal,
        );
      }
      if (status === "denied") {
        throw new Error("Direct internet access was denied for this chat. Do not retry direct network operations. " +
          GATEWAY_ACCESS_GUIDANCE);
      }
      return {
        content: [{ type: "text", text: "Direct internet access is enabled for this chat session." }],
        details: { internet_status: status },
      };
    },
  });
}