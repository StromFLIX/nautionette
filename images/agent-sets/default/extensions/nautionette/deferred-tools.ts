/** Native Pi tool search, with current permissions and loadout preserved on resume. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const MARKER = "nautionette.mcp-schema-mode";

export function registerMcpTools(pi: ExtensionAPI, tools: any[], mode = "deferred") {
  if (!["direct", "deferred"].includes(mode)) throw new Error("Invalid MCP schema mode");
  const names = new Set(tools.map(tool => tool.name));
  for (const tool of tools) pi.registerTool({ ...tool, exposure: mode === "deferred" ? "deferred" : "direct" });
  if (!tools.length) return;
  let deferred = mode === "deferred";
  pi.on("session_start", (_event, ctx) => {
    const search = pi.getAllTools().find(tool => tool.name === "tool_search" && tool.reachesTools);
    if (deferred && !search) {
      // Custom hosts can disable built-ins. Preserve capabilities rather than
      // leaving deferred tools unreachable without their native loader.
      deferred = false;
      for (const tool of tools) pi.registerTool({ ...tool, exposure: "direct" });
      console.error("[nautionette] tool_search unavailable; retaining direct MCP schemas");
    }
    const previous = ctx.sessionManager.getBranch().findLast(entry =>
      entry.type === "custom" && entry.customType === MARKER)?.data as { mode?: string } | undefined;
    if (deferred) {
      // Migrate old eager transcripts once. Subsequent processes keep exactly the
      // tools native tool_search loaded on this branch, intersected with today's
      // registered allowlist by Pi. Never reset that loadout on every response.
      const active = pi.getActiveTools().filter(name => previous?.mode === "deferred" || !names.has(name));
      pi.setActiveTools([...new Set([...active, "tool_search"])]);
    }
    const current = deferred ? "deferred" : "direct";
    if (previous?.mode !== current) pi.appendEntry(MARKER, { mode: current });
  });
  pi.on("before_agent_start", event => {
    if (!deferred) return;
    event.systemPromptOptions.sections.nautionette_mcp =
      "Selected external MCP capabilities are available through tool_search. Search by task or exact tool name before calling a tool that is not loaded. File, shell, history and first-party output/creative tools remain directly available. Search does not execute a remote tool or grant additional permissions.";
    // An explicit, exact tool name in this input can be loaded without an extra
    // discovery/model round trip. Only currently allowed, registered names qualify.
    const mentioned = tools.filter(tool => {
      const escaped = tool.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return new RegExp(`(^|[^A-Za-z0-9_])${escaped}($|[^A-Za-z0-9_])`).test(event.prompt || "");
    }).map(tool => tool.name);
    if (mentioned.length) pi.setActiveTools([...new Set([...pi.getActiveTools(), ...mentioned])]);
  });
}
