// The context window is a model limit, not the transcript's character budget.
export function modelContextWindow (catalog, modelId) {
  const window = catalog.models?.find((model) => model.id === (modelId || catalog.default_model))?.context_length
  return Number.isSafeInteger(window) && window > 0 ? window : null
}

export function latestContext (messages, activeTurn, modelId) {
  // A new container gets a fresh prompt. Don't show the previous turn's usage while waiting.
  const context = activeTurn
    ? activeTurn.context
    : messages.findLast((message) => message.role === 'assistant')?.meta?.context
  if (context?.source !== 'provider' || context.model !== modelId ||
      !Number.isSafeInteger(context.tokens) || context.tokens <= 0) return null
  return context
}

export function contextMeter (context, window) {
  const tokens = context?.tokens ?? null
  const percent = tokens !== null && window ? Math.round(tokens / window * 100) : null
  const count = (value) => value.toLocaleString()
  const label = percent !== null ? `${percent}% context` : tokens !== null ? `${count(tokens)} tokens` : 'Context unknown'
  const detail = tokens !== null
    ? `${count(tokens)}${window ? ` of ${count(window)}` : ''} tokens — last model response. ` +
      'Includes system instructions, tools, cached input and output; excludes unsent text. ' +
      'Each chat turn starts a fresh context from trimmed history.'
    : 'Token usage is unavailable until the model reports it. No character-based estimate is used.'
  return {
    label,
    title: detail + (window ? ` Model window: ${count(window)} tokens.` : ' Model context window is unknown.'),
    percent,
    width: Math.min(100, percent ?? 0)
  }
}
