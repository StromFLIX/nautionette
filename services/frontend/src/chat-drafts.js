import { onUnmounted, ref, toRaw, watch } from 'vue'
import { api } from './api.js'
import { uploadAttachments } from './attachments.js'
import { chatCacheScope } from './chat-cache.js'
import { createDraftStorage, createDraftStore } from './draft-storage.js'

export const chatDrafts = createDraftStore({
  storage: createDraftStorage(), scope: chatCacheScope,
  remote: { get: api.chatDraft, put: api.saveChatDraft },
  upload: (id, attachments) => uploadAttachments(id, attachments, api.uploadAttachment)
})

// Timers and queued writes belong to the store, not a mounted chat component.
// Navigating away cannot cancel a save or an attachment upload.
window.addEventListener('online', () => chatDrafts.retry())
setInterval(() => chatDrafts.retry(), 15000)

export function useChatDraft (key) {
  const text = ref(''), attachments = ref([]), error = ref('')
  let current, applying = false
  function apply () {
    if (!current) return
    applying = true
    text.value = current.text
    attachments.value = current.attachments.map(item => ({ ...item }))
    error.value = current.localError || current.error
    applying = false
  }
  const unsubscribe = chatDrafts.subscribe(changed => { if (changed === current?.key) apply() })
  watch(key, id => {
    if (current) chatDrafts.sync(current.id)
    current = chatDrafts.entry(id)
    apply()
    chatDrafts.open(id)
  }, { immediate: true, flush: 'sync' })
  watch([text, attachments], () => {
    if (!applying && current) {
      chatDrafts.save(current.id, text.value, attachments.value.map(item => ({ ...toRaw(item) })))
    }
  }, { flush: 'sync' })
  function resume () {
    if (document.visibilityState === 'visible' && current) chatDrafts.sync(current.id)
  }
  document.addEventListener('visibilitychange', resume)
  onUnmounted(() => {
    unsubscribe()
    document.removeEventListener('visibilitychange', resume)
    if (current) chatDrafts.sync(current.id)
  })
  return { text, attachments, error }
}
