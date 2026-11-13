import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { SessionManager } from '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js'
import { prepareSession } from '../../images/pi-base/chat-session.mjs'

function fixture(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'workflow-context-'))
  try { fn(dir) } finally { rmSync(dir, { recursive: true, force: true }) }
}
const resultMessage = (id = 'result-1', content = 'Latest workflow result: 42') => ({
  id, source_id: id, content,
  origin: { kind: 'workflow', relation: 'result', workflow: 'digest', workflow_id: 'digest-run', trigger: 'schedule' },
})

test('external results enter new and resumed native sessions with provenance and no fabricated tool calls', () => fixture(dir => {
  const first = prepareSession({ external_messages: [resultMessage()] }, dir, dir)
  assert.deepEqual(first.contextMessageIds, ['result-1'])
  let manager = SessionManager.open(first.file)
  let context = JSON.stringify(manager.buildSessionContext())
  assert.match(context, /Latest workflow result: 42/)
  assert.match(context, /not a new request from the human/)
  assert.match(context, /digest-run/)
  assert.ok(manager.getEntries().every(entry => entry.message?.role !== 'toolResult'))
  manager.appendMessage({ role: 'user', content: 'Explain it', timestamp: 1 })
  manager.appendCustomMessageEntry('test-answer', 'An explanation', true)
  prepareSession({ external_messages: [resultMessage('result-2', 'New result: 99')] }, dir, dir)
  manager = SessionManager.open(first.file)
  context = JSON.stringify(manager.buildSessionContext())
  assert.match(context, /New result: 99/)
  assert.equal(manager.getEntries().filter(entry => entry.customType === 'nautionette.workflow-result').length, 2)
}))

test('lost acknowledgement and compaction never reimport consumed results, including branched message IDs', () => fixture(dir => {
  const first = prepareSession({ external_messages: [resultMessage()] }, dir, dir)
  const manager = SessionManager.open(first.file)
  const recent = manager.appendMessage({ role: 'user', content: 'Keep recent', timestamp: 1 })
  manager.appendCompaction('Older result summarized', recent, 100000)
  first.sync()
  const retried = prepareSession({ external_messages: [{ ...resultMessage(), id: 'copied-branch-message' }] }, dir, dir)
  assert.deepEqual(retried.contextMessageIds, ['copied-branch-message'])
  const reopened = SessionManager.open(first.file)
  assert.equal(reopened.getEntries().filter(entry => entry.customType === 'nautionette.workflow-result').length, 1)
  assert.doesNotMatch(JSON.stringify(reopened.buildSessionContext()), /Latest workflow result: 42/)
  assert.match(JSON.stringify(reopened.buildSessionContext()), /Older result summarized/)
}))

test('large workflow results retain exact evidence but have bounded append-only model projections', () => fixture(dir => {
  const content = 'LARGE-RESULT-' + 'X'.repeat(100000) + '-END-EVIDENCE'
  const session = prepareSession({ external_messages: [resultMessage('large', content)] }, dir, dir)
  const manager = SessionManager.open(session.file)
  const entry = manager.getEntries().find(entry => entry.customType === 'nautionette.workflow-result')
  assert.ok(entry.content.endsWith(content))
  const context = JSON.stringify(manager.buildSessionContext())
  assert.ok(context.length < 18000)
  assert.match(context, /chat_history/)
  assert.doesNotMatch(context, /END-EVIDENCE/)
  const before = readFileSync(session.file, 'utf8')
  prepareSession({ external_messages: [resultMessage('large', content)] }, dir, dir)
  assert.equal(readFileSync(session.file, 'utf8'), before)
}))

test('a crash before the large-result context edit is repaired without reimport', () => fixture(dir => {
  const session = prepareSession({}, dir, dir)
  const manager = SessionManager.open(session.file)
  manager.appendCustomMessageEntry('nautionette.workflow-result', 'X'.repeat(100000), true, { sourceId: 'old' })
  session.sync()
  prepareSession({}, dir, dir)
  const reopened = SessionManager.open(session.file)
  assert.ok(JSON.stringify(reopened.buildSessionContext()).length < 18000)
  assert.equal(reopened.getEntries().filter(entry => entry.customType === 'nautionette.workflow-result').length, 1)
}))
