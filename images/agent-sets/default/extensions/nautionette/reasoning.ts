// The backend pins provider-advertised capabilities and the user's selection
// into each durable job. Do not infer capabilities from model names here.
const LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
type Level = typeof LEVELS[number];
const EFFORTS = ["none", ...LEVELS.slice(1)];

type ReasoningConfig = {
  effort: string | null;
  format: "openrouter" | "openai";
  reasoning: boolean;
  thinkingLevelMap: Partial<Record<Level, string | null>>;
  level: Level;
};

export function reasoningConfig(env: NodeJS.ProcessEnv = process.env): ReasoningConfig {
  const metadata = JSON.parse(env.NAUTIONETTE_MODEL_REASONING || "{}");
  const efforts = Array.isArray(metadata.efforts)
    ? EFFORTS.filter((effort) => metadata.efforts.includes(effort)) : [];
  const effort = env.NAUTIONETTE_REASONING_EFFORT || null;
  if (effort !== null && !efforts.includes(effort)) {
    throw new Error("Selected reasoning effort is not supported by the pinned model capabilities");
  }
  const format = metadata.format === "openrouter" ? "openrouter" : "openai";
  const thinkingLevelMap = Object.fromEntries(LEVELS.map((level) => [
    level, level === "off" ? "none" : efforts.includes(level) ? level : null,
  ]));
  return {
    effort,
    format,
    reasoning: metadata.supported === true || efforts.length > 0,
    thinkingLevelMap,
    level: (effort === null || effort === "none" ? "off" : effort) as Level,
  };
}

export function reasoningPayload(payload: Record<string, any>, api: string, config: ReasoningConfig) {
  const next = { ...payload };
  // Pi's off/default behavior can emit effort=none or enabled=false. Neither is
  // a provider default, and always-thinking models can reject them. Likewise,
  // Pi must not clamp max/xhigh or silently substitute a different effort.
  delete next.reasoning_effort;
  if (config.effort === null) {
    delete next.reasoning;
  } else if (api === "openai-responses" || config.format === "openrouter") {
    next.reasoning = { ...(config.effort === "none" ? {} : next.reasoning), effort: config.effort };
    delete next.reasoning.enabled;
  } else {
    delete next.reasoning;
    next.reasoning_effort = config.effort;
  }
  return next;
}
