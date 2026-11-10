import { productTour } from './product-tour-data.mjs?v=screens-1'

// The only interaction is selecting another real browser screenshot. Rectangles
// are recorded from the app's DOM during capture, never guessed or redrawn UI.
export function validateProductTour (tour) {
  if (!tour || !Array.isArray(tour.scenes) || !tour.scenes.length) throw new TypeError('Missing captured screens')
  const ids = new Set(tour.scenes.map(scene => scene.id))
  if (ids.size !== tour.scenes.length || !ids.has(tour.initial)) throw new TypeError('Invalid screen identifiers')
  for (const scene of tour.scenes) {
    if (!/^[a-z][a-z0-9-]*$/.test(scene.id) || !/^\/assets\/product-tour\/[a-z0-9-]+\.webp$/.test(scene.src)) throw new TypeError('Screens must use local captured assets')
    if (!scene.title || !scene.alt || !Array.isArray(scene.hotspots)) throw new TypeError('Missing screen description')
    const targets = new Set()
    for (const hotspot of scene.hotspots) {
      const rect = hotspot.rect
      if (!hotspot.id || targets.has(hotspot.id) || !hotspot.label || !ids.has(hotspot.target)) throw new TypeError('Invalid screenshot target')
      targets.add(hotspot.id)
      if (!rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key])) || rect.x < 0 || rect.y < 0 || rect.width <= 0 || rect.height <= 0 || rect.x + rect.width > 1.001 || rect.y + rect.height > 1.001) throw new TypeError('Screenshot target is outside its capture')
    }
  }
  return tour
}

export function mountProductTour (root, tour = productTour) {
  if (!root) return null
  validateProductTour(tour)
  const document = root.ownerDocument
  const image = root.querySelector('[data-tour-image]')
  const original = root.querySelector('[data-tour-original]')
  const large = root.querySelector('[data-tour-fullsize]')
  const overlay = root.querySelector('[data-tour-hotspots]')
  const navigation = root.querySelector('[data-tour-navigation]')
  const title = root.querySelector('[data-tour-title]')
  const status = root.querySelector('[data-tour-status]')
  const controls = root.querySelector('[data-tour-controls]')
  const back = root.querySelector('[data-tour-back]')
  const scenes = new Map(tour.scenes.map(scene => [scene.id, scene]))
  let current = null
  let history = []
  let request = 0
  let destroyed = false
  const buttons = new Map()

  function display (id, fromHotspot, historyMode) {
    const scene = scenes.get(id)
    if (!scene || destroyed) return
    const pending = ++request
    root.setAttribute('aria-busy', 'true')
    status.textContent = `Loading ${scene.title} screenshot…`
    const candidate = document.createElement('img')
    candidate.onload = () => {
      if (destroyed || pending !== request) return
      if (historyMode === 'push' && current && current !== id) history.push(current)
      else if (historyMode === 'back') history.pop()
      else if (historyMode === 'reset') history = []
      current = id
      image.removeAttribute('srcset')
      image.removeAttribute('sizes')
      image.src = scene.src
      image.alt = scene.alt
      original.href = scene.src
      large.href = scene.src
      title.textContent = scene.title
      overlay.replaceChildren()
      for (const hotspot of scene.hotspots) {
        const button = document.createElement('button')
        button.type = 'button'
        button.className = 'product-tour__hotspot'
        button.dataset.tourTarget = hotspot.target
        button.setAttribute('aria-label', hotspot.label)
        button.title = hotspot.label
        for (const [property, value] of Object.entries({ left: hotspot.rect.x, top: hotspot.rect.y, width: hotspot.rect.width, height: hotspot.rect.height })) button.style[property] = `${value * 100}%`
        overlay.append(button)
      }
      for (const [key, button] of buttons) button.setAttribute('aria-pressed', String(key === id))
      back.disabled = history.length === 0
      overlay.hidden = false
      controls.hidden = false
      root.removeAttribute('aria-busy')
      status.textContent = `${scene.title}. Screenshot ${tour.scenes.indexOf(scene) + 1} of ${tour.scenes.length}.`
      if (fromHotspot) buttons.get(id)?.focus({ preventScroll: true })
    }
    candidate.onerror = () => {
      if (destroyed || pending !== request) return
      root.removeAttribute('aria-busy')
      status.textContent = 'That screenshot couldn’t load. The previous screenshot is still available.'
    }
    candidate.src = scene.src
  }

  for (const scene of tour.scenes) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = scene.title
    button.dataset.tourScene = scene.id
    button.setAttribute('aria-pressed', 'false')
    buttons.set(scene.id, button)
    navigation.append(button)
  }
  function click (event) {
    const hotspot = event.target.closest('[data-tour-target]')
    const screen = event.target.closest('[data-tour-scene]')
    if (hotspot && root.contains(hotspot)) display(hotspot.dataset.tourTarget, true, 'push')
    else if (screen && root.contains(screen)) display(screen.dataset.tourScene, false, 'push')
    else if (event.target.closest('[data-tour-back]') && history.length) display(history.at(-1), false, 'back')
    else if (event.target.closest('[data-tour-reset]')) display(tour.initial, false, 'reset')
  }
  root.addEventListener('click', click)
  display(tour.initial, false, 'reset')
  return {
    destroy () {
      destroyed = true
      request++
      root.removeEventListener('click', click)
      root.removeAttribute('aria-busy')
      overlay.replaceChildren()
      overlay.hidden = true
      controls.hidden = true
      navigation.replaceChildren()
    },
  }
}
