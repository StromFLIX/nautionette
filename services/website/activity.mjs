import { TILE_SIZE, GENERATION_MS, createField, nextGeneration, visibleCells } from './activity-simulation.mjs?v=living-2'
import { brand } from './brand.mjs?v=denkzeug-3'
export { TILE_SIZE, GENERATION_MS, createField, nextGeneration, visibleCells }
const COLORS = [brand.colors.agent, brand.colors.session, brand.colors.workflow]
  .map(color => [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16)).join(', '))

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
  const seed = globalThis.crypto?.getRandomValues(new Uint32Array(1))[0] ?? Math.floor(Math.random() * 0x100000000)
  let field = createField(1, 1, seed)
  let cells = visibleCells(field)
  const energies = new Map()
  const clarity = new Map()
  let readingZones = []
  const label = button.querySelector('span')

  function updateButton () {
    button.hidden = false
    button.setAttribute('aria-pressed', String(paused))
    label.textContent = paused ? 'Play activity' : 'Pause activity'
    button.querySelector('svg').innerHTML = paused
      ? '<path d="m3 1 7 5-7 5Z" fill="currentColor"/>'
      : '<path d="M3 2v8M9 2v8" stroke="currentColor" stroke-width="2"/>'
  }

  function measureReadingArea () {
    const bounds = canvas.getBoundingClientRect()
    const copy = canvas.parentElement.querySelectorAll('.hero h1, .hero-icon, .pronunciation > span, .definition, .definition-meta, .hero-actions > a, .field-caption')
    const zones = [...copy].map(element => {
      const box = element.getBoundingClientRect()
      return { left: box.left - bounds.left, right: box.right - bounds.left, top: box.top - bounds.top, bottom: box.bottom - bounds.top }
    })
    if (zones.length === readingZones.length && zones.every((zone, index) => {
      const previous = readingZones[index]
      return zone.left === previous.left && zone.right === previous.right && zone.top === previous.top && zone.bottom === previous.bottom
    })) return
    readingZones = zones
    clarity.clear()
  }
  function tileClarity (index) {
    if (clarity.has(index)) return clarity.get(index)
    const x = (index % field.columns + .5) * TILE_SIZE
    const y = (Math.floor(index / field.columns) + .5) * TILE_SIZE
    let level = 1
    for (const zone of readingZones) {
      const dx = Math.max(zone.left - x, 0, x - zone.right)
      const dy = Math.max(zone.top - y, 0, y - zone.bottom)
      const distance = Math.hypot(dx, dy)
      const fade = Math.max(0, Math.min(1, (distance - 12) / 32))
      level = Math.min(level, fade * fade * (3 - 2 * fade))
    }
    clarity.set(index, level)
    return level
  }
  function draw (instant = false) {
    // Locale/font reflow can move the copy before ResizeObserver is delivered;
    // position-only changes do not trigger it at all. Paint from current geometry,
    // retaining the per-tile cache when the reading zones have not moved.
    measureReadingArea()
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
      context.fillStyle = `rgba(${COLORS[tile.kind]}, ${tile.level * .52 * tileClarity(index)})`
      context.beginPath()
      context.roundRect(x + 3, y + 3, TILE_SIZE - 6, TILE_SIZE - 6, 5)
      context.fill()
    }
    // Tile-centre fading is approximate at fractional layout/device pixels.
    // Keep the intended reading cutouts exact, including the 12px quiet margin.
    for (const zone of readingZones) {
      const left = Math.floor(zone.left - 12)
      const top = Math.floor(zone.top - 12)
      context.clearRect(left, top, Math.ceil(zone.right + 12) - left, Math.ceil(zone.bottom + 12) - top)
    }
  }

  function animate (now) {
    frame = 0
    if (paused || !inView || document.hidden) return
    if (!lastStep) lastStep = now
    if (now - lastStep >= GENERATION_MS) {
      field = nextGeneration(field)
      cells = visibleCells(field)
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
    field = createField(Math.ceil(width / TILE_SIZE), Math.ceil(height / TILE_SIZE), seed)
    cells = visibleCells(field)
    energies.clear()
    clarity.clear() // A new grid can reuse indices at different tile coordinates.
    draw(true)
    sync()
  }
  const resizeObserver = new ResizeObserver(resize)
  resizeObserver.observe(canvas)
  const copyObserver = new ResizeObserver(() => { measureReadingArea(); if (paused) draw(true) })
  const copy = canvas.parentElement.querySelector('.hero-content')
  if (copy) copyObserver.observe(copy)
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
  document.fonts?.ready.then(() => { measureReadingArea(); if (paused) draw(true) })
  return { get paused () { return paused }, get generation () { return field.generation } }
}
