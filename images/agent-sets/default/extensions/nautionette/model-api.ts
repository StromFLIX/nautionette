/** Select an upstream-supported wire format, not one API for an entire provider. */
export type ModelApi = "openai-completions" | "openai-responses";

export function fallbackModelApi(model: string): ModelApi {
  // Older Copilot catalogs omit supported_endpoints. GPT-5+ needs Responses;
  // Claude, Gemini and older GPT models use Chat Completions. Do not send their
  // images through Copilot's text-only Responses compatibility conversion.
  const version = /^copilot\/gpt-(\d+)(?:[.\-]|$)/.exec(model);
  return version && Number(version[1]) >= 5 ? "openai-responses" : "openai-completions";
}

export async function modelApi(model: string, gateway: string): Promise<ModelApi> {
  // Chat jobs pin the API whose capabilities the backend checked. Workflows and
  // older jobs still discover it below. Never silently substitute a different API.
  const selected = process.env.NAUTIONETTE_MODEL_API;
  if (selected) {
    if (selected === "openai-completions" || selected === "openai-responses") return selected;
    throw new Error("Invalid selected model API");
  }
  if (!model.startsWith("copilot/")) return "openai-completions";

  let endpoints: unknown;
  try {
    // Ask through the existing authenticated gateway route, never a provider key.
    const response = await fetch(`${gateway}/_nautionette/integrations/copilot/models`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    endpoints = Array.isArray(payload?.data)
      ? payload.data.find((entry: any) => entry?.id === model.slice("copilot/".length))?.supported_endpoints
      : undefined;
  } catch {
    // Keep known models usable during a catalog outage. Never log response bodies
    // or credentials; this diagnostic contains only the selected model/API.
    console.error(`[nautionette] model catalog unavailable; using ${fallbackModelApi(model)} for ${model}`);
  }

  if (Array.isArray(endpoints) && endpoints.length) {
    if (endpoints.includes("/responses") || endpoints.includes("/v1/responses")) return "openai-responses";
    if (endpoints.includes("/chat/completions") || endpoints.includes("/v1/chat/completions")) {
      return "openai-completions";
    }
    throw new Error(`No supported OpenAI-compatible API advertised for ${model}`);
  }
  return fallbackModelApi(model);
}
