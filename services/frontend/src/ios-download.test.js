import { test } from 'node:test'
import assert from 'node:assert/strict'
import { saveIOSDownload } from './ios-download.js'

test('iOS stages bounded chunks, shares only a private URI and cleans up', async t => {
  const original = globalThis.FileReader
  globalThis.FileReader = class {
    readAsDataURL (blob) {
      blob.arrayBuffer().then(buffer => {
        this.result = `data:;base64,${Buffer.from(buffer).toString('base64')}`
        this.onload()
      })
    }
  }
  t.after(() => { globalThis.FileReader = original })
  const calls = []
  const Filesystem = Object.fromEntries(['mkdir', 'writeFile', 'appendFile', 'rmdir'].map(method => [method, async args => { calls.push({ method, ...args }) }]))
  Filesystem.getUri = async () => ({ uri: 'file:///private/cache/export' })
  const Share = { share: async args => { calls.push({ method: 'share', ...args }) } }
  const deps = { Filesystem, Directory: { Cache: 'CACHE' }, Share }
  await saveIOSDownload(new Blob([new Uint8Array(400000)]), '../report.pdf', undefined, deps)
  const chunks = calls.filter(call => call.method === 'appendFile')
  assert.equal(chunks.length, 3)
  assert.ok(chunks.every(call => call.data.length <= 256 * 1024))
  assert.equal(chunks.reduce((sum, call) => sum + Buffer.from(call.data, 'base64').length, 0), 400000)
  assert.ok(calls[1].path.endsWith('/.._report.pdf'))
  assert.deepEqual(calls.at(-2).files, ['file:///private/cache/export'])
  assert.equal(calls.at(-1).method, 'rmdir')

  calls.length = 0
  Share.share = async () => { throw new Error('cancelled') }
  await assert.rejects(saveIOSDownload(new Blob(['a']), 'a.txt', undefined, deps), /cancelled/)
  assert.equal(calls.at(-1).method, 'rmdir')

  calls.length = 0
  await assert.rejects(saveIOSDownload(new Blob(['a']), 'a.txt', AbortSignal.abort(), deps), { name: 'AbortError' })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].method, 'rmdir')
})
