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

test('nested loops enclose their entire body in both directions', () => {
  const source = graph([
    { id: 'root', kind: 'workflow' }, { id: 'outer', kind: 'loop' },
    { id: 'inner', kind: 'loop', parent_id: 'outer' },
    { id: 'call', kind: 'activity', parent_id: 'inner', details: [{ label: 'Tool', value: 'fetch' }] },
    { id: 'save', kind: 'activity', parent_id: 'outer' }, { id: 'end', kind: 'return' }
  ], [
    { id: 'start', source: 'root', target: 'outer' },
    { id: 'each', source: 'outer', target: 'inner' },
    { id: 'part', source: 'inner', target: 'call' },
    { id: 'next', source: 'call', target: 'inner', role: 'repeat' },
    { id: 'saved', source: 'inner', target: 'save' },
    { id: 'repeat', source: 'save', target: 'outer', role: 'repeat' },
    { id: 'done', source: 'outer', target: 'end' }
  ])
  for (const direction of ['TB', 'LR']) {
    const layout = layoutGraph(source, direction)
    for (const node of layout.nodes.filter((item) => item.parentNode)) {
      const parent = layout.nodes.find((item) => item.id === node.parentNode)
      assert.ok(layout.nodes.indexOf(parent) < layout.nodes.indexOf(node))
      assert.ok(node.position.y >= 106)
      assert.ok(node.position.x >= 0)
      assert.ok(node.position.x + node.width <= parent.width)
      assert.ok(node.position.y + node.height <= parent.height)
      assert.equal(node.absolutePosition.x, parent.absolutePosition.x + node.position.x)
    }
    assert.deepEqual(layout.edges.map((edge) => edge.id), ['start', 'saved', 'done'])
  }
})

test('interface size scales node bounds, spacing and nested loop headers together', () => {
  const source = graph([
    { id: 'loop', kind: 'loop' },
    { id: 'call', kind: 'activity', parent_id: 'loop', details: [{ label: 'Tool', value: 'fetch' }] }
  ])
  for (const direction of ['TB', 'LR']) {
    const base = layoutGraph(source, direction)
    for (const scale of [1.25, 1.5]) {
      const larger = layoutGraph(source, direction, scale)
      for (const [index, node] of larger.nodes.entries()) {
        assert.equal(node.width, base.nodes[index].width * scale)
        assert.equal(node.height, base.nodes[index].height * scale)
        assert.equal(node.absolutePosition.x, base.nodes[index].absolutePosition.x * scale)
        assert.equal(node.absolutePosition.y, base.nodes[index].absolutePosition.y * scale)
        assert.equal(node.style.height, `${node.height}px`)
      }
      const [parent, child] = larger.nodes
      assert.ok(child.position.y >= 106 * scale)
      assert.ok(child.position.y + child.height <= parent.height)
    }
  }
})

test('comparison remaps parents of new and removed loop members', () => {
  const before = graph([{ id: 'loop', kind: 'loop', label: 'items' }, { id: 'old', kind: 'activity', label: 'old', parent_id: 'loop' }])
  const after = graph([{ id: 'group', kind: 'loop', label: 'items' }, { id: 'new', kind: 'activity', label: 'new', parent_id: 'group' }])
  const result = compareGraphs(before, after)
  assert.equal(result.nodes.find((node) => node.label === 'old').parent_id, 'next-group')
  assert.equal(result.nodes.find((node) => node.label === 'new').parent_id, 'next-group')
  assert.ok(layoutGraph(result).nodes.every((node) => Number.isFinite(node.absolutePosition.x)))
})