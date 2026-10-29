import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, appendFileSync, writeFileSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { SessionManager, CURRENT_SESSION_VERSION } from '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js'
import { prepareSession } from '../../images/pi-base/chat-session.mjs'

function fixture(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'chat-session-'))
  try { fn(dir) } finally { rmSync(dir, { recursive: true, force: true }) }
}

test('native continuation retains tool evidence, compaction boundaries and extension state', () => fixture(dir => {
  const first = prepareSession({ turn_id: 'one', history: [] }, dir, dir)
  const manager = SessionManager.open(first.file)
  const user = manager.appendMessage({ role: 'user', content: 'Do work', timestamp: 1 })
  manager.appendCustomEntry('extension-state', { counter: 7 })
  manager.appendMessage({ role: 'assistant', content: [{ type: 'toolCall', id: 'tool-1', name: 'bash', arguments: { command: 'pwd' } }], timestamp: 2 })
  manager.appendMessage({ role: 'toolResult', toolCallId: 'tool-1', toolName: 'bash', content: [{ type: 'text', text: 'exact-evidence' }], timestamp: 3 })
  const recent = manager.appendMessage({ role: 'user', content: 'Keep this', timestamp: 4 })
  manager.appendCompaction('Older work summarized', recent, 100000)
  first.sync()
  const second = prepareSession({ turn_id: 'two', history: [{ role: 'user', content: 'MUST NOT REIMPORT' }] }, dir, dir)
  assert.equal(second.id, first.id)
  const reopened = SessionManager.open(second.file)
  assert.ok(reopened.getEntry(user))
  assert.ok(reopened.getEntries().some(entry => entry.type === 'custom' && entry.data.counter === 7))
  const context = JSON.stringify(reopened.buildSessionContext())
  assert.match(context, /Older work summarized/)
  assert.match(context, /Keep this/)
  assert.doesNotMatch(context, /exact-evidence|MUST NOT REIMPORT/)
  assert.match(readFileSync(second.file, 'utf8'), /exact-evidence/)
}))

test('legacy import is honest, complete, one-time and never fabricates native tool messages', () => fixture(dir => {
  const session = prepareSession({ turn_id: 'one', history: [
    { role: 'user', content: 'old question' },
    { role: 'assistant', content: 'old answer', steps: [{ kind: 'tool', name: 'bash', args: { command: 'pwd' }, result: 'old evidence' }] },
  ] }, dir, dir)
  const entries = SessionManager.open(session.file).getEntries()
  assert.ok(entries.some(entry => entry.customType === 'nautionette.legacy-import'))
  assert.match(JSON.stringify(entries), /old evidence/)
  assert.equal(entries.filter(entry => entry.type === 'message').length, 1)
  assert.ok(entries.every(entry => entry.message?.role !== 'toolResult'))
}))

test('input journal fences uncertain/consumed IDs; only confirmed untouched inputs may resume', () => fixture(dir => {
  const first = prepareSession({ turn_id: 'one' }, dir, dir)
  first.beginInput('one')
  first.consumed('one', 42)
  first.beginInput('queued')
  first.release('queued')
  const next = prepareSession({ turn_id: 'two' }, dir, dir)
  assert.throws(() => next.beginInput('one'), /already submitted/)
  next.beginInput('queued')
  assert.throws(() => next.beginInput('queued'), /already submitted/)
  next.beginInput('two')
  const journal = readFileSync(join(dir, 'inputs.jsonl'), 'utf8')
  assert.match(journal, /"turnId":"two"/)
  appendFileSync(join(dir, 'inputs.jsonl'), '{broken')
  assert.throws(() => prepareSession({}, dir, dir), /uncertain write/)
}))

test('repair only a torn final record; fail closed on complete corruption or future versions', () => fixture(dir => {
  const first = prepareSession({}, dir, dir)
  appendFileSync(first.file, '{"type":"message"')
  prepareSession({}, dir, dir)
  assert.ok(readFileSync(first.file, 'utf8').endsWith('\n'))
  appendFileSync(first.file, 'not json\n')
  assert.throws(() => prepareSession({}, dir, dir))
  const header = { type: 'session', id: 'future', version: CURRENT_SESSION_VERSION + 1 }
  writeFileSync(first.file, JSON.stringify(header) + '\n')
  assert.throws(() => prepareSession({}, dir, dir), /Unsupported/)
  assert.equal(JSON.parse(readFileSync(first.file, 'utf8')).id, 'future')
}))

test('session paths cannot be symlinks', () => fixture(dir => {
  writeFileSync(join(dir, 'outside'), '{}')
  symlinkSync(join(dir, 'outside'), join(dir, 'session.jsonl'))
  assert.throws(() => prepareSession({}, dir, dir), /regular file/)
}))
