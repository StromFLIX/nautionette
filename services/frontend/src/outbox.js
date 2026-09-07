export function createOutbox ({ storage, prefix, send, changed = () => {}, accepted = () => {}, now = Date.now, uuid = () => crypto.randomUUID() }) {
  const sending = new Set()

  function items () {
    const result = []
    for (let index = 0; index < storage.length; index++) {
      const key = storage.key(index)
      if (!key?.startsWith(prefix)) continue
      try {
        const item = JSON.parse(storage.getItem(key))
        if (item?.id && item.chatId && typeof item.text === 'string') result.push(item)
      } catch { /* Leave unreadable entries untouched. */ }
    }
    return result.sort((left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id))
  }

  function save (item) {
    storage.setItem(prefix + item.id, JSON.stringify(item))
    changed(items())
  }

  function discard (id) {
    storage.removeItem(prefix + id)
    changed(items())
  }

  function enqueue (chatId, text, projectIds) {
    const createdAt = Math.max(now(), (items().at(-1)?.createdAt || 0) + 1)
    const item = { id: uuid(), chatId, text, createdAt, attempts: 0, nextAttempt: 0, error: '' }
    if (projectIds !== undefined) item.projectIds = [...projectIds]
    save(item)
    return item
  }

  async function flush () {
    const chats = new Set()
    const work = []
    for (const item of items()) {
      if (chats.has(item.chatId)) continue
      chats.add(item.chatId)
      if (sending.has(item.id) || item.error || item.nextAttempt > now()) continue
      sending.add(item.id)
      work.push((async () => {
        try {
          const result = await send(item)
          accepted(result.message)
          discard(item.id)
        } catch (error) {
          if (!storage.getItem(prefix + item.id)) return
          const retryable = !error.status || [408, 409, 429].includes(error.status) || error.status >= 500
          item.attempts++
          item.nextAttempt = now() + Math.min(30000, 1000 * 2 ** Math.min(item.attempts - 1, 5))
          item.error = retryable ? '' : error.message
          save(item)
        } finally {
          sending.delete(item.id)
        }
      })())
    }
    await Promise.all(work)
    changed(items())
  }

  function retry (id) {
    const item = items().find((entry) => entry.id === id)
    if (item) save({ ...item, error: '', nextAttempt: 0 })
    return flush()
  }

  function reconcile (messages) {
    for (const message of messages) storage.removeItem(prefix + message.id)
    changed(items())
  }

  return { items, enqueue, flush, retry, discard, reconcile }
}