// On-demand native applications. Nothing is spawned until a tool actually uses an app.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { mkdtemp, writeFile } from "node:fs/promises";
import { join } from "node:path";

export const CRAFT_TOOL_NAMES = new Set(["craft_apps", "craft_tools", "craft_call"]);
const MAX_FRAME = 32 * 1024 * 1024;
const MAX_TEXT = 24_000;
const VERSION = /^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[a-zA-Z0-9][a-zA-Z0-9.-]{0,40})?$/;
type Config = { app: string; name: string; description: string; version: string; sha256: string; arch: string; args: string[] };
type Pending = { resolve: (value: any) => void; reject: (error: Error) => void; clean: () => void };

export function loadCraftConfig({ catalogFile = "/usr/local/lib/nautionette/artcraft/catalog.json", selection = process.env.NAUTIONETTE_ARTCRAFT_JSON } = {}): Config[] {
  // Older/custom images can still answer chats without a native catalog.
  if (!existsSync(catalogFile)) return [];
  const catalog = JSON.parse(readFileSync(catalogFile, "utf8"));
  const arch = process.arch === "arm64" ? "aarch64" : "x86_64";
  const selected = selection ? JSON.parse(selection) : Object.entries(catalog).map(([app, item]: [string, any]) => ({ app, release: { app, version: item.version, sha256: item.sha256[arch], arch } }));
  if (!Array.isArray(selected) || selected.length > 13) throw new Error("Invalid ArtCraft runtime selection");
  const seen = new Set();
  return selected.map(item => {
    if (!item || !Object.hasOwn(catalog, item.app) || seen.has(item.app) || item.release?.app !== item.app || typeof item.release?.version !== "string" || !VERSION.test(item.release.version) || typeof item.release.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.release.sha256) || item.release.arch !== arch || Object.keys(item.release).some(key => !["app", "version", "sha256", "arch"].includes(key))) throw new Error("Invalid ArtCraft runtime entry");
    seen.add(item.app);
    const metadata = catalog[item.app];
    return { app: item.app, name: metadata.name, description: metadata.description, args: metadata.mcp_args || [], ...item.release };
  });
}

/** Newline JSON-RPC, one mutable document session per app. Never replay a call. */
export class CraftSession {
  child: ChildProcessWithoutNullStreams;
  pending = new Map<number, Pending>();
  tools: any[] = [];
  instructions = "";
  failure = "";
  private buffer = "";
  private sequence = 0;
  private tail: Promise<any> = Promise.resolve();
  private stopped = false;
  private timer?: ReturnType<typeof setTimeout>;
  private timeout: number;

  constructor(binary: string, args: string[], cwd: string, timeout = 180_000) {
    this.timeout = timeout;
    this.child = spawn(binary, ["mcp", ...args], {
      cwd, detached: true, stdio: "pipe",
      // No gateway credentials, turn grants or package secrets in child env.
      env: { PATH: process.env.PATH, HOME: cwd, LANG: "C.UTF-8", TMPDIR: process.env.TMPDIR || "/tmp" },
    });
    this.child.stdin.on("error", () => this.fail("Application input closed"));
    this.child.on("error", () => this.fail("Native application could not start; check the installed version"));
    this.child.on("close", () => {
      this.fail("Application exited; its unsaved in-memory state is no longer available");
    });
    this.child.stderr.on("data", () => {}); // Drain, but never leak arbitrary native diagnostics/secrets.
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk: string) => {
      this.buffer += chunk;
      let index;
      while ((index = this.buffer.indexOf("\n")) !== -1) {
        if (Buffer.byteLength(this.buffer.slice(0, index)) > MAX_FRAME) return this.fail("Application reply exceeded 32 MiB");
        const line = this.buffer.slice(0, index).trim();
        this.buffer = this.buffer.slice(index + 1);
        if (!line) continue;
        let reply;
        try { reply = JSON.parse(line); } catch { return this.fail("Application returned invalid JSON-RPC"); }
        if (!reply || typeof reply !== "object" || Array.isArray(reply)) return this.fail("Application returned invalid JSON-RPC");
        const pending = this.pending.get(reply.id);
        if (!pending) {
          if (reply.method && !Object.hasOwn(reply, "id")) continue; // MCP notification.
          return this.fail("Application returned an uncorrelated reply");
        }
        this.pending.delete(reply.id);
        pending.clean();
        if (reply.jsonrpc !== "2.0" || (!Object.hasOwn(reply, "result") && !reply.error)) {
          pending.reject(new Error("Application returned an invalid JSON-RPC envelope"));
          this.fail("Application protocol failed");
        } else if (reply.error) pending.reject(new Error(String(reply.error.message || "Application request failed").slice(0, 2000)));
        else pending.resolve(reply.result);
      }
      if (Buffer.byteLength(this.buffer) > MAX_FRAME) this.fail("Application reply exceeded 32 MiB");
    });
  }

  private fail(message: string) {
    if (!this.failure) this.failure = `${message}. A mutation may already have applied: do not replay it blindly. Save/check the document when possible, or explicitly reopen the app and restore its last published source.`;
    for (const item of this.pending.values()) { item.clean(); item.reject(new Error(this.failure)); }
    this.pending.clear();
    this.buffer = "";
    if (this.stopped) return;
    this.stopped = true;
    this.child.stdin.destroy();
    try { if (this.child.pid) process.kill(-this.child.pid, "SIGTERM"); } catch { /* Already gone. */ }
    this.timer = setTimeout(() => { try { if (this.child.pid) process.kill(-this.child.pid, "SIGKILL"); } catch { /* Already gone. */ } }, 1000);
    this.timer.unref();
  }

  close() { this.fail("Application session closed"); }

  request(method: string, params: any, signal?: AbortSignal): Promise<any> {
    signal?.throwIfAborted();
    if (this.failure) return Promise.reject(new Error(this.failure));
    const id = ++this.sequence;
    const raw = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
    if (Buffer.byteLength(raw) > MAX_FRAME) throw new Error("Application request exceeds 32 MiB");
    return new Promise((resolve, reject) => {
      const abort = () => this.fail("Application operation cancelled; session stopped");
      const timer = setTimeout(() => this.fail("Application operation timed out; session stopped"), this.timeout);
      const clean = () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); };
      this.pending.set(id, { resolve, reject, clean });
      signal?.addEventListener("abort", abort, { once: true });
      this.child.stdin.write(raw);
    });
  }

  async initialize(signal?: AbortSignal) {
    const initialized = await this.request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "nautionette-artcraft", version: "1" } }, signal);
    if (!initialized?.protocolVersion) throw new Error("Application did not initialize MCP");
    this.instructions = String(initialized.instructions || "").slice(0, 8000);
    this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
    let cursor: string | undefined;
    let pages = 0;
    const cursors = new Set();
    do {
      if (++pages > 50) throw new Error("Application tool catalog exceeds its page limit");
      const result = await this.request("tools/list", cursor ? { cursor } : {}, signal);
      if (!Array.isArray(result?.tools) || result.tools.some((tool: any) => typeof tool.name !== "string" || typeof tool.inputSchema !== "object" || !tool.inputSchema)) throw new Error("Invalid application tool catalog");
      this.tools.push(...result.tools);
      cursor = result.nextCursor;
      if (this.tools.length > 2000 || (cursor && cursors.has(cursor))) throw new Error("Application tool catalog exceeds its limit");
      if (cursor) cursors.add(cursor);
    } while (cursor);
    if (!this.tools.length) throw new Error("Application exposes no callable tools");
  }

  call(tool: string, args: any, signal?: AbortSignal) {
    // Tools in one model response may run concurrently. Serialize access to a
    // mutable engine; abort while queued must not dispatch or kill a sibling.
    const task = this.tail.then(() => {
      signal?.throwIfAborted();
      if (!this.tools.some(item => item.name === tool)) throw new Error("Unknown application tool; use craft_tools to discover its exact name/schema");
      return this.request("tools/call", { name: tool, arguments: args }, signal);
    });
    this.tail = task.catch(() => {});
    return task;
  }
}

const appProperty = { type: "string", description: "App ID from craft_apps, for example cadcraft, wordcraft or deckcraft" };
const textResult = (value: any) => ({ content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }], details: value });

export function registerCraftTools(pi: ExtensionAPI, config = loadCraftConfig(), { root = "/opt/artcraft", maxSessions = 3, timeout = 180_000 } = {}) {
  if (!config.length) return;
  const sessions = new Map<string, Promise<CraftSession>>();
  const processes = new Map<string, CraftSession>();
  const broken = new Set<string>();
  const metadata = (app: string) => {
    const item = config.find(item => item.app === app);
    if (!item) throw new Error("Application is unavailable/disabled. Use craft_apps and Settings → Creative apps.");
    return item;
  };
  const open = async (app: string, cwd: string, signal?: AbortSignal, explicit = false) => {
    signal?.throwIfAborted();
    const item = metadata(app);
    if (explicit) {
      const previous = sessions.get(app);
      try {
        const existing = await previous;
        if (existing?.failure && sessions.get(app) === previous) {
          existing.close(); sessions.delete(app); processes.delete(app);
        }
      } catch {
        if (sessions.get(app) === previous) { sessions.delete(app); processes.delete(app); }
      }
      broken.delete(app);
    }
    if (broken.has(app)) throw new Error("Application state was lost. Explicitly reopen with craft_apps action=open, then restore the last saved/published source. No command was replayed.");
    if (!sessions.has(app)) {
      if (sessions.size >= maxSessions) throw new Error(`At most ${maxSessions} native apps may be open per turn. Save/publish a source, then close an app with craft_apps.`);
      const initializing = (async () => {
        let session: CraftSession | undefined;
        try {
          const manifest = JSON.parse(readFileSync(join(root, app, "manifest.json"), "utf8"));
          if (["app", "version", "arch", "sha256"].some(key => manifest[key] !== item[key as keyof Config])) throw new Error("Installed application does not match this turn's pinned version");
          session = new CraftSession(join(root, app, "bin", `${app}-cli`), item.args, cwd, timeout);
          processes.set(app, session);
          await session.initialize(signal);
          return session;
        } catch (error) { session?.close(); throw error; }
      })();
      sessions.set(app, initializing);
    }
    const pending = sessions.get(app)!;
    try {
      const session = await pending;
      if (session.failure) throw new Error(session.failure);
      return session;
    } catch (error) {
      if (sessions.get(app) === pending) { sessions.delete(app); processes.delete(app); broken.add(app); }
      throw error;
    }
  };
  const close = async (app: string) => {
    const pending = sessions.get(app), session = processes.get(app);
    sessions.delete(app); processes.delete(app); broken.delete(app);
    // Stop even a process which has not completed MCP initialization. Never
    // wait for its request timeout before allowing turn shutdown/explicit close.
    session?.close();
    if (pending) { try { (await pending).close(); } catch { /* Failed startup already closed it. */ } }
  };
  pi.on("session_shutdown", async () => { await Promise.all([...sessions.keys()].map(close)); });

  pi.registerTool({
    name: "craft_apps", label: "Creative applications",
    description: "List the installed native ArtCraft apps and pinned versions, or open/close a headless app. All execution is server-side, independent of the browser. Apps start on demand (at most 3 open per turn), keep state within this turn, and stop when it ends. Save and publish editable source plus exports/previews before finishing; use read_output to restore source in later turns. Closing discards unsaved state. Explicit open after a failure creates a NEW empty session; no mutations are retried.",
    promptSnippet: "Native ArtCraft creative apps are available through craft_apps, craft_tools and craft_call; use them for requested CAD, documents, slides, spreadsheets, graphics, PDFs, video or audio deliverables.",
    parameters: { type: "object", properties: { action: { type: "string", enum: ["list", "open", "close"], default: "list" }, app: appProperty }, additionalProperties: false },
    async execute(_id: string, params: any, signal: AbortSignal | undefined, _update: any, ctx: any) {
      const action = params.action || "list";
      if (action !== "list") {
        metadata(params.app);
        if (action === "close") await close(params.app);
        else await open(params.app, ctx.cwd, signal, true);
      }
      return textResult({ apps: config.map(({ app, name, description, version }) => ({ app, name, description, version, open: sessions.has(app), state_lost: broken.has(app) })), ...(action === "close" ? { note: "App closed; unsaved in-memory changes were discarded." } : {}) });
    },
  });
  pi.registerTool({
    name: "craft_tools", label: "Discover application tools",
    description: "Discover an ArtCraft app's actual MCP tools. Starts its headless engine on demand. With name, returns that tool's complete JSON parameter schema; otherwise lists up to 30 names/descriptions, optionally filtered by query. Use returned schemas, not guessed commands. For hundreds of engine commands, use the app's command-list tool and its execute/run tool. UI-only tools need a desktop and are not supported here.",
    parameters: { type: "object", properties: { app: appProperty, name: { type: "string" }, query: { type: "string", maxLength: 120 }, offset: { type: "integer", minimum: 0, default: 0 } }, required: ["app"], additionalProperties: false },
    async execute(_id: string, params: any, signal: AbortSignal | undefined, _update: any, ctx: any) {
      const session = await open(params.app, ctx.cwd, signal);
      if (params.name) {
        const tool = session.tools.find(item => item.name === params.name);
        if (!tool) throw new Error("Unknown application tool");
        return textResult({ app: params.app, tool, instructions: session.instructions });
      }
      const query = (params.query || "").toLowerCase();
      const tools = session.tools.filter(tool => `${tool.name} ${tool.description || ""}`.toLowerCase().includes(query));
      const offset = params.offset || 0;
      return textResult({ app: params.app, tools: tools.slice(offset, offset + 30).map(({ name, description }) => ({ name, description: String(description || "").slice(0, 600) })), total: tools.length, next_offset: offset + 30 < tools.length ? offset + 30 : null });
    },
  });
  pi.registerTool({
    name: "craft_call", label: "Use creative application",
    description: "Call one discovered native ArtCraft MCP tool in this turn's persistent headless app session. Use craft_tools to retrieve its exact schema first. Commands for the same app run sequentially; failed/interrupted mutations are never automatically replayed. Render results can return images to vision-capable models. Save/export files under /workspace (absolute paths are safest), inspect a preview, then publish_output with preview/source/export roles so the user can see/download them. Native state is ephemeral until explicitly saved and published.",
    parameters: { type: "object", properties: { app: appProperty, tool: { type: "string", minLength: 1 }, arguments: { type: "object", additionalProperties: true, default: {} } }, required: ["app", "tool"], additionalProperties: false },
    async execute(_id: string, params: any, signal: AbortSignal | undefined, _update: any, ctx: any) {
      const session = await open(params.app, ctx.cwd, signal);
      const result = await session.call(params.tool, params.arguments || {}, signal);
      const parts: any[] = [];
      for (const part of result?.content || []) {
        if (part.type === "text" && typeof part.text === "string") parts.push({ type: "text", text: part.text });
        else if (part.type === "image" && ["image/png", "image/jpeg", "image/webp"].includes(part.mimeType) && typeof part.data === "string") parts.push({ type: "image", data: part.data, mimeType: part.mimeType });
      }
      if (!parts.length) parts.push({ type: "text", text: JSON.stringify(result?.structuredContent || { note: "No text/image content; export the result to a local file." }) });
      let remaining = MAX_TEXT;
      for (const part of parts) if (part.type === "text") {
        if (part.text.length > remaining) {
          const directory = await mkdtemp(join(ctx.cwd, "craft-result-"));
          const path = join(directory, "result.txt");
          await writeFile(path, part.text, { mode: 0o600 });
          part.text = `${part.text.slice(0, remaining)}\n… Full result saved to ${path}`;
        }
        remaining = Math.max(0, remaining - part.text.length);
      }
      if (result?.isError) throw new Error(parts.filter(part => part.type === "text").map(part => part.text).join("\n") || "Application tool failed");
      return { content: parts, details: { app: params.app, tool: params.tool } };
    },
  });
}
