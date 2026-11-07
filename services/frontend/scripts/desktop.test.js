import { test } from 'node:test'
import assert from 'node:assert/strict'
import { resolve } from 'node:path'
import security from '../desktop/security.cjs'

const { trustedRenderer, externalURL, assetPath } = security

test('desktop only trusts its bundled origin and opens web URLs externally', () => {
  assert.equal(trustedRenderer('capacitor://localhost/'), true)
  for (const url of ['https://localhost/', 'capacitor://evil/', 'capacitor://localhost.evil/', 'file:///etc/passwd', 'garbage']) {
    assert.equal(trustedRenderer(url), false)
  }
  assert.equal(externalURL('https://example.com'), true)
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,test', 'ms-settings:', 'garbage']) {
    assert.equal(externalURL(url), false)
  }
})

test('desktop asset handler never reads outside dist', () => {
  const root = resolve('/app/dist')
  assert.equal(assetPath(root, 'capacitor://localhost/'), resolve(root, 'index.html'))
  assert.equal(assetPath(root, 'capacitor://localhost/assets/app.js'), resolve(root, 'assets/app.js'))
  for (const url of ['capacitor://localhost/..%2fsecret', 'capacitor://localhost/%00', 'capacitor://localhost/..%5csecret', 'https://example.com/']) {
    assert.equal(assetPath(root, url), null)
  }
})
