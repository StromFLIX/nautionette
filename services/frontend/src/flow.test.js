import { test } from 'node:test'
import assert from 'node:assert/strict'
import { compareGraphs, duration, layoutGraph } from './flow.js'

const graph = (nodes, edges = []) => ({ nodes, edges, warnings: [], mode: 'definition' })

test('draft changes keep removed steps and do not mark shifted IDs as changes', () => {
  const before = graph([
    { id: 'a', kind: 'activity', label: 'fetch', code: 'fetch(1)' },
    { id: 'b', kind: 'activity', label: 'save', code: 'save()' },
    { id: 'c', kind: 'activity', label: 'notify', code: 'notify()' }
  ], [{ id: 'ab', source: 'a', target: 'b' }])
  const after = graph([
    { id: 'a', kind: 'activity', label: 'validate', code: 'validate()' },
    { id: 'b', kind: 'activity', label: 'fetch', code: 'fetch(2)' },
    { id: 'c', kind: 'activity', label: 'save', code: 'save()' }
  ], [{ id: 'bc', source: 'b', target: 'c' }])
  const result = compareGraphs(before, after)
  assert.deepEqual(result.nodes.map((node) => node.change), ['added', 'changed', null, 'removed'])
  assert.equal(result.nodes[1].before_code, 'fetch(1)')
  assert.equal(result.edges.length, 1)
  assert.equal(result.edges[0].change, null)
})

test('a new flow marks every step as added', () => {
  assert.equal(compareGraphs(null, graph([{ id: 'a', kind: 'workflow', label: 'demo' }])).nodes[0].change, 'added')
})

test('edge changes are preserved even if node content did not change', () => {
  const nodes = [{ id: 'a', kind: 'activity', label: 'first' }, { id: 'b', kind: 'activity', label: 'second' }]
  const result = compareGraphs(graph(nodes, [{ id: 'ab', source: 'a', target: 'b', label: 'Yes' }]), graph(nodes, [{ id: 'ab', source: 'a', target: 'b', label: 'No' }]))
  assert.deepEqual(result.edges.map((edge) => edge.change), ['added', 'removed'])
})

test('automatic layout handles branches and cycles in both orientations', () => {
  const source = graph([{ id: 'a' }, { id: 'b' }, { id: 'c' }], [
    { id: 'ab', source: 'a', target: 'b' }, { id: 'ac', source: 'a', target: 'c' }, { id: 'ba', source: 'b', target: 'a' }
  ])
  for (const direction of ['TB', 'LR']) {
    const layout = layoutGraph(source, direction)
    assert.equal(layout.nodes.length, 3)
    assert.ok(layout.nodes.every((node) => Number.isFinite(node.position.x) && Number.isFinite(node.position.y)))
    assert.notDeepEqual(layout.nodes[1].position, layout.nodes[2].position)
  }
})

test('durations retain milliseconds and do not count time after completion', () => {
  assert.equal(duration('2026-09-07T12:00:00Z', '2026-09-07T12:00:00.172Z'), '172ms')
  assert.equal(duration('2026-09-07T12:00:00Z', '2026-09-07T12:03:38Z'), '3m 38s')
  assert.equal(duration('invalid'), '')
})