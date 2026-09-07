import { ref } from 'vue'
import { api, server } from './api'
import { createOutbox } from './outbox'
import { chatCache, chatCacheScope } from './chat-cache'

export const pendingMessages = ref([])
const listeners = new Set()
let outbox = null
let timer = null

export function onDelivery (listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function flush () {
  outbox?.flush().catch(() => {})
}

function resume () {
  if (document.visibilityState === 'visible') flush()
}

export const delivery = {
  connect () {
    delivery.disconnect()
    const scope = chatCacheScope()
    const current = createOutbox({
      storage: localStorage,
      prefix: `nautionette.outbox.${encodeURIComponent(server.url || location.origin)}.`,
      send: (item) => api.sendMessage(item.chatId, item.text, item.id, item.projectIds, item.attachments),
      changed: (items) => { if (outbox === current) pendingMessages.value = items },
      accepted: async (message) => {
        await chatCache.accept(message, scope)
        if (outbox === current) listeners.forEach((listener) => listener(message))
      }
    })
    outbox = current
    pendingMessages.value = outbox.items()
    timer = setInterval(flush, 1000)
    window.addEventListener('online', flush)
    window.addEventListener('storage', flush)
    document.addEventListener('visibilitychange', resume)
    flush()
  },

  disconnect () {
    clearInterval(timer)
    window.removeEventListener('online', flush)
    window.removeEventListener('storage', flush)
    document.removeEventListener('visibilitychange', resume)
    outbox = null
    pendingMessages.value = []
  },

  enqueue (chatId, text, projectIds, attachments) {
    if (!outbox) throw new Error('Connect to an instance before sending.')
    outbox.enqueue(chatId, text, projectIds, attachments)
    flush()
  },

  reconcile (messages) { outbox?.reconcile(messages) },
  retry (id) { outbox?.retry(id).catch(() => {}) },
  discard (id) { outbox?.discard(id) }
}