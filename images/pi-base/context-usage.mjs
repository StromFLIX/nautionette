/** Pi normalizes provider usage: input excludes cache reads/writes, output includes reasoning.
 * Use the latest request, never summed billing usage across an agent's tool loop.
 * All-zero usage is Pi's placeholder when an upstream does not report counts.
 */
export function contextUsage(message, model) {
  if (message?.role !== 'assistant') return null;
  const usage = message.usage;
  const keys = ['input', 'output', 'cacheRead', 'cacheWrite'];
  if (!usage || !keys.every((key) => Number.isSafeInteger(usage[key]) && usage[key] >= 0)) return null;
  const input = usage.input + usage.cacheRead + usage.cacheWrite;
  if (!input) return null;
  return {
    model,
    tokens: input + usage.output,
    input_tokens: input,
    output_tokens: usage.output,
    cache_read_tokens: usage.cacheRead,
    cache_write_tokens: usage.cacheWrite,
    source: 'provider',
  };
}
