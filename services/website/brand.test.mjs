import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { definitions, definitionFor, setupDefinition } from './definitions.mjs'
import { createField, nextGeneration, visibleCells } from './activity.mjs'
import { appURL } from './urls.mjs'
import { brand } from './brand.mjs'

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8')

function definitionDocument () {
  const listeners = {}
  const selector = { value: 'en', addEventListener: (event, fn) => { listeners[event] = fn } }
  const definition = {}
  return { selector, definition, listeners, getElementById: id => id === 'definition' ? definition : selector }
}

test('Denkzeug keeps the supplied German definition verbatim and defaults to English', () => {
  assert.equal(definitions.de, 'Ein Denkzeug ist ein nicht zum Körper eines lebenden oder künstlichen Organismus gehörendes Objekt, mit dessen Hilfe die Funktionen des Geistes erweitert werden, um auf diese Weise ein entferntes Ziel zu erreichen.')
  assert.deepEqual(definitionFor(undefined), { language: 'en', text: definitions.en, direction: 'ltr' })
  assert.equal(definitionFor('not-a-locale').language, 'en')
  assert.equal(definitionFor('__proto__').language, 'en')
  assert.equal(definitionFor('ar').direction, 'rtl')
  assert.ok(html.includes(`lang="en" dir="ltr">${definitions.en}</p>`))
  const options = [...html.matchAll(/<option value="([a-z]+)"/g)].map(match => match[1])
  assert.deepEqual(options, Object.keys(definitions))
  assert.equal(options.length, 12)
})

test('definition language changes persist, including Arabic direction and recovery to LTR', () => {
  const document = definitionDocument()
  const stored = new Map([['denkzeug-definition-language', 'de']])
  setupDefinition(document, { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value) })
  assert.equal(document.definition.textContent, definitions.de)
  assert.equal(document.selector.value, 'de')
  document.selector.value = 'ar'
  document.listeners.change()
  assert.equal(document.definition.dir, 'rtl')
  assert.equal(stored.get('denkzeug-definition-language'), 'ar')
  document.selector.value = 'en'
  document.listeners.change()
  assert.equal(document.definition.dir, 'ltr')
})

test('blocked storage never prevents changing the definition', () => {
  const document = definitionDocument()
  setupDefinition(document, { getItem () { throw Error('blocked') }, setItem () { throw Error('blocked') } })
  assert.equal(document.definition.lang, 'en')
  document.selector.value = 'ja'
  document.listeners.change()
  assert.equal(document.definition.textContent, definitions.ja)
  assert.equal(document.definition.lang, 'ja')
})

test('the activity field follows Conway rules instead of random flashes', () => {
  const field = { columns: 8, rows: 8, generation: 0, cells: new Map([[26, 0], [27, 0], [28, 0]]) }
  const next = nextGeneration(field)
  assert.deepEqual([...next.cells.keys()].sort((a, b) => a - b), [19, 27, 35])
  assert.equal(next.generation, 1)
  const restored = nextGeneration(next)
  assert.deepEqual([...restored.cells.keys()].sort((a, b) => a - b), [26, 27, 28])
  assert.deepEqual([...field.cells.keys()], [26, 27, 28], 'steps do not mutate their input')
})

test('activity remains deterministic, sparse and within bounds across sizes and reseeds', () => {
  for (const [columns, rows] of [[1, 1], [12, 26], [52, 33], [140, 50]]) {
    let field = createField(columns, rows)
    let same = createField(columns, rows)
    for (let i = 0; i < 160; i++) {
      const cells = visibleCells(field)
      for (const [index, kind] of cells) {
        assert.ok(index >= 0 && index < columns * rows)
        assert.ok([0, 1, 2].includes(kind))
      }
      if (columns > 1) assert.ok(cells.size < columns * rows * .3)
      assert.deepEqual(field, same)
      field = nextGeneration(field)
      same = nextGeneration(same)
    }
  }
})

test('different visit seeds change the composition without mutating earlier frames', () => {
  const field = createField(52, 33, 47)
  const original = structuredClone(field)
  assert.notDeepEqual(visibleCells(field), visibleCells(createField(52, 33, 48)))
  nextGeneration(field)
  visibleCells(field)
  assert.deepEqual(field, original)
})

test('seven minutes of activity do not settle into a short loop or a fixed set of routes', () => {
  for (const [columns, rows] of [[12, 26], [52, 33]]) {
    let field = createField(columns, rows, 47)
    const frames = new Set(), motifs = new Set(), routes = new Set(), paces = new Set(), lengths = new Set()
    let forks = 0
    for (let i = 0; i < 1000; i++) {
      const cells = visibleCells(field)
      frames.add(JSON.stringify([...cells].sort((a, b) => a[0] - b[0])))
      assert.ok(cells.size <= Math.floor(columns * rows * .16), 'bursts remain sparse')
      assert.ok(field.colonies.length <= (columns > 30 ? 6 : 3))
      assert.ok(field.flows.length <= (columns > 30 ? 3 : 2))
      for (const colony of field.colonies) motifs.add(colony.motif)
      for (const flow of field.flows) {
        const route = JSON.stringify(flow.paths)
        if (!routes.has(route) && flow.paths.length > 1) forks++
        routes.add(route)
        paces.add(flow.pace)
        lengths.add(flow.length)
        for (const path of flow.paths) {
          for (const index of path) assert.ok(index >= 0 && index < columns * rows)
        }
      }
      field = nextGeneration(field)
    }
    assert.ok(frames.size > 900, `${frames.size} unique frames, not a short repeating cycle`)
    assert.equal(motifs.size, 8)
    assert.ok(routes.size > 15)
    assert.ok(forks > 0)
    assert.equal(paces.size, 3)
    assert.equal(lengths.size, 4)
  }
})

test('only safe deployment URLs become app links', () => {
  assert.equal(appURL(' https://my-instance.example/app '), 'https://my-instance.example/app')
  assert.equal(appURL('http://localhost:9000'), 'http://localhost:9000/')
  for (const value of ['', '#start', 'javascript:alert(1)', 'data:text/html,test', '//evil.example', 'https://user:secret@example.com']) {
    assert.equal(appURL(value), null)
  }
})

test('website keeps working deployment and release contracts with reusable vector branding', () => {
  assert.match(html, /<h1 id="brand-title">Denkzeug<\/h1>/)
  for (const heading of ['Free', 'Remote', 'Extensible', 'Create', 'Process', 'Show']) assert.ok(html.includes(`>${heading}</h3>`))
  for (const id of ['app-link', 'release-status', 'release-downloads', 'ios-status']) assert.ok(html.includes(`id="${id}"`))
  assert.ok(html.includes('https://getartcraft.com/'))
  assert.ok(html.includes(`${brand.repository}#run-it`))
  assert.ok(html.includes('Existing instance addresses and installer filenames keep their Nautionette names for compatibility.'))
  assert.ok(!html.includes('Repository links, instance addresses and installation commands still use'))
  for (const asset of ['assets/denkzeug-icon.svg', 'assets/fonts/Sora.woff2', 'assets/fonts/SourceSerif4.woff2']) assert.ok(existsSync(new URL(asset, import.meta.url)))
  const svg = readFileSync(new URL('./assets/denkzeug-icon.svg', import.meta.url), 'utf8')
  assert.ok(svg.includes('<path'))
  assert.ok(!/<image|base64/.test(svg), 'the icon is a real vector, not an embedded bitmap')
  const dockerfile = readFileSync(new URL('./Dockerfile', import.meta.url), 'utf8')
  for (const module of ['activity.mjs', 'activity-simulation.mjs', 'definitions.mjs', 'urls.mjs']) assert.ok(dockerfile.includes(module))
  assert.ok(dockerfile.includes('COPY assets/'))
})
