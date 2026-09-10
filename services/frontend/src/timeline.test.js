import test from 'node:test'
import assert from 'node:assert/strict'
import { foldEvent, groupToolCalls, summarizeToolCalls } from './timeline.js'

const text = (value) => ({ kind: 'text', text: value })
const tool = (name, ok = true) => ({ kind: 'tool', name, ok })

test('plain responses have no tool disclosure', () => {
  const parts = [text('Just an answer')]
  assert.equal(groupToolCalls(parts), parts)
  assert.deepEqual(groupToolCalls([]), [])
})

test('narration separates consecutive tool groups while preserving the timeline', () => {
  const parts = [text('Looking into it'), tool('bash'), tool('read'), text('Now editing'), tool('edit'), text('All done')]
  const original = structuredClone(parts)
  const grouped = groupToolCalls(parts)
  assert.deepEqual(grouped, [
    parts[0],
    { kind: 'tool-group', id: 'tool-group-1', steps: parts.slice(1, 3) },
    parts[3],
    { kind: 'tool-group', id: 'tool-group-4', steps: [parts[4]] },
    parts[5]
  ])
  assert.deepEqual(parts, original)
  assert.equal(grouped[1].steps[0], parts[1])
  assert.equal(grouped[2], parts[3])
})

test('groups have unique, stable keys as calls and narration arrive, even without tool IDs', () => {
  const parts = [tool('bash')]
  const first = groupToolCalls(parts)[0]
  assert.deepEqual(first.steps, parts)
  parts.push(tool('read'))
  assert.equal(groupToolCalls(parts)[0].id, first.id)
  assert.deepEqual(groupToolCalls(parts)[0].steps, parts)
  parts.push(text('Next step'))
  assert.equal(groupToolCalls(parts)[0].id, first.id)
  parts.push(tool('read'))
  const updated = groupToolCalls(parts)
  assert.equal(updated.length, 3)
  assert.equal(updated[0].id, first.id)
  assert.notEqual(updated[2].id, first.id)
  assert.deepEqual(updated[0].steps, parts.slice(0, 2))
  assert.deepEqual(updated[2].steps, [parts[3]])
  parts.push(tool('edit'), text('Done'))
  assert.deepEqual(groupToolCalls(parts).filter(part => part.kind === 'tool-group').map(part => part.id), [first.id, updated[2].id])
})

test('empty and whitespace-only deltas do not split tool groups or enter disclosures', () => {
  const parts = [text(''), tool('read'), text(' \n\t'), tool('read'), text('Done')]
  const grouped = groupToolCalls(parts)
  assert.deepEqual(grouped, [
    { kind: 'tool-group', id: 'tool-group-1', steps: [parts[1], parts[3]] },
    parts[4]
  ])
})

test('streamed narration stays outside tools when later calls start and finish', () => {
  const steps = []
  foldEvent(steps, { type: 'tool', id: 'one', name: 'bash' })
  foldEvent(steps, { type: 'delta', text: 'Next' })
  const first = groupToolCalls(steps)[0]
  foldEvent(steps, { type: 'delta', text: ' file' })
  foldEvent(steps, { type: 'tool', id: 'two', name: 'read' })
  foldEvent(steps, { type: 'tool_done', id: 'one', result: 'Done' })
  const grouped = groupToolCalls(steps)
  assert.equal(grouped[0].id, first.id)
  assert.deepEqual(grouped.map(part => part.kind), ['tool-group', 'text', 'tool-group'])
  assert.equal(grouped[1].text, 'Next file')
  assert.equal(summarizeToolCalls(grouped[0].steps).pending, false)
  assert.equal(summarizeToolCalls(grouped[2].steps).pending, true)
  // Persisted timelines must render in the same order as the live stream.
  assert.deepEqual(groupToolCalls(JSON.parse(JSON.stringify(steps))), grouped)
})

test('tool summary counts invocations with singular, plural and mixed labels', () => {
  for (const [steps, label] of [
    [[], 'Ran 0 tool calls'],
    [[tool('bash')], 'Ran 1 shell command'],
    [[tool('bash'), tool('powershell')], 'Ran 2 shell commands'],
    [[tool('read')], 'Ran 1 tool call'],
    [[tool('read'), tool('read'), tool('coolify_get_logs')], 'Ran 3 tool calls'],
    [[tool('bash'), tool('edit')], 'Ran 1 shell command and 1 other tool call'],
    [[...Array.from({ length: 20 }, () => tool('bash')), tool('read'), text('Checking'), tool('edit')], 'Ran 20 shell commands and 2 other tool calls']
  ]) assert.equal(summarizeToolCalls(steps).label, label)
})

test('streamed completion updates status without counting the call twice', () => {
  const steps = []
  foldEvent(steps, { type: 'tool', id: 'one', name: 'bash', args: { command: 'ls' } })
  assert.deepEqual(summarizeToolCalls(steps), { label: 'Ran 1 shell command', failed: 0, pending: true })
  foldEvent(steps, { type: 'tool_done', id: 'one', error: true, result: 'Command failed' })
  assert.deepEqual(summarizeToolCalls(steps), { label: 'Ran 1 shell command', failed: 1, pending: false })
  foldEvent(steps, { type: 'tool', id: 'two', name: 'read' })
  assert.deepEqual(summarizeToolCalls(steps), { label: 'Ran 1 shell command and 1 other tool call', failed: 1, pending: true })
})
