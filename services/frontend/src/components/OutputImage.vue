<template>
  <div ref="element" class="output-image">
    <button v-if="url && !error" type="button" class="output-image__open" :aria-label="`View ${file.name}`" @click="expanded = true">
      <img :src="url" :alt="file.name" @error="error = 'Could not display preview'" />
    </button>
    <p v-else class="caption dim" role="status">{{ error || 'Loading preview…' }}</p>
    <button v-if="error" type="button" class="btn btn--sm" @click="load">Retry preview</button>
    <q-dialog v-model="expanded">
      <div class="output-image__dialog">
        <button v-close-popup type="button" class="btn btn--icon" aria-label="Close preview"><span class="material-icons" aria-hidden="true">close</span></button>
        <img :src="url" :alt="file.name" />
        <p class="caption">{{ file.name }}</p>
      </div>
    </q-dialog>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({
  chatId: { type: String, required: true },
  outputId: { type: String, required: true },
  revision: { type: Number, required: true },
  file: { type: Object, required: true }
})
const element = ref(null)
const url = ref('')
const error = ref('')
const expanded = ref(false)
let controller, observer
let visible = false
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
    const blob = await api.outputFile(props.chatId, props.outputId, props.revision, props.file.id, {
      preview: true, signal: AbortSignal.any([request.signal, AbortSignal.timeout(30000)])
    })
    if (!request.signal.aborted) url.value = URL.createObjectURL(new Blob([blob], { type: 'image/png' }))
  } catch (err) {
    if (!request.signal.aborted) error.value = `Could not load preview: ${err.message}`
  }
}
watch([() => props.chatId, () => props.outputId, () => props.revision, () => props.file.id], () => {
  cleanup()
  error.value = ''
  expanded.value = false
  if (visible) load()
})
onMounted(() => {
  observer = new IntersectionObserver((entries) => {
    if (entries.some(entry => entry.isIntersecting)) {
      visible = true
      observer.disconnect()
      load()
    }
  }, { rootMargin: '100px' })
  observer.observe(element.value)
})
onUnmounted(() => { observer?.disconnect(); cleanup() })
</script>

<style scoped>
.output-image { min-width: 0; }
.output-image__open { display: block; width: 100%; border: 0; padding: 0; border-radius: var(--radius-md); background: var(--surface-input); overflow: hidden; cursor: zoom-in; }
.output-image__open img { display: block; width: 100%; max-height: 480px; object-fit: contain; }
.output-image__dialog { padding: 12px; background: var(--surface); border-radius: var(--radius-lg); max-width: 95vw !important; overflow-wrap: anywhere; }
.output-image__dialog > button { display: flex; margin-left: auto; }
.output-image__dialog img { display: block; max-width: 90vw; max-height: 80vh; object-fit: contain; }
</style>
