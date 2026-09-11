import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compileSkinCss, exportSkin, importDesign, installSkin, MAX_SKINS, sanitizeSkins, validateSkin } from './skins.js'
import { sanitizePreferences } from './preferences-schema.js'

const pack = (patch = {}) => ({ format: 'nautionette-skin', version: 2, id: 'test-pack', name: 'Test pack', base: 'orbit', tokens: { accent: '#abcdef' }, css: '.bubble { border-radius: 0; background: linear-gradient(#123456, #654321); }', assets: {}, ...patch })
const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg=='

for (const id of ['winamp-classic', 'windows-xp']) {
  test(`${id} is a standalone, portable pack with embedded artwork and component CSS`, () => {
    const text = readFileSync(new URL(`./skin-examples/${id}.skin.json`, import.meta.url), 'utf8')
    const { skin } = importDesign(text)
    assert.equal(skin.id, id)
    assert.ok(Object.keys(skin.assets).length)
    const css = compileSkinCss(skin)
    assert.match(css, /data:image\/png;base64/)
    assert.match(css, /data-skin-part/)
    assert.doesNotMatch(css, /skin:|https?:/)
    assert.deepEqual(importDesign(exportSkin(skin)).skin, skin)
  })
}

test('v1 theme compatibility and versioned skin round trips without account data', () => {
  assert.deepEqual(importDesign('{"version":1,"theme":"sand","overrides":{}}'), { kind: 'theme', theme: 'sand', overrides: {} })
  const skin = validateSkin(pack({ credentials: 'private', messages: ['not exported'] }))
  const exported = JSON.parse(exportSkin(skin, { 'radius-sm': 0 }))
  assert.equal(exported.credentials, undefined)
  assert.equal(exported.messages, undefined)
  assert.equal(exported.tokens['radius-sm'], 0)
  assert.equal(exported.css, skin.css)
  assert.deepEqual(importDesign(JSON.stringify(exported)).skin, exported)
})

test('schema rejects malformed input, unknown bases, tokens, assets and large imports atomically', () => {
  for (const text of ['null', '[]', '{}', 'bad JSON', ' '.repeat(512001)]) assert.throws(() => importDesign(text))
  for (const patch of [
    { version: 3 }, { format: 'unknown' }, { id: 'test\"]{}' }, { id: '__proto__' }, { name: '' }, { name: 'x'.repeat(81) },
    { author: {} }, { description: 1 }, { base: 'other' }, { tokens: [] }, { tokens: { invalid: 'red' } },
    { tokens: { font: 'serif; opacity: 0' } }, { css: '.a{}'.repeat(16001) }, { css: 2 },
    { assets: [] }, { assets: { wallpaper: 'https://tracker.test/a.png' } },
    { assets: { wallpaper: 'data:image/svg+xml;base64,PHN2Zz4=' } },
    { assets: { wallpaper: 'data:text/html;base64,YQ==' } }, { assets: { wallpaper: 'data:image/png;base64,!' } }
  ]) assert.throws(() => validateSkin(pack(patch)), JSON.stringify(patch).slice(0, 120))
})

test('CSS parser fails closed on network requests, scripts, escaped names and parser recovery', () => {
  for (const css of [
    '@import "https://tracker.test/x";', '@IMPORT url("https://tracker.test/x");',
    '.a {background:url(https://tracker.test/a)}', '.a{background:url(/api/private)}', '.a {--image: url(//tracker.test/a)}',
    '.a{background:image-set("https://tracker.test/a" 1x)}', '.a{background:-webkit-image-set("https://tracker.test/a" 1x)}',
    '.a{background:src("https://tracker.test/a")}', '.a { background: u\\72l(https://tracker.test/a) }',
    '.a{background:url("data:image/svg+xml;base64,PHN2Zz4=")}', '.a{background:url("skin:missing")}',
    '.a{behavior:url("skin:a")}', '.a{-moz-binding:none}', '.a{width:expression(alert(1))}',
    '@namespace svg url(https://tracker.test/a);', '@document url(https://tracker.test/a){}', '@unknown{}',
    '</style><script>alert(1)</script>', '.a::before{content:"</StYlE><img src=x>"}',
    '.a {color: red; invalid}', '.a { & .b {color:red} }',
    '.a {color: red; @media (max-width: 900px) { .b {color:blue} } }'
  ]) assert.throws(() => validateSkin(pack({ css })), css)
})

test('bundled images/fonts, responsive rules, pseudo selectors and variables compile with active-skin scoping', () => {
  const skin = validateSkin(pack({
    assets: { image, font: 'data:font/woff2;base64,d09GMg==' },
    css: '@font-face {font-family: test-pack-font; src:url("skin:font")} :root{--texture:url("skin:image")} html,body,.bubble:hover::before {background:var(--texture)} @media(max-width:900px){.a,.b{display:grid}} @supports(display:grid){.a{gap:1rem}}'
  }))
  const css = compileSkinCss(skin)
  assert.match(css, /html\[data-skin="test-pack"\]\{--texture:/)
  assert.match(css, /html\[data-skin="test-pack"\] body/)
  assert.match(css, /html\[data-skin="test-pack"\] \.bubble:hover::before/)
  assert.match(css, /@media/)
  assert.match(css, /data:font\/woff2;base64/)
  assert.equal(skin.css.includes('skin:font'), true, 'compilation must not mutate the original pack')
})

test('child combinators and media/container range queries remain valid CSS', () => {
  const skin = validateSkin(pack({ css: 'html > body .shell > main + aside ~ footer{color:red} @media (400px < width <= 900px){.shell > main{padding:0}} @container (width >= 200px){.bubble::before{content:"< >"}}' }))
  const css = compileSkinCss(skin)
  assert.match(css, /html\[data-skin="test-pack"\]>body \.shell>main\+aside~footer/)
  assert.match(css, /@media\s*\(400px<width<=900px\)/)
  assert.match(css, /@container\s*\(width>=200px\)/)
})

test('reduced motion strips pack animations and transitions without breaking keyframes', () => {
  const skin = validateSkin(pack({ css: '@keyframes test-pack-pulse{from{opacity:.8}to{opacity:1}} .a{animation:test-pack-pulse 1s infinite;transition:all 1s;scroll-behavior:smooth;color:red}' }))
  const normal = compileSkinCss(skin)
  assert.match(normal, /@keyframes test-pack-pulse\{from\{opacity:/)
  assert.match(normal, /animation:/)
  const reduced = compileSkinCss(skin, true)
  assert.doesNotMatch(reduced, /animation:|transition:|scroll-behavior:/)
  assert.match(reduced, /color:red/)
})

test('library install replaces matching ids, limits storage, and drops broken persisted packs', () => {
  let library = installSkin([], pack())
  library = installSkin(library, pack({ name: 'Updated' }))
  assert.equal(library.length, 1)
  assert.equal(library[0].name, 'Updated')
  for (let i = 1; i < MAX_SKINS; i++) library = installSkin(library, pack({ id: `pack-${i}` }))
  assert.throws(() => installSkin(library, pack({ id: 'overflow' })), /full/)
  assert.deepEqual(sanitizeSkins([pack(), pack({ css: '@import "https://example.test/a";' })]), [validateSkin(pack())])
  const bulky = pack({ assets: { image: 'data:image/png;base64,' + 'A'.repeat(400000) } })
  assert.throws(() => validateSkin({ ...bulky, css: '.a{background:url("skin:image")}'.repeat(10) }), /expanded assets/)
  let heavy = []
  for (let i = 0; i < 3; i++) heavy = installSkin(heavy, { ...bulky, id: `bulky-${i}` })
  assert.throws(() => installSkin(heavy, { ...bulky, id: 'overflow' }), /full/)
})

test('preference migration isolates skin tweaks and recovers missing/corrupt active packs', () => {
  const saved = sanitizePreferences({ skin: 'test-pack', skins: [pack({ base: 'daylight' })], theme: 'sand', overrides: {
    orbit: { accent: '#111111' }, 'skin:test-pack': { accent: '#222222' }, 'skin:missing': { accent: '#333333' }
  } })
  assert.equal(saved.skin, 'test-pack')
  assert.equal(saved.theme, 'daylight')
  assert.deepEqual(saved.overrides, { orbit: { accent: '#111111' }, 'skin:test-pack': { accent: '#222222' } })
  const recovered = sanitizePreferences({ ...saved, skins: [pack({ css: 'bad CSS' })] })
  assert.equal(recovered.skin, '')
  assert.deepEqual(recovered.skins, [])
  assert.deepEqual(recovered.overrides, { orbit: { accent: '#111111' } })
})
