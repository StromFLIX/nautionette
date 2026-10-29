// Bound the canonical model projection, not the native evidence or just the UI.
// A newest assistant/tool batch cannot be split by compaction, so one unbounded
// result can otherwise survive every compaction and immediately overflow again.
export const MAX_TOOL_CONTEXT_CHARS = 32000;
export const MAX_TOOL_CONTEXT_LINES = 1000;

export function toolContextEdits(projectedEntries) {
  const edits = [];
  for (const entry of projectedEntries) {
    if (entry.sourceEntry.type !== 'message') continue;
    for (const message of entry.messages) {
      if (message.role !== 'toolResult' || !Array.isArray(message.content)) continue;
      const text = message.content.filter(part => part.type === 'text').map(part => part.text).join('\n');
      if (text.length <= MAX_TOOL_CONTEXT_CHARS && text.split('\n').length <= MAX_TOOL_CONTEXT_LINES) continue;
      const reference = JSON.stringify(entry.sourceEntry.id);
      const notice = `[Large tool result: preview only. Original evidence retained. Read it with chat_history(entry_id=${reference}, offset=0), then follow next_offset; do not repeat the original tool to recover its output.]\n`;
      const separator = '\n\n[... middle omitted ...]\n\n';
      const chars = Math.floor((MAX_TOOL_CONTEXT_CHARS - notice.length - separator.length) / 2);
      const lines = Math.floor((MAX_TOOL_CONTEXT_LINES - 6) / 2);
      const head = text.slice(0, chars).split('\n').slice(0, lines).join('\n');
      const tail = text.slice(-chars).split('\n').slice(-lines).join('\n');
      let replaced = false;
      const content = message.content.flatMap(part => {
        if (part.type !== 'text') return [part];
        if (replaced) return [];
        replaced = true;
        return [{ type: 'text', text: notice + head + separator + tail }];
      });
      edits.push({ type: 'context_edit', targetId: entry.sourceEntry.id, replacement: { content } });
    }
  }
  return edits;
}
