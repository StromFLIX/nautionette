// Durable native Pi state, separate from Pi home/auth and the ephemeral workspace.
import { appendFileSync, closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { SessionManager, CURRENT_SESSION_VERSION } from '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js';
import { fileReferences } from './chat-files.mjs';
import { toolContextEdits } from './tool-context.mjs';

export function syncFile(path) {
  const fd = openSync(path, 'r');
  try { fsyncSync(fd); } finally { closeSync(fd); }
}

export function prepareSession(job, workspace, directory = '/chat-session') {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const file = `${directory}/session.jsonl`;
  if (existsSync(file)) {
    if (!lstatSync(file).isFile()) throw new Error('Native chat session must be a regular file');
    const raw = readFileSync(file, 'utf8');
    const lines = raw.split('\n');
    const header = JSON.parse(lines[0]);
    if (header.type !== 'session' || typeof header.id !== 'string' || !header.id ||
        !Number.isInteger(header.version ?? 1) || (header.version ?? 1) < 1 || header.version > CURRENT_SESSION_VERSION) {
      throw new Error('Unsupported or invalid native Pi session; original retained');
    }
    // Never let Pi silently skip corrupt complete records. Preserve a torn final
    // write for diagnosis and only repair that incomplete trailing record.
    const ids = new Set();
    function validate(entry) {
      if (!entry || typeof entry.type !== 'string' || entry.type === 'session') throw new Error('Invalid native session entry; original retained');
      if (header.version >= 2) {
        if (typeof entry.id !== 'string' || !entry.id || ids.has(entry.id) ||
            (entry.parentId !== null && !ids.has(entry.parentId))) throw new Error('Broken native session tree; original retained');
        ids.add(entry.id);
      }
      if (entry.type === 'message' && typeof entry.message?.role !== 'string') throw new Error('Invalid native session message; original retained');
    }
    for (const line of lines.slice(1, -1)) if (line.trim()) validate(JSON.parse(line));
    if (lines.at(-1)) {
      let trailing;
      try { trailing = JSON.parse(lines.at(-1)); }
      catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        writeFileSync(`${directory}/torn-${Date.now()}.jsonl`, lines.at(-1), { mode: 0o600, flag: 'wx', flush: true });
        writeFileSync(file, raw.slice(0, raw.lastIndexOf('\n') + 1), { mode: 0o600 });
      }
      if (trailing !== undefined) { validate(trailing); appendFileSync(file, '\n'); }
      syncFile(file);
      syncFile(directory);
    }
  } else {
    // Import once with the public SDK. These are legacy display records, NOT
    // fabricated native tool calls or a recovered native compaction checkpoint.
    const manager = SessionManager.inMemory(workspace);
    if (job.history?.length) {
      manager.appendCustomMessageEntry('nautionette.legacy-import',
        'Legacy chat imported from the stored UI transcript. Tool evidence may be truncated; native compaction, reasoning and original tool messages were not retained. Never infer that an interrupted action is safe to repeat.', true);
      for (const message of job.history) {
        const content = [{ type: 'text', text: (message.content || '') + fileReferences(message.files) }, ...(message.images || [])];
        if (message.role === 'user') manager.appendMessage({ role: 'user', content, timestamp: Date.now() });
        else manager.appendCustomMessageEntry('nautionette.legacy-assistant', content, true);
        if (message.steps?.length) manager.appendCustomMessageEntry('nautionette.legacy-tools',
          'Legacy execution timeline (display evidence, possibly incomplete):\n' + JSON.stringify(message.steps), true);
      }
    }
    const temp = `${directory}/session.tmp`;
    writeFileSync(temp, [manager.getHeader(), ...manager.getEntries()].map(entry => JSON.stringify(entry)).join('\n') + '\n', { mode: 0o600 });
    syncFile(temp);
    renameSync(temp, file);
    syncFile(directory);
  }
  // Repair already-oversized chats before Pi's initial compaction check too.
  // Public SDK context edits preserve the original bytes and active branch;
  // repeated opens do not add edits when the projection is already bounded.
  const manager = SessionManager.open(file);
  const edits = toolContextEdits(manager.buildSessionProjection().entries);
  for (const edit of edits) manager.appendContextEdit(edit.targetId, edit.replacement);
  if (edits.length) syncFile(file);

  const journal = `${directory}/inputs.jsonl`;
  const states = new Map();
  if (existsSync(journal)) {
    if (!lstatSync(journal).isFile()) throw new Error('Invalid chat input journal');
    for (const line of readFileSync(journal, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      try { const entry = JSON.parse(line); states.set(entry.id, entry.state); }
      catch { throw new Error('Chat input journal has an uncertain write; verify previous inputs before continuing'); }
    }
  }
  function record(entry) {
    appendFileSync(journal, JSON.stringify({ ...entry, turnId: job.turn_id, at: Date.now() }) + '\n', { mode: 0o600 });
    syncFile(journal);
    syncFile(directory);
    states.set(entry.id, entry.state);
  }
  return {
    file,
    id: JSON.parse(readFileSync(file, 'utf8').split('\n', 1)[0]).id,
    beginInput(id) {
      if (states.has(id) && states.get(id) !== 'released') throw new Error('This input was already submitted to Pi. Its outcome is uncertain; verify the previous turn and send a new message instead of replaying it.');
      record({ id, state: 'submitted' });
    },
    consumed(id, timestamp) { record({ id, state: 'consumed', userTimestamp: timestamp }); },
    release(id) { if (states.get(id) === 'submitted') record({ id, state: 'released' }); },
    sync() { syncFile(file); },
  };
}
