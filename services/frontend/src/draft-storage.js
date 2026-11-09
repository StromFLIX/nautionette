import { openDB } from 'idb'

// Separate from the bounded history cache: drafts must never be evicted with history.
export function createDraftStorage (name = 'nautionette.composer-drafts', journal = globalThis.localStorage) {
  let database
  const open = () => (database ||= openDB(name, 1, {
    upgrade (db) { db.createObjectStore('drafts') }
  }))
  const journalKey = key => `${name}.${key}`
  return {
    async get (key) {
      const saved = await (await open()).get('drafts', key)
      // A tiny synchronous text/metadata journal closes the unload gap while
      // IndexedDB commits the original File bytes. Read it after the DB read.
      let data
      try {
        const latest = journal?.getItem(journalKey(key))
        if (!latest) return saved
        data = JSON.parse(latest)
        if (typeof data.text !== 'string' || !Array.isArray(data.attachments)) return saved
      } catch { return saved }
      data.attachments = data.attachments.map(item => {
        const original = saved?.attachments.find(file => file.id === item.id)?.file
        return { ...item, ...(original ? { file: original } : {}) }
      })
      return data
    },
    async put (key, value) {
      try {
        journal?.setItem(journalKey(key), JSON.stringify({
          ...value, attachments: value.attachments.map(({ file, ...metadata }) => metadata)
        }))
      } catch (error) {
        // Never allow an older journal to mask a newer IndexedDB snapshot.
        try { journal?.removeItem(journalKey(key)) } catch { /* IndexedDB is still usable. */ }
        await (await open()).put('drafts', value, key)
        throw error
      }
      await (await open()).put('drafts', value, key)
    },
    async remove (key) {
      journal?.removeItem(journalKey(key))
      await (await open()).delete('drafts', key)
    }
  }
}

export function createDraftStore ({ storage, remote, upload, scope, uuid = () => crypto.randomUUID(), delay = 600 }) {
  const entries = new Map()
  const listeners = new Set()
  const notify = entry => listeners.forEach(fn => fn(entry.key))
  function entryFor (id) {
    const key = JSON.stringify([scope(), id])
    if (!entries.has(key)) {
      const entry = { key, id, scope: scope(), text: '', attachments: [], revision: null, dirty: false, error: '', version: 0 }
      entries.set(key, entry)
      entry.ready = storage.get(key).then(saved => {
        if (saved && !entry.version) Object.assign(entry, saved)
        else if (saved) entry.revision = saved.revision
      }).catch(() => { entry.localError = 'Draft storage is unavailable. Keep this page open until you send.' }).then(() => notify(entry))
    }
    return entries.get(key)
  }
  function persist (entry) {
    if (entry.deleted) return
    // Snapshot synchronously, including File/Blob bytes, not Vue proxies.
    const { text, attachments, revision, dirty, mutation } = entry
    const data = structuredClone({ text, attachments, revision, dirty, mutation })
    // Start the transaction now. Chaining it behind a previous commit leaves a
    // JS-only gap where an immediate reload can cancel the newest edit/clear.
    // IndexedDB itself serializes read-write transactions in creation order.
    entry.writing = storage.put(entry.key, data).then(() => {
      if (entry.localError) { entry.localError = ''; notify(entry) }
    }).catch(() => {
      entry.localError = 'Could not save this draft on this device. Keep this page open until you send.'
      notify(entry)
    })
  }
  function schedule (entry) {
    clearTimeout(entry.timer)
    entry.timer = setTimeout(() => syncEntry(entry), delay)
  }
  async function syncEntry (entry) {
    await entry.ready
    if (entry.deleted || entry.scope !== scope() || !entry.id || entry.id.startsWith('local:')) return
    if (entry.syncing) { entry.again = true; return entry.syncing }
    entry.syncing = (async () => {
      try {
        const version = entry.version
        if (!entry.dirty || entry.revision === null) {
          const saved = await remote.get(entry.id)
          if (typeof saved.text !== 'string' || !Array.isArray(saved.attachments) || !Number.isInteger(saved.revision)) throw new Error('Invalid draft response')
          if (entry.deleted || entry.scope !== scope()) return
          if (!entry.dirty && entry.version === version) {
            entry.text = saved.text
            entry.attachments = saved.attachments.map(item => ({ ...item, uploaded: item, uploadedChat: entry.id }))
            entry.revision = saved.revision
            entry.error = ''
            persist(entry)
            notify(entry)
          } else if (entry.revision === null) {
            if (saved.text || saved.attachments.length) throw Object.assign(new Error('Draft changed on another device; your local draft has been kept'), { status: 409 })
            entry.revision = saved.revision
          }
        }
        if (!entry.dirty) return
        const submitted = { text: entry.text, attachments: entry.attachments.map(item => ({ ...item })), mutation: entry.mutation }
        const uploaded = await upload(entry.id, submitted.attachments)
        if (entry.deleted || entry.scope !== scope()) return
        // Persist upload IDs even when typing continues, avoiding duplicate uploads.
        for (const item of submitted.attachments) {
          const current = entry.attachments.find(candidate => candidate.id === item.id)
          if (current) { current.uploaded = item.uploaded; current.uploadedChat = item.uploadedChat }
        }
        persist(entry)
        const saved = await remote.put(entry.id, {
          revision: entry.revision, mutation_id: submitted.mutation, text: submitted.text,
          attachment_ids: uploaded.map(item => item.id)
        })
        entry.revision = saved.revision
        if (entry.mutation === submitted.mutation) entry.dirty = false
        entry.error = ''
        persist(entry)
        notify(entry)
        if (entry.dirty) entry.again = true
      } catch (error) {
        entry.error = error.status === 409
          ? 'Draft sync conflict: another device has a different draft. This device’s draft is kept locally; neither copy was overwritten.'
          : 'Draft sync unavailable. Your draft is kept on this device and will retry when connected.'
        notify(entry)
      }
    })().finally(() => {
      entry.syncing = null
      if (entry.again) { entry.again = false; schedule(entry) }
    })
    return entry.syncing
  }
  return {
    subscribe (fn) { listeners.add(fn); return () => listeners.delete(fn) },
    entry: entryFor,
    async open (id) { const entry = entryFor(id); await entry.ready; syncEntry(entry); return entry },
    save (id, text, attachments) {
      const entry = entryFor(id)
      entry.version++
      Object.assign(entry, { text, attachments: structuredClone(attachments), mutation: uuid(), dirty: true })
      persist(entry)
      schedule(entry)
      return entry
    },
    async remove (id) {
      const entry = entryFor(id)
      entry.deleted = true
      clearTimeout(entry.timer)
      await entry.ready
      await entry.writing
      await storage.remove(entry.key)
      entries.delete(entry.key)
    },
    sync (id) { return syncEntry(entryFor(id)) },
    retry () { for (const entry of entries.values()) if (entry.dirty) syncEntry(entry) }
  }
}
