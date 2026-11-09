// An illustrative cellular field, never connected to visitor or instance activity.
// Short-lived Life colonies bloom and disperse; workflow trains take new routes,
// sometimes splitting and meeting again. A seed makes the simulation testable,
// while each visit gets a fresh composition. No shared loop or periodic reset.
export const TILE_SIZE = 28
export const GENERATION_MS = 420
const AGENT = 0
const SESSION = 1
const WORKFLOW = 2
const COLORS = ['101, 122, 222', '163, 151, 200', '211, 162, 110']
const MOTIFS = [
  { name: 'glider', kind: AGENT, cells: [[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]] },
  { name: 'scout', kind: AGENT, cells: [[1, 0], [4, 0], [0, 1], [0, 2], [4, 2], [0, 3], [1, 3], [2, 3], [3, 3]] },
  { name: 'bloom', kind: AGENT, cells: [[1, 0], [2, 0], [0, 1], [1, 1], [1, 2]] },
  { name: 'acorn', kind: AGENT, cells: [[1, 0], [3, 1], [0, 2], [1, 2], [4, 2], [5, 2], [6, 2]] },
  { name: 'beacon', kind: SESSION, cells: [[0, 0], [1, 0], [0, 1], [3, 2], [2, 3], [3, 3]] },
  { name: 'toad', kind: SESSION, cells: [[1, 0], [2, 0], [3, 0], [0, 1], [1, 1], [2, 1]] },
  { name: 'switch', kind: SESSION, cells: [[1, 0], [2, 0], [0, 1], [3, 2], [1, 3], [2, 3]] },
  { name: 'gathering', kind: SESSION, cells: [[0, 0], [1, 0], [0, 1], [1, 1], [3, 2], [4, 2], [3, 3], [4, 3]] }
]

function random (field) {
  // Numerical Recipes LCG; deliberately non-cryptographic, for illustration only.
  field.randomState = (Math.imul(field.randomState, 1664525) + 1013904223) >>> 0
  return field.randomState / 0x100000000
}
function integer (field, min, max) { return min + Math.floor(random(field) * (max - min + 1)) }
function budget (field) { return Math.max(1, Math.min(180, Math.floor(field.columns * field.rows * .16))) }

function quietPoint (field) {
  const { columns: w, rows: h } = field
  // Vary positions within the outer bands, not the same nine fixed anchors.
  const edge = integer(field, 0, 3)
  if (edge < 2) return [integer(field, 0, w - 1), edge === 0 ? integer(field, 0, Math.floor(h * .16)) : integer(field, Math.floor(h * .8), h - 1)]
  return [edge === 2 ? integer(field, 0, Math.floor(w * .12)) : integer(field, Math.floor(w * .85), w - 1), integer(field, 0, h - 1)]
}

// Pure Conway step, also useful independently of the visual choreography.
function lifeStep (cells, w, h) {
  const neighbours = new Map()
  for (const [index, kind] of cells) {
    const x = index % w
    const y = Math.floor(index / w)
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue
        const nx = x + dx
        const ny = y + dy
        if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue
        const key = ny * w + nx
        const tally = neighbours.get(key) || [0, 0, 0]
        tally[kind]++
        neighbours.set(key, tally)
      }
    }
  }
  const next = new Map()
  for (const [index, tally] of neighbours) {
    const count = tally[0] + tally[1] + tally[2]
    if (count === 3 || (count === 2 && cells.has(index))) {
      next.set(index, cells.get(index) ?? (tally[SESSION] > tally[AGENT] ? SESSION : AGENT))
    }
  }
  return next
}

function addColony (field, warm = false, kind) {
  const choices = MOTIFS.filter(motif => (kind === undefined || motif.kind === kind) && !field.colonies.some(colony => colony.motif === motif.name))
  const motif = choices[integer(field, 0, choices.length - 1)]
  const turn = integer(field, 0, 3)
  const mirror = random(field) < .5 ? -1 : 1
  let [x, y] = quietPoint(field)
  // A fresh cohort should have space to unfold, not land on an existing one.
  for (let attempt = 0; attempt < 8 && field.colonies.some(c => Math.abs(c.x - x) + Math.abs(c.y - y) < 7); attempt++) [x, y] = quietPoint(field)
  const offsets = motif.cells.map(([px, py]) => {
    let dx = (px - 2) * mirror
    let dy = py - 1
    for (let i = 0; i < turn; i++) [dx, dy] = [-dy, dx]
    return [dx, dy]
  })
  const fit = (position, values, size) => Math.max(0, Math.min(size - 1 - Math.max(...values), Math.max(-Math.min(...values), position)))
  x = fit(x, offsets.map(([dx]) => dx), field.columns)
  y = fit(y, offsets.map(([, dy]) => dy), field.rows)
  const cells = new Map()
  for (const [dx, dy] of offsets) {
    const col = x + dx
    const row = y + dy
    if (col >= 0 && col < field.columns && row >= 0 && row < field.rows) cells.set(row * field.columns + col, motif.kind)
  }
  let colonyCells = cells
  const age = warm ? integer(field, 0, 4) : 0
  for (let i = 0; i < age; i++) colonyCells = lifeStep(colonyCells, field.columns, field.rows)
  if (!colonyCells.size) colonyCells = cells
  field.colonies.push({ id: field.nextId++, motif: motif.name, x, y, age, lifetime: integer(field, 22, 58), cells: colonyCells })
}

function line (from, to, w) {
  const points = []
  let [x, y] = from
  while (x !== to[0] || y !== to[1]) {
    if (x !== to[0]) x += Math.sign(to[0] - x)
    else y += Math.sign(to[1] - y)
    points.push(y * w + x)
  }
  return points
}

function addFlow (field, warm = false) {
  const { columns: w, rows: h } = field
  const start = quietPoint(field)
  let end = quietPoint(field)
  for (let i = 0; i < 8 && Math.abs(start[0] - end[0]) + Math.abs(start[1] - end[1]) < 8; i++) end = quietPoint(field)
  // Follow an outer corridor so the most visible motion doesn't cross the copy.
  const top = random(field) < .5
  const corridor = top ? integer(field, 0, Math.floor(h * .15)) : integer(field, Math.floor(h * .82), h - 1)
  const elbow = [start[0], corridor]
  const join = [end[0], corridor]
  const path = [start[1] * w + start[0], ...line(start, elbow, w), ...line(elbow, join, w), ...line(join, end, w)]
  const branches = [path]
  if (Math.abs(start[0] - end[0]) > 5 && random(field) < .6) {
    const fork = Math.floor(path.length * .3)
    const forkPoint = [path[fork] % w, Math.floor(path[fork] / w)]
    const detourY = Math.max(0, Math.min(h - 1, corridor + (top ? 2 : -2)))
    const bend = [forkPoint[0], detourY]
    const rejoin = [join[0], detourY]
    branches.push([...path.slice(0, fork + 1), ...line(forkPoint, bend, w), ...line(bend, rejoin, w), ...line(rejoin, end, w)])
  }
  field.flows.push({ id: field.nextId++, paths: branches, age: warm ? integer(field, 0, 8) : 0, pace: integer(field, 1, 3), length: integer(field, 2, 5) })
}

function collectColonies (field) {
  const cells = new Map()
  const cap = budget(field)
  for (const colony of field.colonies) {
    for (const [index, kind] of colony.cells) {
      if (cells.size >= cap) break
      cells.set(index, kind)
    }
  }
  return cells
}

export function createField (columns, rows, seed = 0xD3E2A91) {
  const field = {
    columns: Math.max(1, Math.floor(columns)), rows: Math.max(1, Math.floor(rows)), generation: 0,
    cells: new Map(), colonies: [], flows: [], nextId: 0, randomState: seed >>> 0
  }
  const count = field.columns > 30 ? 6 : 3
  for (let i = 0; i < count; i++) addColony(field, true, i === 0 ? AGENT : i === 1 ? SESSION : undefined)
  addFlow(field, true)
  if (field.columns > 30) addFlow(field, true)
  field.nextColonyAt = integer(field, 4, 10)
  field.nextFlowAt = integer(field, 9, 20)
  field.cells = collectColonies(field)
  return field
}

export function nextGeneration (field) {
  // Plain input fields still get exact, unchoreographed Conway behaviour.
  if (!field.colonies) return { ...field, generation: field.generation + 1, cells: lifeStep(field.cells, field.columns, field.rows) }
  const next = {
    ...field, generation: field.generation + 1,
    colonies: field.colonies.map(colony => ({ ...colony, age: colony.age + 1, cells: lifeStep(colony.cells, field.columns, field.rows) }))
      .filter(colony => colony.age < colony.lifetime && colony.cells.size),
    flows: field.flows.map(flow => ({ ...flow, age: flow.age + 1 }))
      .filter(flow => Math.floor(flow.age / flow.pace) < Math.max(...flow.paths.map(path => path.length)) + flow.length)
  }
  const limit = field.columns > 30 ? 6 : 3
  if (next.generation >= next.nextColonyAt) {
    if (next.colonies.length < limit) addColony(next)
    next.nextColonyAt = next.generation + integer(next, 5, 13)
  }
  if (next.generation >= next.nextFlowAt) {
    if (next.flows.length < (field.columns > 30 ? 3 : 2)) addFlow(next)
    next.nextFlowAt = next.generation + integer(next, 8, 24)
  }
  next.cells = collectColonies(next)
  return next
}

export function visibleCells (field) {
  if (!field.flows) return new Map(field.cells)
  const cells = new Map()
  const cap = budget(field)
  for (const flow of field.flows) {
    const head = Math.floor(flow.age / flow.pace)
    for (const path of flow.paths) {
      for (let tail = 0; tail < flow.length; tail++) {
        const index = path[head - tail]
        if (index !== undefined && cells.size < cap) cells.set(index, WORKFLOW)
      }
    }
  }
  // Work stays sparse even when a chaotic Life pattern briefly blooms.
  for (const [index, kind] of field.cells) {
    if (cells.size >= cap) break
    if (!cells.has(index)) cells.set(index, kind)
  }
  return cells
}
