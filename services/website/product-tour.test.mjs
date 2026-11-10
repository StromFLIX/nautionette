import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { productTour } from './product-tour-data.mjs'
import { validateProductTour } from './product-tour.mjs'

const clone = () => structuredClone(productTour)

test('the tour uses captured app screens and measured, bounded click targets', () => {
  assert.equal(validateProductTour(productTour), productTour)
  for (const id of ['chat', 'workflows', 'runs', 'statistics', 'settings']) assert.ok(productTour.scenes.some(scene => scene.id === id), id)
  assert.equal(productTour.initial, 'chat')
  for (const scene of productTour.scenes) {
    assert.match(scene.source.commit, /^[a-f0-9]{40}$/)
    assert.ok(scene.source.route.startsWith('/'))
    assert.ok(scene.hotspots.length > 0)
    assert.ok(scene.hotspots.every(hotspot => hotspot.target !== scene.id))
  }
})

test('every switch is backed by a local browser PNG and WebP, not generated HTML', () => {
  let bytes = 0
  for (const scene of productTour.scenes) {
    const webp = readFileSync(new URL(`.${scene.src}`, import.meta.url))
    assert.equal(webp.subarray(0, 4).toString(), 'RIFF')
    assert.equal(webp.subarray(8, 12).toString(), 'WEBP')
    const png = readFileSync(new URL(`.${scene.src.replace(/\.webp$/, '.png')}`, import.meta.url))
    assert.equal(png.subarray(1, 4).toString(), 'PNG')
    assert.equal(png.readUInt32BE(16), 1920)
    assert.equal(png.readUInt32BE(20), 1260)
    bytes += webp.length
  }
  assert.ok(bytes < 900000, `${bytes} bytes exceeds the captured screenshot budget`)
  const dockerfile = readFileSync(new URL('./Dockerfile', import.meta.url), 'utf8')
  assert.match(dockerfile, /product-tour\.mjs product-tour-data\.mjs/)
  assert.match(dockerfile, /COPY assets\//)
})

test('unknown destinations, foreign images and guessed out-of-bounds rectangles are rejected', () => {
  let data = clone(); data.initial = 'missing'
  assert.throws(() => validateProductTour(data), /identifiers/)
  data = clone(); data.scenes[0].src = 'https://other.example/screen.webp'
  assert.throws(() => validateProductTour(data), /local captured/)
  data = clone(); data.scenes[0].hotspots[0].target = 'missing'
  assert.throws(() => validateProductTour(data), /target/)
  for (const rect of [{ x: -0.1, y: 0, width: 0.1, height: 0.1 }, { x: 0.9, y: 0, width: 0.2, height: 0.1 }, { x: 0, y: 0, width: 0, height: 0.1 }, { x: NaN, y: 0, width: 0.1, height: 0.1 }]) {
    data = clone(); data.scenes[0].hotspots[0].rect = rect
    assert.throws(() => validateProductTour(data), /outside/)
  }
})

test('the viewer contains no substitute app or simulation interface', () => {
  const code = readFileSync(new URL('./product-tour.mjs', import.meta.url), 'utf8')
  assert.doesNotMatch(code, /innerHTML|localStorage|sessionStorage|fetch\(|XMLHttpRequest|WebSocket|EventSource|eval\(/)
  const html = readFileSync(new URL('./index.template.html', import.meta.url), 'utf8')
  assert.match(html, /data-tour-hotspots[^>]*hidden/)
  assert.match(html, /data-tour-controls hidden/)
  assert.match(html, /Real app screenshots, captured with demo data/)
  assert.doesNotMatch(html, /workspace-demo|data-workspace-demo|Simulate run/)
})
