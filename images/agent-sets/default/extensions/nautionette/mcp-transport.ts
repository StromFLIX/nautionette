/** Recover the shared MCP session without blindly repeating a mutation. */
export function createMcpTransport(url: string, fetcher: typeof fetch = fetch) {
  let messageId = 0;
  let sessionId: string | undefined;
  let connected = false;
  let connecting: Promise<void> | undefined;
  let readonlyTools = new Set<string>();

  class HttpFailure extends Error {
    status: number;
    session: string | undefined;
    constructor(status: number, session: string | undefined) {
      super(`MCP transport -> HTTP ${status}`);
      this.status = status;
      this.session = session;
    }
  }

  async function send(method: string, params: Record<string, unknown>, signal?: AbortSignal) {
    signal?.throwIfAborted();
    const notification = method.startsWith("notifications/");
    const body = { jsonrpc: "2.0", method, params, ...(notification ? {} : { id: ++messageId }) };
    const headers: Record<string, string> = {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      "mcp-protocol-version": "2025-06-18",
    };
    const requestSession = sessionId;
    if (requestSession) headers["mcp-session-id"] = requestSession;
    const response = await fetcher(url, { method: "POST", headers, body: JSON.stringify(body), signal });
    if (!response.ok) {
      // Do not leak upstream bodies or infer that a failed mutation did not run.
      await response.body?.cancel();
      throw new HttpFailure(response.status, requestSession);
    }
    const returned = response.headers.get("mcp-session-id");
    if (returned) sessionId = returned;
    if (notification) { await response.body?.cancel(); return {}; }
    const raw = await response.text();
    const payload = raw.includes("event:") || raw.startsWith("data:")
      ? raw.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trim()).join("")
      : raw;
    const parsed = JSON.parse(payload);
    if (parsed.error) throw new Error(`${method} -> ${parsed.error.message ?? "mcp error"}`);
    return parsed.result ?? {};
  }

  async function connect(signal?: AbortSignal): Promise<void> {
    signal?.throwIfAborted();
    if (connecting) return connecting;
    if (connected) return;
    connecting = (async () => {
      sessionId = undefined;
      try {
        await send("initialize", {
          protocolVersion: "2025-06-18", capabilities: {},
          clientInfo: { name: "nautionette-agent-set", version: "1.0.0" },
        }, signal);
        await send("notifications/initialized", {}, signal);
        connected = true;
      } catch (error) {
        sessionId = undefined;
        connected = false;
        throw error;
      }
    })();
    try { await connecting; } finally { connecting = undefined; }
  }

  async function rpc(method: string, params: Record<string, any> = {}, signal?: AbortSignal): Promise<Record<string, any>> {
    await connect(signal);
    try { return await send(method, params, signal); }
    catch (error) {
      // A stale gateway session rejects otherwise unrelated tools with 400/404.
      // Re-handshake once; don't retry network errors, auth failures or RPC errors.
      if (!(error instanceof HttpFailure) || !error.session || ![400, 404].includes(error.status)) throw error;
      if (sessionId === error.session) connected = false;
      const safe = method === "tools/list" || (method === "tools/call" && readonlyTools.has(params.name));
      try { await connect(signal); }
      catch {
        throw new Error(`${method} -> HTTP ${error.status}; MCP session recovery failed. Original operation was not replayed.`);
      }
      if (!safe) {
        throw new Error(`${method} -> HTTP ${error.status}; MCP session refreshed. Original operation was NOT replayed; inspect its outcome before retrying.`);
      }
      return await send(method, params, signal);
    }
  }

  return {
    rpc, connect,
    setReadOnlyTools(names: string[]) { readonlyTools = new Set(names); },
  };
}
