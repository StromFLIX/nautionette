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

test('one group retains call order and intervening narration, leaving intro and answer outside', () => {
  const parts = [text('Looking into it'), tool('bash'), text('Now reading'), tool('read'), text('All done')]
  const grouped = groupToolCalls(parts)
  assert.deepEqual(grouped, [
    parts[0],
    { kind: 'tool-group', id: 'tool-group', steps: parts.slice(1, 4) },
    parts[4]
  ])
  assert.equal(parts.length, 5)
  assert.equal(grouped[1].steps[0], parts[1])
})

test('single and tool-only timelines are grouped with a stable key as calls arrive', () => {
  const parts = [tool('bash')]
  const first = groupToolCalls(parts)[0]
  assert.deepEqual(first.steps, parts)
  parts.push(text('Next step'), tool('read'))
  const updated = groupToolCalls(parts)
  assert.equal(updated.length, 1)
  assert.equal(updated[0].id, first.id)
  assert.deepEqual(updated[0].steps, parts)
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
