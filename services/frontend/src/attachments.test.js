import assert from 'node:assert/strict'
import { test } from 'node:test'
import { addAttachments, isImage, MAX_FILE_BYTES, uploadAttachments } from './attachments.js'
import { createOutbox } from './outbox.js'

const file = (type = 'image/png', size = 10) => ({ name: 'screenshot.png', type, size })

test('file selection accepts any format but rejects oversized, empty or excess files atomically', () => {
  const selected = addAttachments([], [file()], () => 'image')
  assert.equal(selected[0].name, 'screenshot.png')
  for (const type of ['application/pdf', 'text/csv', 'application/zip', 'image/svg+xml', '']) {
    const attachment = addAttachments([], [file(type)])[0]
    assert.equal(attachment.mime_type, type || 'application/octet-stream')
    assert.equal(isImage(attachment), false)
  }
  assert.equal(isImage(selected[0]), true)
  assert.throws(() => addAttachments(selected, [file('application/pdf', MAX_FILE_BYTES + 1)]), /5 MiB/)
  assert.throws(() => addAttachments(selected, [file('text/plain', 0)]), /nonempty/)
  assert.throws(() => addAttachments(selected, [file(), file(), file(), file()]), /4 files/)
  assert.equal(selected.length, 1)
})

test('partial upload failures keep successful references and retry only missing uploads', async () => {
  const attachments = addAttachments([], [file(), file('application/pdf')])
  let calls = 0
  const upload = async (_chat, file) => {
    calls++
    if (calls === 2) throw new Error('offline')
    return { id: `server-${calls}`, name: file.name, mime_type: file.type, size: file.size }
  }
  await assert.rejects(uploadAttachments('chat', attachments, upload), /offline/)
  assert.equal(attachments[0].uploaded.id, 'server-1')
  const result = await uploadAttachments('chat', attachments, upload)
  assert.deepEqual(result.map((attachment) => attachment.id), ['server-1', 'server-3'])
  assert.equal(result[1].mime_type, 'application/pdf')
  assert.equal(calls, 3)
  await uploadAttachments('another-chat', attachments, upload)
  assert.equal(calls, 5)
})

for (const mime of ['image/png', 'application/pdf', 'application/octet-stream']) {
  test(`outbox persists only metadata and retries attachment-only sends with the same ID: ${mime}`, async () => {
    const data = new Map()
    const storage = { get length () { return data.size }, key: (i) => [...data.keys()][i],
      getItem: (key) => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) }
    const sent = []
    const options = { storage, prefix: 'test.', send: async (item) => { sent.push(item); return { message: { id: item.id } } } }
    const outbox = createOutbox(options)
    const attachment = { id: 'server-file', name: 'file', mime_type: mime, size: 10, file: file(mime), data: 'not-persisted' }
    const item = outbox.enqueue('chat', '', [], [attachment])
    attachment.id = 'changed'
    const restored = createOutbox(options)
    assert.equal(restored.items()[0].attachments[0].id, 'server-file')
    assert.equal(restored.items()[0].attachments[0].mime_type, mime)
    assert.equal(restored.items()[0].attachments[0].file, undefined)
    assert.equal(restored.items()[0].attachments[0].data, undefined)
    await restored.flush()
    assert.equal(sent[0].id, item.id)
    assert.equal(sent[0].text, '')
    assert.equal(restored.items().length, 0)
  })
}
