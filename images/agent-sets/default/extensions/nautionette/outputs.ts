/** First-party output control over MCP; binary data transfers directly to S3. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createHash } from "node:crypto";
import { constants, readFileSync, unlinkSync } from "node:fs";
import { mkdtemp, open, realpath, rm } from "node:fs/promises";
import { basename, extname, join, resolve, sep } from "node:path";

// Safety ceiling only. Effective file/publication/chat/global limits are checked
// server-side against Settings → Storage on every prepare AND commit.
const HARD_FILE_BYTES = 1024 * 1024 * 1024;
export const OUTPUT_TRANSPORT_TOOLS = new Set(["backend_prepare_chat_output", "backend_publish_chat_output", "backend_read_chat_output"]);
const LOCAL_TOOLS = new Set(["publish_output", "read_output"]);
export const isOutputTransportTool = (name: string) => OUTPUT_TRANSPORT_TOOLS.has(name) || LOCAL_TOOLS.has(name);
export const publicationRequestId = (value: string) => [...value].length <= 256 ? value : `sha256:${createHash("sha256").update(value).digest("hex")}`;

type Rpc = (method: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<Record<string, any>>;
type Grant = { grant: string };
type FileSpec = { path: string; name?: string; mime_type?: string; role?: "file" | "source" | "preview" };
const MIME: Record<string, string> = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
  ".pdf": "application/pdf", ".json": "application/json", ".csv": "text/csv", ".txt": "text/plain",
  ".md": "text/markdown", ".svg": "image/svg+xml", ".dxf": "image/vnd.dxf", ".zip": "application/zip",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".step": "model/step", ".stp": "model/step", ".stl": "model/stl",
  ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
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
const transferSignal = (signal?: AbortSignal) => signal ? AbortSignal.any([signal, AbortSignal.timeout(300000)]) : AbortSignal.timeout(300000);
const storageError = (message: string, settingsUrl = "/settings/storage", code = "output_transfer_failed") =>
  new Error(JSON.stringify({ code, message, settings_url: settingsUrl }));

export async function sandboxFile(spec: FileSpec, cwd: string, signal?: AbortSignal) {
  signal?.throwIfAborted();
  const roots = [...new Set([resolve(cwd), "/workspace", "/projects"])];
  const path = await realpath(resolve(cwd, spec.path));
  if (!within(path, roots)) throw new Error("Publish only files from the workspace or selected project worktrees");
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    if (!within(await realpath(`/proc/self/fd/${handle.fd}`), roots)) throw new Error("Output file escaped its workspace");
    const before = await handle.stat();
    if (!before.isFile() || before.size < 1) throw new Error("Output files must be regular, nonempty files");
    if (before.size > HARD_FILE_BYTES) throw storageError("Output files exceed the protocol ceiling of 1 GiB each.", undefined, "output_file_too_large");
    const hash = createHash("sha256"), buffer = Buffer.alloc(1024 * 1024);
    let position = 0;
    while (position < before.size) {
      signal?.throwIfAborted();
      const { bytesRead } = await handle.read(buffer, 0, Math.min(buffer.length, before.size - position), position);
      if (!bytesRead) throw new Error("Output file changed while publishing; finish writing it first");
      hash.update(buffer.subarray(0, bytesRead)); position += bytesRead;
    }
    const unchanged = async () => {
      const after = await handle.stat();
      if (after.size !== before.size || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs)
        throw new Error("Output file changed while publishing; finish writing it first");
    };
    await unchanged();
    return {
      handle, unchanged,
      metadata: {
        name: spec.name || basename(path), mime_type: spec.mime_type || MIME[extname(path).toLowerCase()] || "application/octet-stream",
        role: spec.role || "file", size: before.size, sha256: hash.digest("hex"),
      },
    };
  } catch (error) { await handle.close(); throw error; }
}

async function transport(rpc: Rpc, name: string, body: Record<string, unknown>, signal?: AbortSignal) {
  const result = await rpc("tools/call", { name, arguments: { body } }, transferSignal(signal));
  const text = (result.content || []).filter((part: any) => part.type === "text").map((part: any) => part.text).join("\n");
  if (result.isError) throw new Error(text || "Output operation failed. Manage storage at /settings/storage");
  return JSON.parse(text);
}

function transferUrl(url: unknown): string {
  if (typeof url !== "string") throw new Error("Invalid private binary transfer response");
  const parsed = new URL(url);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash)
    throw new Error("Invalid private binary transfer response");
  return parsed.href;
}

const textResult = (value: Record<string, any>) => ({ content: [{ type: "text" as const, text: JSON.stringify(value) }], details: value });
const id = { type: "string", pattern: "^[a-f0-9]{32}$" };

export function registerOutputTools(pi: ExtensionAPI, rpc: Rpc, grant: Grant | null) {
  if (!grant) return;
  pi.registerTool({
    name: "publish_output", label: "Publish output",
    description: "Publish generated files durably to this chat's Outputs tab. Supply local file paths, not bytes or URLs. Include a PNG/JPEG/WebP preview for PDFs/CAD files, and role=source for editable documents. Publish completed snapshots only. To revise, first read_output and supply output_id plus expected_revision; old revisions stay available. Up to 8 files; configurable file, publication, chat and instance quotas are enforced. Quota errors include usage, limits and a settings_url: give the user that link instead of repeatedly retrying. Success means all files are committed. If interrupted, read_output before retrying.",
    promptSnippet: "Use publish_output for requested deliverables (images, PDFs, exports), including previews/source when useful. Ordinary repository edits do not need publication.",
    parameters: {
      type: "object", additionalProperties: false, required: ["title", "files"],
      properties: {
        title: { type: "string", minLength: 1, maxLength: 120 }, summary: { type: "string", maxLength: 2000 },
        source_app: { type: "string", maxLength: 64 }, output_id: id,
        expected_revision: { type: "integer", minimum: 1, maximum: 64 },
        files: { type: "array", minItems: 1, maxItems: 8, items: {
          type: "object", additionalProperties: false, required: ["path"], properties: {
            path: { type: "string", minLength: 1, maxLength: 4096 }, name: { type: "string", minLength: 1, maxLength: 200 },
            mime_type: { type: "string", minLength: 1, maxLength: 127 }, role: { enum: ["file", "source", "preview"] },
          },
        } },
      },
    } as any,
    async execute(toolCallId, params: any, signal, _onUpdate, ctx) {
      const files: Awaited<ReturnType<typeof sandboxFile>>[] = [];
      const request_id = publicationRequestId(toolCallId);
      try {
        for (const spec of params.files) files.push(await sandboxFile(spec, ctx.cwd, signal));
        const prepared = await transport(rpc, "backend_prepare_chat_output", {
          ...params, ...grant, request_id, files: files.map(file => file.metadata),
        }, signal);
        let output = prepared.output;
        if (!output) {
          if (!Array.isArray(prepared.files) || prepared.files.length !== files.length || !/^[a-f0-9]{32}$/.test(prepared.upload_id))
            throw new Error("Invalid private binary upload response");
          for (let index = 0; index < files.length; index++) {
            const file = files[index], target = prepared.files[index];
            await file.unchanged(); signal?.throwIfAborted();
            const body = file.handle.createReadStream({ autoClose: false, start: 0, end: file.metadata.size - 1 });
            try {
              const response = await fetch(transferUrl(target.url), {
                method: "PUT", headers: target.headers, body: body as any, duplex: "half",
                signal: transferSignal(signal), redirect: "error",
              } as any);
              await response.body?.cancel();
              if (response.status === 507) throw storageError("Output object storage is out of physical disk space. Remove outputs in Settings → Storage or increase the storage disk capacity; raising a quota alone does not create space.", prepared.settings_url, "output_disk_full");
              if (response.status >= 500) throw storageError(`Output object storage is unavailable (HTTP ${response.status}). Check MaxIO health in Settings → Storage. Files are not published.`, prepared.settings_url, "output_storage_unavailable");
              if (!response.ok) throw storageError(`Binary upload failed (HTTP ${response.status}). Check storage health; if full, remove outputs or increase disk capacity.`, prepared.settings_url);
              await file.unchanged();
            } catch (error) {
              signal?.throwIfAborted();
              // Fetch/provider errors may contain presigned URLs. Never forward
              // their raw message, response body, cause or headers to the model.
              if (error instanceof Error && error.message.startsWith('{"code":')) throw error;
              throw storageError("Binary upload failed or timed out. Files are not published. Inspect read_output before retrying.", prepared.settings_url);
            } finally { body.destroy(); }
          }
          output = await transport(rpc, "backend_publish_chat_output", { ...grant, request_id, upload_id: prepared.upload_id }, signal);
        }
        return textResult({ output, message: "Published to this chat's Outputs tab. Files are durable; local sandbox files can now be removed." });
      } finally { for (const file of files) await file.handle.close(); }
    },
  });
  pi.registerTool({
    name: "read_output", label: "Read output",
    description: "List this chat's published outputs (no arguments), inspect an output/revision (output_id, optional revision), or restore one file to this sandbox (also file_id). Use this on a later turn to continue working from saved source, or after an uncertain publication. Restored files return a local path, not bytes or private URLs. Use read on a restored image for visual inspection. Access is limited to this chat.",
    parameters: { type: "object", additionalProperties: false, properties: {
      output_id: id, revision: { type: "integer", minimum: 1, maximum: 64 }, file_id: id,
    } } as any,
    async execute(_toolCallId, params: any, signal, _onUpdate, ctx) {
      const result = await transport(rpc, "backend_read_chat_output", { ...params, ...grant, binary: true }, signal);
      if (!result.file) return textResult(result);
      const file = result.file;
      if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > HARD_FILE_BYTES || !/^[a-f0-9]{64}$/.test(file.sha256))
        throw new Error("Invalid output file response");
      signal?.throwIfAborted();
      const directory = await mkdtemp(join(ctx.cwd, "output-"));
      const extension = /\.[a-z0-9]{1,12}$/i.exec(file.name)?.[0] || ".bin";
      const path = join(directory, `document${extension}`);
      let completed = false;
      try {
        const target = await open(path, "wx", 0o600);
        try {
          const response = await fetch(transferUrl(file.download_url), { signal: transferSignal(signal), redirect: "error" });
          if (!response.ok || !response.body) { await response.body?.cancel(); throw new Error(); }
          const hash = createHash("sha256"); let size = 0;
          for await (const chunk of response.body) {
            signal?.throwIfAborted(); size += chunk.length;
            if (size > file.size) throw new Error();
            hash.update(chunk);
            // FileHandle.write can be short; writeFile handles complete chunks.
            await target.writeFile(chunk);
          }
          if (size !== file.size || hash.digest("hex") !== file.sha256) throw new Error();
          completed = true;
        } finally { await target.close(); }
      } catch {
        signal?.throwIfAborted();
        throw storageError("Output download failed, expired, or did not pass its size/checksum check. Retry read_output.");
      } finally { if (!completed) await rm(directory, { recursive: true, force: true }); }
      return textResult({ output_id: result.output_id, revision: result.revision, file: {
        id: file.id, name: file.name, mime_type: file.mime_type, size: file.size, sha256: file.sha256, path,
      } });
    },
  });
}
