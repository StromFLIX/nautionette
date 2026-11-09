// An illustrative cellular field, never connected to visitor or instance activity.
// Conway gliders are agents, stable/oscillating groups are sessions, and short
// amber trains follow repeatable workflow paths. No random flashing or WebGL.
export const TILE_SIZE = 28
export const GENERATION_MS = 420
const AGENT = 0
const SESSION = 1
const WORKFLOW = 2
const COLORS = ['101, 122, 222', '163, 151, 200', '211, 162, 110']
const glider = [[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]]
const hive = [[1, 0], [2, 0], [0, 1], [3, 1], [1, 2], [2, 2]]
const blinker = [[0, 0], [1, 0], [2, 0]]

export function createField (columns, rows) {
  const field = { columns: Math.max(1, Math.floor(columns)), rows: Math.max(1, Math.floor(rows)), generation: 0, cells: new Map() }
  seedField(field)
  return field
}

function stamp (field, pattern, x, y, kind) {
  for (const [dx, dy] of pattern) {
    const col = ((Math.round(x) + dx) % field.columns + field.columns) % field.columns
    const row = ((Math.round(y) + dy) % field.rows + field.rows) % field.rows
    field.cells.set(row * field.columns + col, kind)
  }
}

function seedField (field) {
  const { columns: w, rows: h } = field
  // Deliberately leave a large reading area in the middle, even on a phone.
  stamp(field, glider, w * .08, h * .12, AGENT)
  stamp(field, glider, w * .77, h * .08, AGENT)
  stamp(field, glider, w * .13, h * .73, AGENT)
  stamp(field, hive, w * .88, h * .46, SESSION)
  stamp(field, blinker, w * .03, h * .48, SESSION)
  stamp(field, hive, w * .64, h * .86, SESSION)
  if (w > 30) {
    stamp(field, hive, w * .27, h * .08, SESSION)
    stamp(field, glider, w * .86, h * .74, AGENT)
    stamp(field, blinker, w * .25, h * .83, SESSION)
  }
}

export function nextGeneration (field) {
  const neighbours = new Map()
  const { columns: w, rows: h } = field
  for (const [index, kind] of field.cells) {
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
  const cells = new Map()
  for (const [index, tally] of neighbours) {
    const count = tally[0] + tally[1] + tally[2]
    if (count === 3 || (count === 2 && field.cells.has(index))) {
      cells.set(index, field.cells.get(index) ?? (tally[SESSION] > tally[AGENT] ? SESSION : AGENT))
    }
  }
  const next = { ...field, cells, generation: field.generation + 1 }
  // Replenish the finite field gently; don't let the illustration die out.
  if (next.generation % 64 === 0) seedField(next)
  return next
}

export function visibleCells (field) {
  const cells = new Map(field.cells)
  const { columns: w, rows: h, generation } = field
  const run = Math.max(4, Math.floor(w * .3))
  const paths = [
    // A horizontal train turns down at the right edge.
    [
      ...Array.from({ length: run }, (_, i) => [Math.floor(w * .5) + i, Math.floor(h * .13)]),
      ...Array.from({ length: 4 }, (_, i) => [Math.floor(w * .5) + run - 1, Math.floor(h * .13) + i + 1])
    ],
    Array.from({ length: Math.max(5, Math.floor(h * .38)) }, (_, i) => [Math.floor(w * .12), Math.floor(h * .6) + i]),
    Array.from({ length: Math.max(4, Math.floor(w * .24)) }, (_, i) => [Math.floor(w * .58) + i, Math.floor(h * .79)])
  ]
  for (const [p, path] of paths.entries()) {
    const head = (generation + p * 5) % (path.length + 9)
    for (let tail = 0; tail < 4; tail++) {
      const point = path[head - tail]
      if (!point) continue
      const [x, y] = point
      if (x >= 0 && x < w && y >= 0 && y < h) cells.set(y * w + x, WORKFLOW)
    }
  }
  return cells
}

export function mountActivity (canvas, button, storage) {
  const context = canvas?.getContext('2d')
  if (!context) return null // CSS grid and all content remain without canvas.
  const motion = matchMedia('(prefers-reduced-motion: reduce)')
  let savedPause = false
  try { savedPause = storage?.getItem('denkzeug-motion') === 'paused' } catch { /* Optional preference. */ }
  let paused = savedPause || motion.matches
  let inView = true
  let frame = 0
  let lastDraw = 0
  let lastStep = 0
  let width = 0
  let height = 0
  let field = createField(1, 1)
  const energies = new Map()
  const label = button.querySelector('span')

  function updateButton () {
    button.hidden = false
    button.setAttribute('aria-pressed', String(paused))
    label.textContent = paused ? 'Play activity' : 'Pause activity'
    button.querySelector('svg').innerHTML = paused
      ? '<path d="m3 1 7 5-7 5Z" fill="currentColor"/>'
      : '<path d="M3 2v8M9 2v8" stroke="currentColor" stroke-width="2"/>'
  }

  function draw (instant = false) {
    const cells = visibleCells(field)
    for (const [index, kind] of cells) {
      if (!energies.has(index)) energies.set(index, { kind, level: instant ? 1 : 0 })
      energies.get(index).kind = kind
    }
    context.clearRect(0, 0, width, height)
    for (const [index, tile] of energies) {
      const active = cells.has(index)
      tile.level = instant ? Number(active) : tile.level + (Number(active) - tile.level) * .28
      if (tile.level < .012 && !active) { energies.delete(index); continue }
      const x = (index % field.columns) * TILE_SIZE
      const y = Math.floor(index / field.columns) * TILE_SIZE
      context.fillStyle = `rgba(${COLORS[tile.kind]}, ${tile.level * .52})`
      context.beginPath()
      context.roundRect(x + 3, y + 3, TILE_SIZE - 6, TILE_SIZE - 6, 5)
      context.fill()
    }
  }

  function animate (now) {
    frame = 0
    if (paused || !inView || document.hidden) return
    if (!lastStep) lastStep = now
    if (now - lastStep >= GENERATION_MS) {
      field = nextGeneration(field)
      lastStep = now
    }
    // Render at at most 24fps, not the screen's 60/120Hz refresh rate.
    if (now - lastDraw >= 1000 / 24) { draw(); lastDraw = now }
    frame = requestAnimationFrame(animate)
  }
  function sync () {
    cancelAnimationFrame(frame)
    frame = 0
    lastStep = 0
    if (!paused && inView && !document.hidden) frame = requestAnimationFrame(animate)
  }
  function resize () {
    const box = canvas.getBoundingClientRect()
    width = box.width
    height = box.height
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = Math.round(width * dpr)
    canvas.height = Math.round(height * dpr)
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
    field = createField(Math.ceil(width / TILE_SIZE), Math.ceil(height / TILE_SIZE))
    energies.clear()
    draw(true)
    sync()
  }
  const resizeObserver = new ResizeObserver(resize)
  resizeObserver.observe(canvas)
  const intersection = new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; sync() })
  intersection.observe(canvas)
  document.addEventListener('visibilitychange', sync)
  motion.addEventListener('change', () => {
    paused = motion.matches || savedPause
    updateButton()
    sync()
    if (paused) draw(true)
  })
  button.addEventListener('click', () => {
    paused = !paused
    savedPause = paused
    try { storage?.setItem('denkzeug-motion', paused ? 'paused' : 'playing') } catch { /* Still usable without storage. */ }
    updateButton()
    sync()
  })
  updateButton()
  resize()
  return { get paused () { return paused }, get generation () { return field.generation } }
}
