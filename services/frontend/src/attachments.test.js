import assert from 'node:assert/strict'
import { test } from 'node:test'
import { addImages, MAX_IMAGE_BYTES, uploadImages } from './attachments.js'
import { createOutbox } from './outbox.js'

const file = (type = 'image/png', size = 10) => ({ name: 'screenshot.png', type, size })

test('file selection accepts raster images and rejects unsupported, oversized, empty or excess files atomically', () => {
  const selected = addImages([], [file()], () => 'image')
  assert.equal(selected[0].name, 'screenshot.png')
  assert.throws(() => addImages(selected, [file('image/svg+xml')]), /PNG/)
  assert.throws(() => addImages(selected, [file('image/png', MAX_IMAGE_BYTES + 1)]), /5 MiB/)
  assert.throws(() => addImages(selected, [file('image/png', 0)]), /nonempty/)
  assert.throws(() => addImages(selected, [file(), file(), file(), file()]), /4 images/)
  assert.equal(selected.length, 1)
})

test('partial upload failures keep successful references and retry only missing uploads', async () => {
  const images = addImages([], [file(), file()])
  let calls = 0
  const upload = async () => {
    calls++
    if (calls === 2) throw new Error('offline')
    return { id: `server-${calls}`, name: 'image.png', mime_type: 'image/png', size: 10 }
  }
  await assert.rejects(uploadImages('chat', images, upload), /offline/)
  assert.equal(images[0].uploaded.id, 'server-1')
  const result = await uploadImages('chat', images, upload)
  assert.deepEqual(result.map((image) => image.id), ['server-1', 'server-3'])
  assert.equal(calls, 3)
  await uploadImages('another-chat', images, upload)
  assert.equal(calls, 5)
})

test('outbox persists small image metadata across reload and retries image-only sends with the same ID', async () => {
  const data = new Map()
  const storage = { get length () { return data.size }, key: (i) => [...data.keys()][i],
    getItem: (key) => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) }
  const sent = []
  const options = { storage, prefix: 'test.', send: async (item) => { sent.push(item); return { message: { id: item.id } } } }
  const outbox = createOutbox(options)
  const image = { id: 'server-image', name: 'image.png', mime_type: 'image/png', size: 10, file: file(), data: 'not-persisted' }
  const item = outbox.enqueue('chat', '', [], [image])
  image.id = 'changed'
  const restored = createOutbox(options)
  assert.equal(restored.items()[0].attachments[0].id, 'server-image')
  assert.equal(restored.items()[0].attachments[0].file, undefined)
  assert.equal(restored.items()[0].attachments[0].data, undefined)
  await restored.flush()
  assert.equal(sent[0].id, item.id)
  assert.equal(sent[0].text, '')
  assert.equal(restored.items().length, 0)
})
