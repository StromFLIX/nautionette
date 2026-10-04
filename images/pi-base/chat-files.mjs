import { mkdirSync, writeFileSync } from "node:fs";

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_FILES = 8; // Four current attachments plus four from bounded history.

// Materialize original bytes, without parsing, converting, or executing uploads.
// Filenames never choose directories and cannot overwrite workspace instructions.
export function prepareFiles(job, workspace) {
  const messages = [job, ...(job.history || [])];
  const files = messages.flatMap(message => message.files || []);
  if (files.length > MAX_FILES) throw new Error("Too many attached files");
  const ids = new Set();
  for (const file of files) {
    if (!/^[a-f0-9]{32}$/.test(file.id) || ids.has(file.id) ||
        typeof file.name !== "string" || Array.from(file.name).length > 200 ||
        typeof file.data !== "string" || file.data.length > Math.ceil(MAX_FILE_BYTES / 3) * 4 ||
        file.data.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(file.data)) {
      throw new Error("Invalid attached file");
    }
    ids.add(file.id);
  }
  if (files.length) mkdirSync(`${workspace}/attachments`, { recursive: true, mode: 0o700 });
  for (const message of messages) {
    if (!message.files?.length) continue;
    message.files = message.files.map(file => {
      const bytes = Buffer.from(file.data, "base64");
      if (!bytes.length || bytes.length > MAX_FILE_BYTES || bytes.toString("base64") !== file.data) {
        throw new Error("Invalid attached file size or encoding");
      }
      const extension = file.name.match(/\.[a-zA-Z0-9]{1,16}$/)?.[0] || "";
      const path = `${workspace}/attachments/${file.id}${extension}`;
      writeFileSync(path, bytes, { mode: 0o600, flag: "wx" });
      return { id: file.id, name: file.name, mime_type: file.mime_type, size: bytes.length, path };
    });
  }
}

export function fileReferences(files = []) {
  if (!files.length) return "";
  return "\nAttached files are available unchanged in the workspace. Use tools to inspect them as needed.\n" +
    files.map(file => JSON.stringify({ name: file.name, path: file.path })).join("\n");
}
