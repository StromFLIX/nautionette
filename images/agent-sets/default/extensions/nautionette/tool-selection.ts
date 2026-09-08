/** Lossless tool selection. Malformed configuration must never enable every tool. */
export function allowedTools(json: string | undefined, legacy = ""): Set<string> | null {
  if (json === undefined) {
    const names = legacy.split(",").map((name) => name.trim()).filter(Boolean);
    return names.length ? new Set(names) : null;
  }
  let value: unknown;
  try { value = JSON.parse(json); }
  catch { throw new Error("Invalid MCP tool selection: expected null or a JSON array of names"); }
  if (value === null) return null;
  if (!Array.isArray(value) || value.some((name) => typeof name !== "string" || !name.trim())) {
    throw new Error("Invalid MCP tool selection: expected null or a JSON array of names");
  }
  return new Set(value);
}
