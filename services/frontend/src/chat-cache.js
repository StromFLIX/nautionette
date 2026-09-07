import { openDB } from 'idb'
import { ref } from 'vue'
import { api, server } from './api.js'

export const CHAT_CACHE_LIMIT = 100
export const cacheError = ref('')

export function createChatCache (name = 'nautionette.chat-cache') {
  let database
  function open () {
    database ||= openDB(name, 1, {
      upgrade (db) {
        db.createObjectStore('snapshots', { keyPath: ['scope', 'id'] }).createIndex('recent', ['scope', 'savedAt'])
        db.createObjectStore('lists')
      }
    })
    return database
  }
  return {
    async get (scope, id) {
      const db = await open()
      return (await db.get('snapshots', [scope, id]))?.data || null
    },
    async put (scope, data, expected) {
      if (!data?.chat?.id) return
      const db = await open()
      const transaction = db.transaction('snapshots', 'readwrite')
      if (expected !== undefined) {
        const current = (await transaction.store.get([scope, data.chat.id]))?.data || null
        if (JSON.stringify(current) !== JSON.stringify(expected)) {
          await transaction.done
          return
        }
      }
      await transaction.store.put({ scope, id: data.chat.id, savedAt: Date.now(), data: JSON.parse(JSON.stringify(data)) })
      const keys = await transaction.store.index('recent').getAllKeys(IDBKeyRange.bound([scope, 0], [scope, Infinity]))
      for (const key of keys.slice(0, Math.max(0, keys.length - CHAT_CACHE_LIMIT))) await transaction.store.delete(key)
      await transaction.done
    },
    async accept (scope, message) {
      const db = await open()
      const transaction = db.transaction('snapshots', 'readwrite')
      const entry = await transaction.store.get([scope, message.chat_id])
      if (entry && !entry.data.messages.some((saved) => saved.id === message.id)) {
        entry.data.messages.push(JSON.parse(JSON.stringify(message)))
        await transaction.store.put(entry)
      }
      await transaction.done
    },
    async list (scope) {
      return (await (await open()).get('lists', scope)) || []
    },
    async saveList (scope, chats) {
      await (await open()).put('lists', JSON.parse(JSON.stringify(chats)), scope)
    },
    async remove (scope, id) {
      await (await open()).delete('snapshots', [scope, id])
    }
  }
}

const storage = createChatCache()
export const chatCacheScope = () => server.url || location.origin

async function cached (operation) {
  try {
    return await operation()
  } catch {
    cacheError.value = 'Offline history could not be saved on this device.'
    return null
  }
}

export const chatCache = {
  get: (id, scope = chatCacheScope()) => cached(() => storage.get(scope, id)),
  put: (data, scope = chatCacheScope(), expected) => cached(() => storage.put(scope, data, expected)),
  accept: (message, scope = chatCacheScope()) => cached(() => storage.accept(scope, message)),
  list: (scope = chatCacheScope()) => cached(() => storage.list(scope)),
  saveList: (chats, scope = chatCacheScope()) => cached(() => storage.saveList(scope, chats)),
  remove: (id, scope = chatCacheScope()) => cached(() => storage.remove(scope, id))
}

let warming = null
export function warmChatCache (chats, scope = chatCacheScope()) {
  if (warming?.scope === scope) return
  const job = { scope }
  warming = job
  const remaining = chats.slice(0, CHAT_CACHE_LIMIT)
  async function worker () {
    while (remaining.length && warming === job && scope === chatCacheScope()) {
      const chat = remaining.shift()
      const saved = await chatCache.get(chat.id, scope)
      if (saved && saved.chat.updated_at === chat.updated_at && !chat.answering && !saved.active_turn) continue
      try {
        const data = await api.chat(chat.id, AbortSignal.timeout(15000))
        if (warming === job && scope === chatCacheScope()) await chatCache.put(data, scope, saved)
      } catch { return }
    }
  }
  Promise.all([worker(), worker()]).finally(() => { if (warming === job) warming = null })
}