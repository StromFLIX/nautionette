import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { readFileSync } from 'node:fs'
const { existingDataPath } = createRequire(import.meta.url)('../desktop/data-path.cjs')

test('display rebranding never creates a fresh browser profile over an existing identity', () => {
  const root = join('profiles', 'app-data')
  for (const name of ['nautionette-frontend', 'Nautionette']) {
    assert.equal(existingDataPath(root, join(root, name), () => false), join(root, name))
    assert.equal(existingDataPath(root, join(root, 'Denkzeug'), path => path === join(root, name)), join(root, name))
  }
  assert.equal(existingDataPath(root, join(root, 'Denkzeug'), () => false), join(root, 'nautionette-frontend'))
  assert.equal(existingDataPath(root, join(root, 'custom-profile'), () => true), join(root, 'custom-profile'))
})

test('native display identity is configured only after the legacy session path is pinned', () => {
  const main = readFileSync(new URL('../desktop/main.cjs', import.meta.url), 'utf8')
  assert.ok(main.indexOf("app.setPath('userData'") < main.indexOf('app.requestSingleInstanceLock'))
  assert.ok(main.indexOf("app.setPath('sessionData'") < main.indexOf('app.setName(brand.name)'))
})
