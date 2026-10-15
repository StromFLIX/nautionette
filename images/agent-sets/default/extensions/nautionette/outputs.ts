/** First-party output tools. Files and capabilities never enter model arguments. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { constants, readFileSync, unlinkSync } from "node:fs";
import { mkdtemp, open, realpath, writeFile } from "node:fs/promises";
import { basename, extname, join, resolve, sep } from "node:path";

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const MAX_PUBLICATION_BYTES = 40 * 1024 * 1024;
export const OUTPUT_TRANSPORT_TOOLS = new Set(["backend_publish_chat_output", "backend_read_chat_output"]);
const LOCAL_TOOLS = new Set(["publish_output", "read_output"]);
export const isOutputTransportTool = (name: string) => OUTPUT_TRANSPORT_TOOLS.has(name) || LOCAL_TOOLS.has(name);

type Rpc = (method: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<Record<string, any>>;
type Grant = { grant: string };
type FileSpec = { path: string; name?: string; mime_type?: string; role?: "file" | "source" | "preview" };
const MIME: Record<string, string> = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
  ".pdf": "application/pdf", ".json": "application/json", ".csv": "text/csv", ".txt": "text/plain",
  ".md": "text/markdown", ".svg": "image/svg+xml", ".dxf": "image/vnd.dxf", ".zip": "application/zip",
};

export function loadOutputGrant(path = process.env.NAUTIONETTE_OUTPUT_GRANT_FILE || "/tmp/nautionette-outputs.json"): Grant | null {
  let raw: string;
  try { raw = readFileSync(path, "utf8"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw new Error("Cannot load output capability"); }
  try { unlinkSync(path); }
  catch { throw new Error("Cannot consume output capability configuration"); }
  try {
    const value = JSON.parse(raw);
    if (typeof value.grant !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(value.grant)) throw new Error();
    return { grant: value.grant };
  } catch { throw new Error("Invalid output capability configuration"); }
}

const within = (path: string, roots: string[]) => roots.some(root => path.startsWith(root + sep));

export async function sandboxFile(spec: FileSpec, cwd: string, signal?: AbortSignal) {
  signal?.throwIfAborted();
  const roots = [...new Set([resolve(cwd), "/workspace", "/projects"])];
  const path = await realpath(resolve(cwd, spec.path));
  if (!within(path, roots)) throw new Error("Publish only files from the workspace or selected project worktrees");
  // No FIFOs/devices, final symlinks, unbounded readFile(), or symlink races in
  // parent directories. Verify the opened descriptor's actual path on Linux.
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    if (!within(await realpath(`/proc/self/fd/${handle.fd}`), roots)) throw new Error("Output file escaped its workspace");
    const before = await handle.stat();
    if (!before.isFile() || before.size < 1 || before.size > MAX_FILE_BYTES) throw new Error("Output files must be regular, nonempty files of at most 20 MiB");
    const buffer = Buffer.alloc(before.size + 1);
    let size = 0;
    while (size < buffer.length) {
      signal?.throwIfAborted();
      const read = await handle.read(buffer, size, buffer.length - size, null);
      if (!read.bytesRead) break;
      size += read.bytesRead;
    }
    const after = await handle.stat();
    if (size !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs) throw new Error("Output file changed while publishing; finish writing it first");
    return {
      name: spec.name || basename(path),
      mime_type: spec.mime_type || MIME[extname(path).toLowerCase()] || "application/octet-stream",
      role: spec.role || "file",
      data: buffer.subarray(0, size).toString("base64"),
      size,
    };
  } finally { await handle.close(); }
}

async function transport(rpc: Rpc, name: string, body: Record<string, unknown>, signal?: AbortSignal) {
  const bounded = signal ? AbortSignal.any([signal, AbortSignal.timeout(60000)]) : AbortSignal.timeout(60000);
  const result = await rpc("tools/call", { name, arguments: { body } }, bounded);
  const text = (result.content || []).filter((part: any) => part.type === "text").map((part: any) => part.text).join("\n");
  if (result.isError) throw new Error(text || "Output operation failed");
  return JSON.parse(text);
}

const textResult = (value: Record<string, any>) => ({ content: [{ type: "text" as const, text: JSON.stringify(value) }], details: value });
const id = { type: "string", pattern: "^[a-f0-9]{32}$" };

export function registerOutputTools(pi: ExtensionAPI, rpc: Rpc, grant: Grant | null) {
  if (!grant) return; // Workflows/custom environments without a chat have no chat capability.
  pi.registerTool({
    name: "publish_output",
    label: "Publish output",
    description: "Publish generated files durably to this chat's Outputs tab. Supply local file paths, not bytes or URLs. Include a PNG/JPEG/WebP preview for PDFs/CAD files, and role=source for editable documents. Files survive sandbox cleanup. Publish only completed snapshots. To revise an existing output, first read_output and supply output_id plus its expected_revision; old revisions stay available. Up to 8 files, 20 MiB each, 40 MiB total; 256 MiB per chat. Success means all files are committed. If interrupted, read_output before retrying.",
    promptSnippet: "Use publish_output for requested deliverables (images, PDFs, exports), including previews/source when useful. Ordinary repository edits do not need publication.",
    parameters: {
      type: "object", additionalProperties: false, required: ["title", "files"],
      properties: {
        title: { type: "string", minLength: 1, maxLength: 120 },
        summary: { type: "string", maxLength: 2000 },
        source_app: { type: "string", maxLength: 64 },
        output_id: id,
        expected_revision: { type: "integer", minimum: 1, maximum: 64 },
        files: { type: "array", minItems: 1, maxItems: 8, items: {
          type: "object", additionalProperties: false, required: ["path"], properties: {
            path: { type: "string", minLength: 1, maxLength: 4096 },
            name: { type: "string", minLength: 1, maxLength: 200 },
            mime_type: { type: "string", minLength: 1, maxLength: 127 },
            role: { enum: ["file", "source", "preview"] },
          },
        } },
      },
    } as any,
    async execute(toolCallId, params: any, signal, _onUpdate, ctx) {
      const files = [];
      let total = 0;
      for (const spec of params.files) {
        const { size, ...file } = await sandboxFile(spec, ctx.cwd, signal);
        total += size;
        if (total > MAX_PUBLICATION_BYTES) throw new Error("Output publication exceeds 40 MiB");
        files.push(file);
      }
      const output = await transport(rpc, "backend_publish_chat_output", {
        ...params, ...grant, request_id: toolCallId, files,
      }, signal);
      return textResult({ output, message: "Published to this chat's Outputs tab. Files are durable; local sandbox files can now be removed." });
    },
  });
  pi.registerTool({
    name: "read_output",
    label: "Read output",
    description: "List this chat's published outputs (no arguments), inspect an output/revision (output_id, optional revision), or restore one file to this sandbox (also file_id). Use this on a later turn to continue working from the saved source, or after an uncertain publication. Restored files return a local path, not raw bytes. Use the read tool on a restored image for visual inspection. Access is limited to this chat.",
    parameters: {
      type: "object", additionalProperties: false, properties: {
        output_id: id, revision: { type: "integer", minimum: 1, maximum: 64 }, file_id: id,
      },
    } as any,
    async execute(_toolCallId, params: any, signal, _onUpdate, ctx) {
      const result = await transport(rpc, "backend_read_chat_output", { ...params, ...grant }, signal);
      if (!result.file) return textResult(result);
      const file = result.file;
      if (typeof file.data !== "string" || file.data.length > Math.ceil(MAX_FILE_BYTES / 3) * 4) throw new Error("Invalid output file response");
      const data = Buffer.from(file.data, "base64");
      if (!data.length || data.length > MAX_FILE_BYTES || data.toString("base64") !== file.data || data.length !== file.size) throw new Error("Invalid output file response");
      signal?.throwIfAborted();
      const directory = await mkdtemp(join(ctx.cwd, "output-"));
      const extension = /\.[a-z0-9]{1,12}$/i.exec(file.name)?.[0] || ".bin";
      const path = join(directory, `document${extension}`);
      await writeFile(path, data, { mode: 0o600, flag: "wx", signal });
      const { data: _bytes, ...metadata } = file;
      return textResult({ output_id: result.output_id, revision: result.revision, file: { ...metadata, path } });
    },
  });
}
