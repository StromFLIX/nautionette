import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { preparePackages } from '../../images/pi-base/package-runtime.mjs'

const id = 'a'.repeat(32)
function fixture (fn) {
  const root = mkdtempSync(join(tmpdir(), 'package-runtime-'))
  const agentDir = join(root, 'agent'), artifactDir = join(root, 'artifacts'), file = join(root, 'private.json')
  mkdirSync(agentDir)
  mkdirSync(join(artifactDir, id, 'package'), { recursive: true })
  writeFileSync(join(agentDir, 'settings.json'), JSON.stringify({ packages: ['/builtin'], extensions: ['/gateway.ts'] }))
  const config = { installation_id: id, root: 'package', filters: { extensions: [], prompts: ['prompts/review.md'] },
    configuration: { env: { SERVICE_API_KEY: 'private' }, files: { 'agent/service.json': '{"region":"west"}' } } }
  const deliver = () => writeFileSync(file, JSON.stringify([config]))
  deliver()
  try { fn({ root, agentDir, artifactDir, file, inheritedEnv: {}, config, deliver }) }
  finally { rmSync(root, { recursive: true, force: true }) }
}

test('managed runtime uses local artifacts, preserves built-ins, and unlinks private delivery', () => {
  fixture(options => {
    assert.deepEqual(preparePackages(options), { SERVICE_API_KEY: 'private' })
    assert.equal(existsSync(options.file), false)
    const settings = JSON.parse(readFileSync(join(options.agentDir, 'settings.json')))
    assert.deepEqual(settings.extensions, ['/gateway.ts'])
    assert.deepEqual(settings.packages, ['/builtin', { source: join(options.artifactDir, id, 'package'),
      extensions: [], prompts: ['prompts/review.md'] }])
    assert.equal(JSON.stringify(settings).includes('private'), false)
    assert.equal(readFileSync(join(options.agentDir, 'service.json'), 'utf8'), '{"region":"west"}')
  })
})

test('missing delivery is a no-op', () => {
  assert.deepEqual(preparePackages({ file: '/nonexistent-package-delivery.json' }), {})
})

for (const path of ['agent/settings.json', 'agent/auth.json', 'agent/../evil.json', 'agent/extensions/evil.js']) {
  test(`managed configuration cannot write ${path}`, () => {
    fixture(options => {
      options.config.configuration.files = { [path]: 'evil' }; options.deliver()
      assert.throws(() => preparePackages(options), /Unsupported/)
    })
  })
}

test('runtime environment collisions, symlinks and escaped mounts fail closed', () => {
  fixture(options => {
    assert.throws(() => preparePackages({ ...options, inheritedEnv: { SERVICE_API_KEY: 'reserved' } }), /conflicts/)
    options.deliver()
    symlinkSync('/tmp', join(options.artifactDir, id, 'escape'))
    options.config.root = 'escape'; options.deliver()
    assert.throws(() => preparePackages(options), /escaped/)
    options.config.root = 'package'
    symlinkSync(join(options.agentDir, 'settings.json'), join(options.agentDir, 'service.json'))
    options.deliver()
    assert.throws(() => preparePackages(options), /symlinks/)
  })
})
