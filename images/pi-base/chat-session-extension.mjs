// Loaded explicitly for durable chats, regardless of the selected agent set.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { syncFile } from './chat-session.mjs';
import { toolContextEdits } from './tool-context.mjs';

export default function (pi) {
  pi.on('message_start', (event, ctx) => {
    if (event.message.role !== 'user') return;
    const mapped = new Set(ctx.sessionManager.getEntries()
      .filter(entry => entry.type === 'custom' && entry.customType === 'nautionette.input')
      .map(entry => entry.data?.inputId));
    const journal = readFileSync(join(dirname(ctx.sessionManager.getSessionFile()), 'inputs.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
    const states = new Map(journal.map(entry => [entry.id, entry]));
    const pending = [...states.values()].find(entry => entry.turnId === process.env.NAUTIONETTE_TURN_ID && entry.state === 'submitted' && !mapped.has(entry.id));
    if (pending) pi.appendEntry('nautionette.input', { inputId: pending.id, userTimestamp: event.message.timestamp });
  });
  function checkpoint(ctx) {
    const branch = ctx.sessionManager.getBranch();
    const user = branch.findLast(entry => entry.type === 'message' && entry.message.role === 'user');
    const binding = branch.findLast(entry => entry.type === 'custom' && entry.customType === 'nautionette.input');
    if (user && binding?.data?.userTimestamp === user.message.timestamp &&
        !branch.some(entry => entry.type === 'custom' && entry.customType === 'nautionette.input-entry' && entry.data?.inputId === binding.data.inputId)) {
      pi.appendEntry('nautionette.input-entry', { inputId: binding.data.inputId, entryId: user.id });
    }
    syncFile(ctx.sessionManager.getSessionFile());
  }
  // A finalized user/assistant/result has reached SessionManager before these
  // boundaries. Flush before external tools, subsequent model calls and exit.
  pi.on('tool_call', (_event, ctx) => { checkpoint(ctx); });
  pi.on('turn_end', (event, ctx) => {
    checkpoint(ctx);
    // Pi applies these append-only edits before its next threshold check. Keep
    // raw results for chat_history, including errors and tool-call relationships.
    return { entries: toolContextEdits(event.context.contextEntries) };
  });
  pi.on('session_shutdown', (_event, ctx) => { checkpoint(ctx); });
  // A chat owns one exact session. Session-replacing package commands must not
  // silently move continuation into an ephemeral file.
  pi.on('session_before_switch', () => ({ cancel: true }));
  pi.on('session_before_fork', () => ({ cancel: true }));
  // Model switches change the request projection, never the original bytes or
  // native history. Text-only routes must not receive old vision blocks.
  pi.on('context', (event, ctx) => {
    checkpoint(ctx);
    if (process.env.NAUTIONETTE_MODEL_IMAGES !== 'false') return;
    return { messages: event.messages.map(message => {
      if (!Array.isArray(message.content) || !message.content.some(part => part.type === 'image')) return message;
      return { ...message, content: [...message.content.filter(part => part.type !== 'image'),
        { type: 'text', text: '[Historical image input omitted for this text-only route; original attachment files remain available.]' }] };
    }) };
  });
  pi.registerTool({
    name: 'chat_history',
    description: 'Read older native session evidence, including pre-compaction tool commands/results. Read-only, bounded; never executes old commands. Use before repeating uncertain actions. Returns entries before a cursor (exclusive), newest matches first.',
    parameters: { type: 'object', properties: {
      query: { type: 'string', description: 'Optional case-insensitive text search' },
      before: { type: 'string', description: 'Optional native entry ID cursor' },
      entry_id: { type: 'string', description: 'Read one exact entry; combine with offset for a large result' },
      offset: { type: 'integer', minimum: 0, description: 'Character offset within one exact entry' },
    }, additionalProperties: false },
    async execute(_id, args, _signal, _update, ctx) {
      let entries = ctx.sessionManager.getEntries();
      if (args.entry_id) {
        entries = entries.filter(entry => entry.id === args.entry_id);
        if (!entries.length) throw new Error('Unknown history entry');
      }
      if (args.offset && !args.entry_id) throw new Error('offset requires entry_id');
      if (args.before) {
        const index = entries.findIndex(entry => entry.id === args.before);
        if (index < 0) throw new Error('Unknown history cursor');
        entries = entries.slice(0, index);
      }
      const matches = [];
      let size = 0;
      for (const entry of entries.toReversed()) {
        if (entry.type === 'message' && (entry.message.role === 'system' ||
            (!args.entry_id && entry.message.role === 'toolResult' && entry.message.toolName === 'chat_history'))) continue;
        const text = JSON.stringify(entry);
        if (args.query && !text.toLowerCase().includes(args.query.toLowerCase())) continue;
        const offset = args.entry_id ? (args.offset || 0) : 0;
        const excerpt = text.slice(offset, offset + 12000);
        if (matches.length && size + excerpt.length > 20000) break;
        matches.push({ id: entry.id, entry: excerpt, offset, truncated: offset + excerpt.length < text.length,
          next_offset: offset + excerpt.length < text.length ? offset + excerpt.length : null });
        size += excerpt.length;
        if (matches.length >= 20) break;
      }
      return { content: [{ type: 'text', text: JSON.stringify({ entries: matches, before: matches.at(-1)?.id }) }], details: undefined };
    },
  });
}
