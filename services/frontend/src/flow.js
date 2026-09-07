import dagre from '@dagrejs/dagre'

export function compareGraphs (before, after) {
  if (!after) return null
  const previous = before?.nodes || []
  const used = new Set()
  const mapped = new Map()
  const nodes = after.nodes.map((node) => {
    const match = previous.find((candidate) => !used.has(candidate.id) && candidate.kind === node.kind && candidate.label === node.label)
    const id = `next-${node.id}`
    if (!match) return { ...node, id, change: 'added' }
    used.add(match.id)
    mapped.set(match.id, id)
    const changed = (match.signature ?? match.code ?? '') !== (node.signature ?? node.code ?? '')
    return { ...node, id, change: changed ? 'changed' : null, before_code: changed ? match.code : null }
  })
  for (const node of previous) {
    if (used.has(node.id)) continue
    mapped.set(node.id, `previous-${node.id}`)
    nodes.push({ ...node, id: `previous-${node.id}`, change: 'removed' })
  }
  const edgeKey = (edge) => JSON.stringify([edge.source, edge.target, edge.label || ''])
  const oldEdges = (before?.edges || []).map((edge) => ({
    ...edge, id: `previous-${edge.id}`, source: mapped.get(edge.source), target: mapped.get(edge.target)
  }))
  const oldKeys = new Set(oldEdges.map(edgeKey))
  const edges = after.edges.map((edge) => {
    const mappedEdge = { ...edge, id: `next-${edge.id}`, source: `next-${edge.source}`, target: `next-${edge.target}` }
    return { ...mappedEdge, change: oldKeys.has(edgeKey(mappedEdge)) ? null : 'added' }
  })
  const newKeys = new Set(edges.map(edgeKey))
  for (const edge of oldEdges) {
    if (!newKeys.has(edgeKey(edge))) edges.push({ ...edge, change: 'removed' })
  }
  return { ...after, mode: 'changes', nodes, edges, warnings: [...new Set([...(before?.warnings || []), ...(after.warnings || [])])] }
}

export function layoutGraph (graph, direction = 'TB') {
  if (!graph) return { nodes: [], edges: [] }
  const layout = new dagre.graphlib.Graph({ multigraph: true })
    .setGraph({ rankdir: direction, nodesep: 36, ranksep: 70, marginx: 24, marginy: 24 })
    .setDefaultEdgeLabel(() => ({}))
  for (const node of graph.nodes) layout.setNode(node.id, { width: 264, height: 112 })
  for (const edge of graph.edges) layout.setEdge(edge.source, edge.target, {}, edge.id)
  dagre.layout(layout)
  return {
    nodes: graph.nodes.map((node) => {
      const position = layout.node(node.id)
      return {
        id: node.id, type: 'operation', data: node,
        position: { x: position.x - 132, y: position.y - 56 },
        sourcePosition: direction === 'TB' ? 'bottom' : 'right',
        targetPosition: direction === 'TB' ? 'top' : 'left'
      }
    }),
    edges: graph.edges.map((edge) => ({ ...edge, type: 'smoothstep' }))
  }
}

export function duration (start, finish, now = Date.now()) {
  if (!start) return ''
  const elapsed = Math.max(0, (finish ? Date.parse(finish) : now) - Date.parse(start))
  if (!Number.isFinite(elapsed)) return ''
  if (elapsed < 1000) return `${Math.round(elapsed)}ms`
  const seconds = Math.floor(elapsed / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}