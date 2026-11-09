import 'fake-indexeddb/auto'
import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { createDraftStorage, createDraftStore } from './draft-storage.js'

function fixture (storage = createDraftStorage(crypto.randomUUID())) {
  const saved = new Map()
  let serial = 0
  const scope = { value: 'server-a' }
  const remote = {
    async get (id) { return saved.get(id) || { revision: 0, text: '', attachments: [] } },
    async put (id, payload) {
      const previous = await remote.get(id)
      if (previous.mutation_id === payload.mutation_id) return previous
      if (previous.revision !== payload.revision) throw Object.assign(new Error('conflict'), { status: 409 })
      const next = { ...payload, revision: payload.revision + 1, attachments: payload.attachment_ids.map(id => ({ id, name: 'image.png' })) }
      saved.set(id, next)
      return next
    }
  }
  const upload = async (id, attachments) => attachments.map(item => {
    item.uploaded ||= { id: `uploaded-${item.id}`, name: item.name }
    item.uploadedChat = id
    return item.uploaded
  })
  const make = () => createDraftStore({ storage, remote, upload, scope: () => scope.value, uuid: () => `mutation-${++serial}`, delay: 0 })
  return { make, remote, saved, storage, scope }
}

const tick = () => new Promise(resolve => setTimeout(resolve, 10))

test('text and file bytes persist across reload, with separate chats and servers', async () => {
  const f = fixture(), drafts = f.make()
  await drafts.open('local:one')
  drafts.save('local:one', '  a very long\ndraft  ', [{ id: 'file', name: 'image.png', file: new Blob(['image bytes']) }])
  drafts.save('local:two', 'second', [])
  await tick()
  const reopened = await f.make().open('local:one')
  assert.equal(reopened.text, '  a very long\ndraft  ')
  assert.equal(await reopened.attachments[0].file.text(), 'image bytes')
  assert.equal((await f.make().open('local:two')).text, 'second')
  f.scope.value = 'server-b'
  assert.equal((await drafts.open('local:one')).text, '')
})

test('server sync restores files on another device and clears only this chat', async () => {
  const f = fixture(), drafts = f.make()
  await drafts.open('one')
  drafts.save('one', 'hello', [{ id: 'file', name: 'image.png', file: new Blob(['bytes']) }])
  await drafts.sync('one')
  const second = createDraftStore({ storage: createDraftStorage(crypto.randomUUID()), remote: f.remote, upload: async () => [], scope: () => 'server-a', delay: 0 })
  await second.open('one')
  await second.sync('one')
  assert.equal(second.entry('one').text, 'hello')
  assert.equal(second.entry('one').attachments[0].uploaded.id, 'uploaded-file')
  drafts.save('two', 'untouched', [])
  drafts.save('one', '', [])
  await drafts.sync('one')
  assert.equal(f.saved.get('one').text, '')
  assert.equal(drafts.entry('two').text, 'untouched')
})

test('slow restore never overwrites typing and failed sync preserves local data', async () => {
  let resolve
  const storage = { get: () => new Promise(done => { resolve = done }), put: async () => {} }
  const drafts = createDraftStore({ storage, remote: { get: async () => { throw new Error('offline') } }, upload: async () => [], scope: () => 'a', delay: 0 })
  drafts.entry('one')
  drafts.save('one', 'typed before disk restore', [])
  resolve({ text: 'old', attachments: [], revision: 3 })
  await drafts.sync('one')
  assert.equal(drafts.entry('one').text, 'typed before disk restore')
  assert.match(drafts.entry('one').error, /kept on this device/)
})

test('conflicting device edits never overwrite either draft', async () => {
  const f = fixture(), first = f.make()
  await first.open('one'); await first.sync('one')
  const other = createDraftStore({ storage: createDraftStorage(crypto.randomUUID()), remote: f.remote, upload: async () => [], scope: () => 'server-a', uuid: () => 'other', delay: 0 })
  await other.open('one'); await other.sync('one')
  first.save('one', 'first device', [])
  await first.sync('one')
  other.save('one', 'second device', [])
  await other.sync('one')
  assert.equal(f.saved.get('one').text, 'first device')
  assert.equal(other.entry('one').text, 'second device')
  assert.match(other.entry('one').error, /conflict/)
})

test('synchronous journal preserves the newest edit/clear while file transactions commit', async () => {
  const values = new Map()
  const journal = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
  const storage = createDraftStorage(crypto.randomUUID(), journal)
  const attachment = { id: 'one', name: 'notes.txt', file: new Blob(['original bytes']) }
  const data = { text: 'old', attachments: [attachment], dirty: true, revision: 0, mutation: 'old' }
  await storage.put('chat', data)
  const writing = storage.put('chat', { ...data, text: 'latest edit', mutation: 'latest' })
  assert.equal(JSON.parse([...values.values()][0]).text, 'latest edit')
  await writing
  const restored = await storage.get('chat')
  assert.equal(restored.text, 'latest edit')
  assert.equal(await restored.attachments[0].file.text(), 'original bytes')
  const clearing = storage.put('chat', { ...data, text: '', attachments: [], mutation: 'clear' })
  assert.equal(JSON.parse([...values.values()][0]).text, '')
  await clearing
  assert.equal((await storage.get('chat')).text, '')
  journal.getItem = () => '{corrupt'
  assert.equal((await storage.get('chat')).text, '')
})

test('malformed server responses never corrupt a restored local draft', async () => {
  const f = fixture(), drafts = f.make()
  f.remote.get = async () => ({})
  await drafts.open('one')
  await drafts.sync('one')
  assert.equal(drafts.entry('one').text, '')
  assert.deepEqual(drafts.entry('one').attachments, [])
  assert.match(drafts.entry('one').error, /sync unavailable/)
})
