import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { brand } from './brand.js'

test('display branding has a single source and does not rename technical namespaces', () => {
  const canonical = JSON.parse(readFileSync(new URL('../../../branding/brand.json', import.meta.url), 'utf8'))
  assert.equal(brand.name, canonical.name)
  assert.equal(brand.tagline, canonical.tagline)
  assert.equal(brand.defaultTheme, canonical.defaultTheme)
  assert.equal(brand.logo, `/brand/logo.svg?v=${brand.logoVersion}`)
  assert.match(brand.logoVersion, /^[0-9a-f]{12}$/)
  assert.ok(Object.isFrozen(brand))
  const source = readFileSync(new URL('../../../branding/logo.svg', import.meta.url), 'utf8')
  assert.equal(readFileSync(new URL('../public/brand/logo.svg', import.meta.url), 'utf8'), source)
  assert.match(source, /opacity="0\.65"/)
})
