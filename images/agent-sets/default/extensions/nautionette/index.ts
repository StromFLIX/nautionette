/**
 * The default agent set.
 *
 * Two jobs, both of them about openness:
 *  1. point Pi at agentgateway instead of a provider, so no model key ever
 *     enters this container;
 *  2. bridge whatever MCP tools the gateway federates into Pi tools, so adding
 *     a tool to the gateway is enough to give the agent a new capability.
 */
import { writeFileSync } from "node:fs";
import brand from "./brand.json" with { type: "json" };
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { modelApi } from "./model-api.ts";
import { createMcpTransport } from "./mcp-transport.ts";
import { AUTH_TRANSPORT_TOOLS, authControl, waitForMcpAuth } from "./mcp-auth.ts";
import { modelLimits } from "./model-limits.ts";
import { reasoningConfig, reasoningPayload } from "./reasoning.ts";
import { allowedTools } from "./tool-selection.ts";
import { registerMcpTools } from "./deferred-tools.ts";
import { isOutputTransportTool, loadOutputGrant, registerOutputTools } from "./outputs.ts";
import { CRAFT_TOOL_NAMES, registerCraftTools } from "./artcraft.ts";
import { SUBAGENT_TOOLS, registerSubagentTools } from "./subagents.ts";

const GATEWAY = (process.env.AGENTGATEWAY_URL ?? "http://agentgateway:4000").replace(/\/$/, "");
const MODEL = process.env.AGENT_MODEL ?? "openai/gpt-4o-mini";
const MCP_URL = process.env.MCP_URL ?? `${GATEWAY}/mcp`;
const PROVIDER = "nautionette";
// A null selection means all tools; an empty set deliberately bridges none.
const ALLOWED = allowedTools(process.env.NAUTIONETTE_TOOLS_JSON, process.env.NAUTIONETTE_TOOLS);

type JsonRpcResult = Record<string, any>;

const MODE = process.env.NAUTIONETTE_MCP_SCHEMA_MODE || "deferred";
if (!["direct", "deferred"].includes(MODE)) throw new Error("Invalid MCP schema mode");
const TARGETS = allowedTools(process.env.NAUTIONETTE_MCP_TARGETS_JSON);
const transport = createMcpTransport(MCP_URL, fetch, TARGETS === null ? null : [...TARGETS]);
// First-party control must not retain an external session snapshot from before
// login. New remote targets cannot invalidate this backend/workflows-only scope.
const control = createMcpTransport(MCP_URL, fetch, []);
const rpc = transport.rpc;

function renderToolResult(result: JsonRpcResult): string {
  const content = Array.isArray(result?.content) ? result.content : [];
  const text = content
    .filter((part: any) => part?.type === "text")
    .map((part: any) => part.text)
    .join("\n");
  return text || JSON.stringify(result ?? {}, null, 2);
}

export default async function (pi: ExtensionAPI) {
  pi.on("session_shutdown", () => Promise.all([transport.close(), control.close()]));
  const api = await modelApi(MODEL, GATEWAY);
  const reasoning = reasoningConfig();
  console.error(`[nautionette] model ${MODEL} uses ${api} through agentgateway`);
  pi.registerProvider(PROVIDER, {
    name: `${brand.name} gateway`,
    baseUrl: `${GATEWAY}/v1`,
    // agentgateway holds the real provider key; this value only has to exist.
    apiKey: "gateway",
    api,
    authHeader: true,
    models: [
      {
        id: MODEL,
        name: `${MODEL} (via agentgateway)`,
        reasoning: reasoning.reasoning,
        thinkingLevelMap: reasoning.thinkingLevelMap,
        compat: {
          supportsDeveloperRole: false,
          supportsReasoningEffort: reasoning.reasoning,
          thinkingFormat: reasoning.format,
        },
        // Unknown catalogs must not cause Pi to silently strip image blocks.
        input: process.env.NAUTIONETTE_MODEL_IMAGES === "false" ? ["text"] : ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        ...modelLimits(),
      },
    ],
  });

  pi.on("session_start", async (_event, ctx) => {
    const model = ctx.modelRegistry.find(PROVIDER, MODEL);
    if (model) await pi.setModel(model);
    pi.setThinkingLevel(reasoning.level);
  });

  pi.on("before_provider_request", (event, ctx) => {
    if (ctx.model?.provider !== PROVIDER || ctx.model.id !== MODEL) return;
    return reasoningPayload(event.payload as Record<string, any>, api, reasoning);
  });

  // Local first-party tools hold a private, turn-scoped capability. The raw
  // transports must never be declared to the model or gated by MCP selection.
  const grant = loadOutputGrant();
  registerOutputTools(pi, control.rpc, grant);
  registerSubagentTools(pi, control.rpc, grant, ALLOWED);
  registerCraftTools(pi);

  // Tools are discovered, not hard-coded: federate a new MCP server behind the
  // gateway and this agent set picks it up on its next run.
  try {
    await transport.connect();
    const listed = await rpc("tools/list");
    const all: any[] = listed.tools ?? [];
    const authGates = all.some(tool => tool.name === "backend_mcp_oauth_gate");
    const oauthTools = new Set<string>();
    if (authGates) {
      const saved = await authControl(control.rpc, "backend_mcp_oauth_tools", { targets: TARGETS === null ? null : [...TARGETS] });
      for (const name of saved.oauth_tools || []) oauthTools.add(name);
      const names = new Set(all.map(tool => tool.name));
      for (const tool of saved.tools || []) {
        if (!names.has(tool.name)) { all.push(tool); names.add(tool.name); }
      }
    }
    transport.setReadOnlyTools(all.filter(tool => tool.annotations?.readOnlyHint === true).map(tool => tool.name));
    const tools = all.filter((tool) => !AUTH_TRANSPORT_TOOLS.has(tool.name) && !isOutputTransportTool(tool.name) && !SUBAGENT_TOOLS.has(tool.name) && !CRAFT_TOOL_NAMES.has(tool.name) && (ALLOWED === null || ALLOWED.has(tool.name)));

    // Payload-free provenance for the supervisor's workflow accounting. Tool
    // names come from discovery, never namespace/name heuristics.
    if (process.env.NAUTIONETTE_MCP_CATALOG_FILE) {
      writeFileSync(process.env.NAUTIONETTE_MCP_CATALOG_FILE, JSON.stringify(tools.map(tool => tool.name)), { mode: 0o600 });
    }

    const definitions = tools.map(tool => ({
        name: tool.name,
        namespace: { name: tool.server || tool.name.split("_")[0] || "mcp", description: "Selected MCP capabilities" },
        annotations: tool.annotations,
        label: tool.title ?? tool.name,
        description: tool.description ?? `MCP tool ${tool.name}`,
        promptSnippet: tool.description?.split("\n")[0],
        parameters: tool.inputSchema ?? { type: "object", properties: {} },
        async execute(_toolCallId: string, params: unknown, signal?: AbortSignal) {
          if (oauthTools.has(tool.name)) {
            await waitForMcpAuth(control.rpc, grant || {}, tool.name, _toolCallId, signal);
          }
          const dispatch = oauthTools.has(tool.name) ? transport.rpcFresh : rpc;
          const result = await dispatch("tools/call", { name: tool.name, arguments: params ?? {} }, signal);
          const text = renderToolResult(result);
          if (result?.isError) throw new Error(text);
          return { content: [{ type: "text", text }], details: result ?? {} };
        },
      }));
    registerMcpTools(pi, definitions, MODE);
    console.error(`[nautionette] bridged ${tools.length} of ${all.length} MCP tool(s) from ${MCP_URL}`);
  } catch (error) {
    // A missing gateway must not cost the agent its built-in tools.
    console.error(`[nautionette] MCP bridge unavailable: ${(error as Error).message}`);
  }
}
