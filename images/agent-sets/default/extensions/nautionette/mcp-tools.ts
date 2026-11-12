/** Selected MCP schemas are available directly, without a discovery round trip. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const LEGACY_MARKER = "nautionette.mcp-schema-mode";

export function registerMcpTools(pi: ExtensionAPI, tools: any[]) {
  const names = new Set(tools.map(tool => tool.name));
  for (const tool of tools) pi.registerTool({ ...tool, exposure: "direct" });

  // Preserve old transcripts, but undo the loadout written by our short-lived
  // deferred-schema experiment. Only today's allowed/registered tools are added.
  pi.on("session_start", (_event, ctx) => {
    const previous = ctx.sessionManager.getBranch().findLast(entry =>
      entry.type === "custom" && entry.customType === LEGACY_MARKER)?.data as { mode?: string; directToolsRestored?: boolean } | undefined;
    // The old operator fallback also wrote "direct" without necessarily restoring
    // schemas that an earlier deferred turn had explicitly removed.
    if (!previous || !["deferred", "direct"].includes(previous.mode || "") || previous.directToolsRestored) return;
    const all = pi.getAllTools();
    const otherDeferredTools = all.some(tool => !names.has(tool.name) &&
      (tool.exposure === "deferred" || tool.exposure === "codemode"));
    const search = all.find(tool => tool.name === "tool_search");
    const builtinSearch = search?.sourceInfo?.source === "builtin";
    const active = pi.getActiveTools().filter(name =>
      name !== "tool_search" || otherDeferredTools || !builtinSearch);
    pi.setActiveTools([...new Set([...active, ...names])]);
    pi.appendEntry(LEGACY_MARKER, { mode: "direct", directToolsRestored: true });
  });
}
