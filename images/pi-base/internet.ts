import { readFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

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

  pi.registerTool({
    name: "request_internet_access",
    label: "Internet access",
    description: "Request user approval for direct internet access in this chat. Call before " +
      "fetching websites, cloning remote repositories, or downloading packages. Waits for the " +
      "user's decision. An approval applies to this entire chat, not other chats or workflows. " +
      "A denial must not be bypassed through other tools.",
    parameters: {
      type: "object",
      properties: { reason: { type: "string", description: "Why internet access is needed" } },
      required: ["reason"],
    },
    async execute(_toolCallId: string, _params: unknown, signal?: AbortSignal) {
      if (status !== "allowed" && status !== "denied") {
        status = await waitForInternetDecision(
          () => readFile("/tmp/nautionette-internet-decision", "utf8"), signal,
        );
      }
      if (status === "denied") throw new Error("Internet access was denied for this chat. Do not retry or bypass it.");
      return {
        content: [{ type: "text", text: "Internet access is enabled for this chat session." }],
        details: { internet_status: status },
      };
    },
  });
}