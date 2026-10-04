<template>
  <ChatImage v-if="isImage(attachment)" :image="attachment" :chat-id="chatId" />
  <div v-else class="chat-file">
    <button class="chat-file__download" type="button" :disabled="loading" :aria-label="`Download ${attachment.name}`" @click="download">
      <span class="material-icons" aria-hidden="true">description</span>
      <span class="chat-file__details"><strong>{{ attachment.name }}</strong><span class="caption">{{ loading ? 'Downloading…' : `${Math.ceil(attachment.size / 1024)} KiB · Download` }}</span></span>
    </button>
    <span v-if="error" class="caption" role="alert">{{ error }}</span>
  </div>
</template>

<script setup>
import { onUnmounted, ref } from 'vue'
import ChatImage from './ChatImage.vue'
import { api } from '../api'
import { isImage } from '../attachments'

const props = defineProps({ attachment: { type: Object, required: true }, chatId: { type: String, default: '' } })
const loading = ref(false)
const error = ref('')
let controller, url
async function download () {
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  try {
    const file = props.attachment.file || await api.attachment(props.chatId, props.attachment.id, request.signal)
    if (request.signal.aborted) return
    if (url) URL.revokeObjectURL(url)
    // Never embed HTML, SVG or other active content in the application origin.
    url = URL.createObjectURL(new Blob([file], { type: 'application/octet-stream' }))
    const link = document.createElement('a')
    link.href = url
    link.download = props.attachment.name || 'attachment'
    document.body.appendChild(link)
    link.click()
    link.remove()
  } catch (err) {
    if (!request.signal.aborted) error.value = `Could not download file: ${err.message}. Try again.`
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}
onUnmounted(() => { controller?.abort(); if (url) URL.revokeObjectURL(url) })
</script>

<style scoped>
.chat-file { min-width: 0; max-width: 280px; }
.chat-file__download { display: flex; align-items: center; gap: 8px; width: 100%; padding: 10px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: var(--surface-input); font: inherit; text-align: left; cursor: pointer; }
.chat-file__download:disabled { opacity: 0.6; cursor: wait; }
.chat-file__details { display: flex; flex-direction: column; gap: 4px; min-width: 0; overflow-wrap: anywhere; }
</style>
