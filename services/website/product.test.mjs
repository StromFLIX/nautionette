import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8')

test('the product explanation and device views sit immediately after the opening', () => {
  const product = html.indexOf('id="workspace"')
  assert.ok(product > html.indexOf('id="brand-title"'))
  assert.ok(product < html.indexOf('id="idea"'))
  assert.match(html, /A workspace for<br \/>your AI agents\./)
  assert.match(html, /an open-source app you run on your own server/)
  assert.match(html, /Real app screenshots, captured with demo data/)
  assert.match(html, /href="#workspace">See the workspace/)
})

test('product screenshots are local WebP assets with a bounded page-weight budget', () => {
  let bytes = 0
  for (const name of ['desktop.webp', 'desktop-small.webp', 'phone.webp']) {
    const data = readFileSync(new URL(`./assets/product/${name}`, import.meta.url))
    assert.equal(data.subarray(0, 4).toString(), 'RIFF')
    assert.equal(data.subarray(8, 12).toString(), 'WEBP')
    assert.ok(data.length > 1000, `${name}: not an empty or placeholder image`)
    bytes += data.length
  }
  assert.ok(bytes < 350000, `${bytes} bytes exceeds the screenshot asset budget`)
  assert.match(html, /srcset="[^"]*desktop-small\.webp 1280w, [^"]*desktop\.webp 1920w"/)
  assert.match(html, /width="1280" height="840" loading="lazy" decoding="async"/)
  assert.match(html, /width="780" height="1688" loading="lazy" decoding="async"/)
})
