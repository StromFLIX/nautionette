import dagre from '@dagrejs/dagre'

export function compareGraphs (before, after) {
  if (!after) return null
  const previous = before?.nodes || []
  const used = new Set()
  const mapped = new Map()
  const nodes = after.nodes.map((node) => {
    const match = previous.find((candidate) => !used.has(candidate.id) && candidate.kind === node.kind && candidate.label === node.label)
    const id = `next-${node.id}`
    if (!match) return { ...node, id, parent_id: node.parent_id ? `next-${node.parent_id}` : null, change: 'added' }
    used.add(match.id)
    mapped.set(match.id, id)
    const changed = (match.signature ?? match.code ?? '') !== (node.signature ?? node.code ?? '')
    return { ...node, id, parent_id: node.parent_id ? `next-${node.parent_id}` : null, change: changed ? 'changed' : null, before_code: changed ? match.code : null }
  })
  for (const node of previous) {
    if (used.has(node.id)) continue
    mapped.set(node.id, `previous-${node.id}`)
    nodes.push({ ...node, id: `previous-${node.id}`, change: 'removed' })
  }
  for (const node of nodes) {
    if (node.change === 'removed') node.parent_id = mapped.get(node.parent_id) || null
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

export function layoutGraph (graph, direction = 'TB', scale = 1) {
  if (!graph) return { nodes: [], edges: [] }
  const byId = new Map(graph.nodes.map((node) => [node.id, node]))
  const children = new Map()
  const sizes = new Map()
  const positions = new Map()
  for (const node of graph.nodes) {
    const parent = byId.get(node.parent_id)?.kind === 'loop' ? node.parent_id : null
    if (!children.has(parent)) children.set(parent, [])
    children.get(parent).push(node)
  }
  function ancestor (id, parent) {
    let node = byId.get(id)
    while (node && (node.parent_id || null) !== parent) node = byId.get(node.parent_id)
    return node?.id
  }
  function arrange (parent = null) {
    const members = children.get(parent) || []
    const layout = new dagre.graphlib.Graph({ multigraph: true })
      .setGraph({ rankdir: direction, nodesep: 36 * scale, ranksep: 64 * scale, marginx: 28 * scale, marginy: 28 * scale })
      .setDefaultEdgeLabel(() => ({}))
    for (const node of members) {
      const body = node.kind === 'loop' ? arrange(node.id) : null
      const size = body
        ? { width: Math.max(336 * scale, body.width), height: Math.max(190 * scale, body.height + 106 * scale) }
        : { width: 264 * scale, height: (node.details?.length ? 176 : 112) * scale }
      sizes.set(node.id, size)
      layout.setNode(node.id, size)
    }
    for (const edge of graph.edges) {
      if (edge.role === 'repeat') continue
      const source = ancestor(edge.source, parent)
      const target = ancestor(edge.target, parent)
      if (source && target && source !== target) layout.setEdge(source, target, {}, edge.id)
    }
    if (!members.length) return { width: 336 * scale, height: 84 * scale }
    dagre.layout(layout)
    for (const node of members) {
      const point = layout.node(node.id)
      const size = sizes.get(node.id)
      positions.set(node.id, { x: point.x - size.width / 2, y: point.y - size.height / 2 + (parent ? 106 * scale : 0) })
    }
    return layout.graph()
  }
  arrange()
  const nodes = []
  function append (parent = null, origin = { x: 0, y: 0 }) {
    for (const node of children.get(parent) || []) {
      const position = positions.get(node.id)
      const size = sizes.get(node.id)
      const absolutePosition = { x: origin.x + position.x, y: origin.y + position.y }
      nodes.push({
        id: node.id, type: node.kind === 'loop' ? 'loop' : 'operation', data: node,
        position, absolutePosition, ...size,
        parentNode: parent || undefined, extent: parent ? 'parent' : undefined,
        style: { width: `${size.width}px`, height: `${size.height}px` },
        sourcePosition: direction === 'TB' ? 'bottom' : 'right',
        targetPosition: direction === 'TB' ? 'top' : 'left'
      })
      append(node.id, absolutePosition)
    }
  }
  append()
  return {
    nodes,
    edges: graph.edges.filter((edge) => edge.role !== 'repeat' && ancestor(edge.target, byId.get(edge.source)?.parent_id || null) !== edge.source)
      .map((edge) => ({ ...edge, type: 'smoothstep' }))
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