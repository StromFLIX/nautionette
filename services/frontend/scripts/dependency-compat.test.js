// Security overrides must not change the legacy glob/minimatch API used by ASAR.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

const require = createRequire(import.meta.url)
const builderRequire = createRequire(require.resolve('app-builder-lib'))
const universalRequire = createRequire(builderRequire.resolve('@electron/universal'))

test('patched desktop matchers retain file filtering and universal-binary matching', () => {
  const { FileMatcher } = builderRequire('./fileMatcher.js')
  const filter = new FileMatcher('/app', '/out', value => value,
    ['dist/**/*', 'desktop/**/*', '!**/*.map']).createFilter()
  const stat = { isDirectory: () => false }
  assert.equal(filter('/app/dist/assets/main.js', stat), true)
  assert.equal(filter('/app/desktop/main.cjs', stat), true)
  assert.equal(filter('/app/dist/assets/main.js.map', stat), false)
  assert.equal(filter('/app/secrets.env', stat), false)
  const { minimatch } = universalRequire('minimatch')
  assert.equal(minimatch('Contents/Resources/native.node', '*.node', { matchBase: true }), true)
  assert.equal(minimatch('Contents/Resources/main.js', '*.node', { matchBase: true }), false)
})

test('legacy ASAR creation and native module unpacking still work', async t => {
  const asar = builderRequire('@electron/asar')
  const root = await mkdtemp(join(tmpdir(), 'dependency-asar-'))
  t.after(() => rm(root, { force: true, recursive: true }))
  const source = join(root, 'app'), archive = join(root, 'app.asar')
  await mkdir(source)
  await writeFile(join(source, 'main.cjs'), 'module.exports = 42\n')
  await writeFile(join(source, 'native.node'), 'native-module-fixture')
  await asar.createPackageWithOptions(source, archive, { unpack: '*.node' })
  assert.equal(asar.extractFile(archive, 'main.cjs').toString(), 'module.exports = 42\n')
  assert.equal(asar.statFile(archive, 'native.node').unpacked, true)
})
