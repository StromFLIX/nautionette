/** Pin the serving route's advertised budgets, not guessed limits by model name. */
export function modelLimits(env: NodeJS.ProcessEnv = process.env) {
  const metadata = JSON.parse(env.NAUTIONETTE_MODEL_LIMITS || "{}");
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    throw new Error("Invalid pinned model limits");
  }
  const limit = (key: string, fallback: number): number => {
    const value = metadata[key];
    if (value == null) return fallback;
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`Invalid pinned model limit: ${key}`);
    return value;
  };
  // Retain the old conservative budgets only when the catalog has no limits.
  // A separate input ceiling (e.g. Copilot max_prompt_tokens) can be smaller
  // than the combined context window. Pi has one window, so use the safer bound;
  // its ordinary reserve and request-time output clamping still apply.
  const contextWindow = Math.min(limit("context_window", 200000), limit("max_input_tokens", Infinity));
  return { contextWindow, maxTokens: Math.min(limit("max_output_tokens", 8192), contextWindow) };
}
