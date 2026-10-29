import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { SessionManager } from '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js'
import { prepareSession } from '../../images/pi-base/chat-session.mjs'
import { toolContextEdits, MAX_TOOL_CONTEXT_CHARS, MAX_TOOL_CONTEXT_LINES } from '../../images/pi-base/tool-context.mjs'

function projected(content, overrides = {}) {
  return { sourceEntry: { type: 'message', id: 'original' }, messages: [{ role: 'toolResult', content, ...overrides }] }
}

for (const text of ['small', 'x'.repeat(MAX_TOOL_CONTEXT_CHARS), '\n'.repeat(MAX_TOOL_CONTEXT_LINES - 1)]) {
  test(`in-budget tool text is unchanged (${text.length} characters)`, () => {
    assert.deepEqual(toolContextEdits([projected([{ type: 'text', text }])]), [])
  })
}

for (const text of ['HEAD' + 'x'.repeat(2_000_000) + 'TAIL', 'HEAD\n' + 'x\n'.repeat(10000) + 'TAIL']) {
  test(`oversized ${text.includes('\n') ? 'multiline' : 'single-line'} text keeps head/tail and exact history reference`, () => {
    const image = { type: 'image', data: 'untouched', mimeType: 'image/png' }
    const entry = projected([{ type: 'text', text }, image], { isError: true, toolCallId: 'original-call' })
    const before = JSON.stringify(entry)
    const [edit] = toolContextEdits([entry])
    assert.equal(edit.type, 'context_edit')
    assert.equal(edit.targetId, 'original')
    const preview = edit.replacement.content[0].text
    assert.ok(preview.length <= MAX_TOOL_CONTEXT_CHARS)
    assert.ok(preview.split('\n').length <= MAX_TOOL_CONTEXT_LINES)
    assert.match(preview, /chat_history\(entry_id="original", offset=0\)/)
    assert.match(preview, /HEAD/)
    assert.match(preview, /omitted/)
    assert.ok(preview.endsWith('TAIL'))
    assert.deepEqual(edit.replacement.content[1], image)
    assert.equal(JSON.stringify(entry), before)
    assert.deepEqual(toolContextEdits([projected(edit.replacement.content)]), [])
  })
}

test('budget is shared across text blocks; non-tool messages and omitted entries are untouched', () => {
  const blocks = Array.from({ length: 10 }, () => ({ type: 'text', text: 'x'.repeat(5000) }))
  const edits = toolContextEdits([projected(blocks), projected(blocks, { role: 'user' }),
    { sourceEntry: { type: 'message', id: 'omitted' }, messages: [] }])
  assert.equal(edits.length, 1)
  assert.equal(edits[0].replacement.content.length, 1)
  assert.ok(edits[0].replacement.content[0].text.length <= MAX_TOOL_CONTEXT_CHARS)
})

test('reopening repairs a previously compacted huge batch append-only and idempotently', () => {
  const dir = mkdtempSync(join(tmpdir(), 'tool-context-'))
  try {
    const session = prepareSession({}, dir, dir)
    const manager = SessionManager.open(session.file)
    manager.appendMessage({ role: 'user', content: 'Overview please', timestamp: 1 })
    const call = manager.appendMessage({ role: 'assistant', content: [{ type: 'toolCall', id: 'one', name: 'large', arguments: {} }], timestamp: 2 })
    const evidence = 'HEAD' + 'x'.repeat(2_000_000) + 'TAIL'
    const result = manager.appendMessage({ role: 'toolResult', toolCallId: 'one', toolName: 'large', isError: true,
      content: [{ type: 'text', text: evidence }], timestamp: 3 })
    manager.appendCompaction('User wants an overview; tools already completed.', call, 500000)
    const original = readFileSync(session.file, 'utf8')
    assert.ok(JSON.stringify(manager.buildSessionContext()).length > 2_000_000)
    prepareSession({}, dir, dir)
    const repaired = readFileSync(session.file, 'utf8')
    assert.ok(repaired.startsWith(original), 'No original entry or compaction may be rewritten')
    const reopened = SessionManager.open(session.file)
    const message = reopened.buildSessionContext().messages.find(m => m.role === 'toolResult')
    assert.equal(message.isError, true)
    assert.equal(message.toolCallId, 'one')
    assert.ok(message.content[0].text.length <= MAX_TOOL_CONTEXT_CHARS)
    assert.equal(reopened.getEntry(result).message.content[0].text, evidence)
    prepareSession({}, dir, dir)
    assert.equal(readFileSync(session.file, 'utf8'), repaired)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
