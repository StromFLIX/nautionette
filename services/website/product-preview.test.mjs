import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setupProductPreview } from './product-preview.mjs'

function fixture () {
  const listeners = new Map()
  const node = (dataset = {}) => ({
    dataset, hidden: true, textContent: '', tabIndex: -1, attributes: new Map(),
    setAttribute (key, value) { this.attributes.set(key, value) },
    removeAttribute (key) { this.attributes.delete(key) },
    replaceChildren () { this.cleared = true },
    closest () { return this },
    focus () { document.activeElement = this },
  })
  const demoTab = node({ previewMode: 'demo' })
  const screenTab = node({ previewMode: 'screen' })
  const nodes = Object.fromEntries(['toolbar', 'screen', 'demo', 'host', 'feedback', 'disclosure'].map(key => [key, node()]))
  const selectorNodes = new Map([
    ['[data-explorer-toolbar]', nodes.toolbar], ['[data-preview-screen]', nodes.screen],
    ['[data-preview-demo]', nodes.demo], ['[data-workspace-demo]', nodes.host],
    ['[data-preview-feedback]', nodes.feedback], ['[data-preview-disclosure]', nodes.disclosure],
  ])
  const classes = new Set()
  const root = {
    querySelector: key => selectorNodes.get(key), querySelectorAll: () => [demoTab, screenTab],
    classList: { add: key => classes.add(key), remove: key => classes.delete(key) },
    contains: target => [demoTab, screenTab].includes(target),
    addEventListener: (key, listener) => listeners.set(key, listener),
    removeEventListener: key => listeners.delete(key),
  }
  const document = { querySelector: () => root }
  return { document, nodes, demoTab, screenTab, classes, listeners,
    click: target => listeners.get('click')({ target }),
    key: (target, key) => listeners.get('keydown')({ target, key, preventDefault () {} }),
  }
}
const settle = () => new Promise(resolve => setImmediate(resolve))

test('the explorer does not load a demo until explicitly selected', () => {
  const f = fixture()
  setupProductPreview(f.document, () => { throw new Error('must not load') })
  assert.equal(f.nodes.toolbar.hidden, false)
  assert.equal(f.nodes.screen.hidden, false)
  assert.equal(f.nodes.demo.hidden, true)
  assert.equal(f.screenTab.attributes.get('aria-selected'), 'true')
  assert.equal(f.screenTab.tabIndex, 0)
})

test('switching screenshots preserves one mounted local demo instance', async () => {
  const f = fixture()
  let imports = 0; let mounts = 0
  setupProductPreview(f.document, async () => {
    imports++
    return { mountWorkspaceDemo (element) { assert.equal(element, f.nodes.host); mounts++; return { destroy () {} } } }
  })
  f.click(f.demoTab)
  await settle()
  assert.equal(f.nodes.demo.hidden, false)
  assert.equal(f.demoTab.attributes.get('aria-selected'), 'true')
  f.click(f.screenTab)
  assert.equal(f.nodes.screen.hidden, false)
  f.click(f.demoTab)
  await settle()
  assert.equal(imports, 1)
  assert.equal(mounts, 1)
})

test('choosing the screenshot while loading does not later switch back', async () => {
  const f = fixture()
  let resolve
  setupProductPreview(f.document, () => new Promise(done => { resolve = done }))
  f.click(f.demoTab)
  f.click(f.screenTab)
  resolve({ mountWorkspaceDemo: () => ({ destroy () {} }) })
  await settle()
  assert.equal(f.nodes.screen.hidden, false)
  assert.equal(f.nodes.demo.hidden, true)
})

test('optional demo failures preserve real screenshots and explain the fallback', async () => {
  const f = fixture()
  setupProductPreview(f.document, async () => { throw new Error('offline') })
  f.click(f.demoTab)
  await settle()
  assert.equal(f.nodes.screen.hidden, false)
  assert.equal(f.nodes.demo.hidden, true)
  assert.equal(f.nodes.feedback.hidden, false)
  assert.match(f.nodes.feedback.textContent, /screenshots are still available/)
  assert.equal(f.demoTab.attributes.has('aria-busy'), false)
})

test('arrow keys move tab focus without activating a lazily loaded panel', () => {
  const f = fixture()
  setupProductPreview(f.document, () => { throw new Error('must not load') })
  f.key(f.screenTab, 'ArrowLeft')
  assert.equal(f.document.activeElement, f.demoTab)
  assert.equal(f.nodes.demo.hidden, true)
  f.key(f.demoTab, 'End')
  assert.equal(f.document.activeElement, f.screenTab)
})

test('destroying during a load does not mount detached content', async () => {
  const f = fixture()
  let resolve; let mounts = 0
  const preview = setupProductPreview(f.document, () => new Promise(done => { resolve = done }))
  f.click(f.demoTab)
  preview.destroy()
  resolve({ mountWorkspaceDemo: () => { mounts++; return { destroy () {} } } })
  await settle()
  assert.equal(mounts, 0)
  assert.equal(f.listeners.size, 0)
  assert.equal(f.nodes.screen.hidden, false)
})

test('the shipped website contains the complete optional explorer runtime', () => {
  const dockerfile = readFileSync(new URL('./Dockerfile', import.meta.url), 'utf8')
  const html = readFileSync(new URL('./index.template.html', import.meta.url), 'utf8')
  const runtime = ['product-preview.mjs', 'workspace-demo.mjs', 'workspace-demo.css']
  let bytes = 0
  for (const name of runtime) {
    assert.ok(dockerfile.includes(name), name)
    bytes += readFileSync(new URL(`./${name}`, import.meta.url)).length
  }
  assert.ok(bytes < 64000, `${bytes} bytes exceeds the lightweight explorer budget`)
  assert.match(html, /data-explorer-toolbar hidden/)
  assert.match(html, /data-preview-demo hidden/)
  assert.match(html, /data-preview-screen>/)
  assert.match(html, /local wireframe: no account, real agent runs or backend connection/)
})
