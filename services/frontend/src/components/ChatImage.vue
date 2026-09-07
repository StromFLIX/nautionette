<template>
  <div class="chat-image">
    <button v-if="url" class="chat-image__open" type="button" :aria-label="`View ${image.name}`" @click="expanded = true">
      <img :src="url" :alt="image.name || 'Attached image'" @error="error = 'Could not display image'" />
    </button>
    <span v-else class="caption" role="status">{{ error || 'Loading image…' }}</span>
    <button v-if="error" type="button" class="btn btn--sm" @click="load">Retry image</button>
    <q-dialog v-model="expanded">
      <div class="chat-image__dialog">
        <button v-close-popup type="button" class="btn btn--icon" aria-label="Close image">
          <span class="material-icons">close</span>
        </button>
        <img :src="url" :alt="image.name || 'Attached image'" />
        <div class="caption">{{ image.name }}</div>
      </div>
    </q-dialog>
  </div>
</template>

<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({ image: { type: Object, required: true }, chatId: { type: String, default: '' } })
const url = ref('')
const error = ref('')
const expanded = ref(false)
let controller
function cleanup () {
  controller?.abort()
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
}
async function load () {
  cleanup()
  error.value = ''
  const request = new AbortController()
  controller = request
  try {
    const blob = props.image.file || await api.image(props.chatId, props.image.id, request.signal)
    if (!request.signal.aborted) url.value = URL.createObjectURL(blob)
  } catch (err) {
    if (!request.signal.aborted) error.value = `Could not load image: ${err.message}`
  }
}
watch(() => [props.chatId, props.image.id], load, { immediate: true })
onUnmounted(cleanup)
</script>

<style scoped>
.chat-image { min-width: 0; }
.chat-image__open { display: block; padding: 0; border: 0; border-radius: 8px; background: transparent; cursor: zoom-in; overflow: hidden; }
.chat-image__open img { display: block; max-width: 100%; width: auto; height: 100px; object-fit: contain; }
.chat-image__dialog { padding: 12px; background: var(--surface); border-radius: 12px; max-width: 95vw !important; }
.chat-image__dialog > button { display: flex; margin-left: auto; }
.chat-image__dialog img { display: block; max-width: 90vw; max-height: 80vh; object-fit: contain; }
</style>
