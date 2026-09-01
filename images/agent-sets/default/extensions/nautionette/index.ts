/**
 * The default agent set.
 *
 * Two jobs, both of them about openness:
 *  1. point Pi at agentgateway instead of a provider, so no model key ever
 *     enters this container;
 *  2. bridge whatever MCP tools the gateway federates into Pi tools, so adding
 *     a tool to the gateway is enough to give the agent a new capability.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const GATEWAY = (process.env.AGENTGATEWAY_URL ?? "http://agentgateway:4000").replace(/\/$/, "");
const MODEL = process.env.AGENT_MODEL ?? "openai/gpt-4o-mini";
const MCP_URL = process.env.MCP_URL ?? `${GATEWAY}/mcp`;
const PROVIDER = "nautionette";

type JsonRpcResult = Record<string, any>;

let messageId = 0;
let sessionId: string | undefined;

async function rpc(method: string, params: Record<string, unknown> = {}): Promise<JsonRpcResult> {
  const isNotification = method.startsWith("notifications/");
  const body: Record<string, unknown> = { jsonrpc: "2.0", method, params };
  if (!isNotification) body.id = ++messageId;

  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    "mcp-protocol-version": "2025-06-18",
  };
  if (sessionId) headers["mcp-session-id"] = sessionId;

  const response = await fetch(MCP_URL, { method: "POST", headers, body: JSON.stringify(body) });
  const returned = response.headers.get("mcp-session-id");
  if (returned) sessionId = returned;
  if (isNotification) return {};
  if (!response.ok) throw new Error(`${method} -> HTTP ${response.status}`);

  const raw = await response.text();
  const payload = raw.includes("event:") || raw.startsWith("data:")
    ? raw
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("")
    : raw;
  const parsed = JSON.parse(payload);
  if (parsed.error) throw new Error(`${method} -> ${parsed.error.message ?? "mcp error"}`);
  return parsed.result ?? {};
}

function renderToolResult(result: JsonRpcResult): string {
  const content = Array.isArray(result?.content) ? result.content : [];
  const text = content
    .filter((part: any) => part?.type === "text")
    .map((part: any) => part.text)
    .join("\n");
  return text || JSON.stringify(result ?? {}, null, 2);
}

export default async function (pi: ExtensionAPI) {
  pi.registerProvider(PROVIDER, {
    name: "Nautionette gateway",
    baseUrl: `${GATEWAY}/v1`,
    // agentgateway holds the real provider key; this value only has to exist.
    apiKey: "gateway",
    api: "openai-completions",
    authHeader: true,
    models: [
      {
        id: MODEL,
        name: `${MODEL} (via agentgateway)`,
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 200000,
        maxTokens: 8192,
      },
    ],
  });

  pi.on("session_start", async (_event, ctx) => {
    const model = ctx.modelRegistry.find(PROVIDER, MODEL);
    if (model) await pi.setModel(model);
  });

  // Tools are discovered, not hard-coded: federate a new MCP server behind the
  // gateway and this agent set picks it up on its next run.
  try {
    await rpc("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "nautionette-agent-set", version: "1.0.0" },
    });
    await rpc("notifications/initialized");
    const listed = await rpc("tools/list");
    const tools: any[] = listed.tools ?? [];

    for (const tool of tools) {
      pi.registerTool({
        name: tool.name,
        label: tool.title ?? tool.name,
        description: tool.description ?? `MCP tool ${tool.name}`,
        promptSnippet: tool.description?.split("\n")[0],
        parameters: tool.inputSchema ?? { type: "object", properties: {} },
        async execute(_toolCallId: string, params: unknown) {
          const result = await rpc("tools/call", { name: tool.name, arguments: params ?? {} });
          const text = renderToolResult(result);
          if (result?.isError) throw new Error(text);
          return { content: [{ type: "text", text }], details: result ?? {} };
        },
      });
    }
    console.error(`[nautionette] bridged ${tools.length} MCP tool(s) from ${MCP_URL}`);
  } catch (error) {
    // A missing gateway must not cost the agent its built-in tools.
    console.error(`[nautionette] MCP bridge unavailable: ${(error as Error).message}`);
  }
}
