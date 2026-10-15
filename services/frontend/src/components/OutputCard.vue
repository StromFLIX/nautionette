<template>
  <article class="output-card" :class="{ 'output-card--compact': compact }" :aria-label="output.title">
    <header class="output-card__head">
      <span class="material-icons" aria-hidden="true">inventory_2</span>
      <div class="grow">
        <button v-if="compact" type="button" class="output-card__jump" :aria-label="`View output: ${output.title}`" @click="$emit('open', output)">{{ output.title }}</button>
        <h2 v-else>{{ output.title }}</h2>
        <p class="caption dim">Ready · Revision {{ output.revision }}<template v-if="output.source_app"> · {{ output.source_app }}</template></p>
      </div>
    </header>
    <p v-if="!compact && output.summary" class="output-card__summary">{{ output.summary }}</p>
    <div v-if="previews.length" class="output-card__previews">
      <OutputImage v-for="file in previews" :key="file.id" :chat-id="chatId" :output-id="output.id" :revision="output.revision" :file="file" />
    </div>
    <button v-if="compact" type="button" class="btn btn--sm btn--outline" @click="$emit('open', output)">View files in Outputs</button>
    <ul v-else class="output-card__files" aria-label="Output files">
      <li v-for="file in output.files" :key="file.id">
        <button type="button" class="output-card__download" :disabled="Boolean(loadingFile)" :aria-label="`Download ${file.name}`" @click="download(file)">
          <span class="material-icons" aria-hidden="true">{{ file.role === 'source' ? 'edit_document' : 'download' }}</span>
          <span class="output-card__file-details"><strong>{{ file.name }}</strong><span class="caption dim">{{ file.role === 'source' ? 'Editable source · ' : file.role === 'preview' ? 'Preview · ' : '' }}{{ formatSize(file.size) }} · {{ loadingFile === file.id ? 'Downloading…' : 'Download' }}</span></span>
        </button>
      </li>
    </ul>
    <p v-if="error" class="caption" role="alert">{{ error }}</p>
  </article>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import OutputImage from './OutputImage.vue'
import { api } from '../api'

const props = defineProps({
  chatId: { type: String, required: true },
  output: { type: Object, required: true },
  compact: { type: Boolean, default: false }
})
defineEmits(['open'])
const previews = computed(() => {
  const explicit = props.output.files.filter(file => file.role === 'preview' && file.previewable)
  const files = explicit.length ? explicit : props.output.files.filter(file => file.previewable)
  return props.compact ? files.slice(0, 1) : files
})
const formatSize = size => size >= 1024 * 1024 ? `${(size / (1024 * 1024)).toFixed(1)} MiB` : `${Math.max(1, Math.ceil(size / 1024))} KiB`
const loadingFile = ref('')
const error = ref('')
let controller, url
function cleanup () {
  controller?.abort()
  if (url) URL.revokeObjectURL(url)
  url = ''
  loadingFile.value = ''
}
async function download (file) {
  cleanup()
  const request = new AbortController()
  controller = request
  loadingFile.value = file.id
  error.value = ''
  try {
    const blob = await api.outputFile(props.chatId, props.output.id, props.output.revision, file.id, {
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(60000)])
    })
    if (request.signal.aborted) return
    // Never navigate to or embed generated HTML/SVG/PDF in the app origin.
    url = URL.createObjectURL(new Blob([blob], { type: 'application/octet-stream' }))
    const link = document.createElement('a')
    link.href = url
    link.download = file.name
    document.body.appendChild(link)
    link.click()
    link.remove()
  } catch (err) {
    if (!request.signal.aborted) error.value = `Could not download file: ${err.message}. Try again.`
  } finally {
    if (!request.signal.aborted) loadingFile.value = ''
  }
}
watch([() => props.chatId, () => props.output.id, () => props.output.revision], () => { cleanup(); error.value = '' })
onUnmounted(cleanup)
</script>

<style scoped>
.output-card { min-width: 0; padding: 16px; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface); overflow-wrap: anywhere; }
.output-card__head { display: flex; align-items: flex-start; gap: 10px; }
.output-card__head > .material-icons { color: var(--accent); font-size: 1.25rem; margin-top: 2px; }
.output-card__head h2, .output-card__head p { margin: 0 0 6px; }
.output-card__head h2 { font-size: 1rem; font-weight: 600; }
.output-card__summary { white-space: pre-wrap; font-size: 0.875rem; }
.output-card__previews { display: grid; gap: 12px; margin: 12px 0; }
.output-card__files { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); gap: 8px; list-style: none; margin: 12px 0 0; padding: 0; }
.output-card__files li { min-width: 0; }
.output-card__download { display: flex; align-items: center; gap: 8px; width: 100%; height: 100%; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-input); color: var(--text); text-align: left; font: inherit; cursor: pointer; }
.output-card__download:disabled { opacity: 0.6; cursor: wait; }
.output-card__file-details { display: flex; flex-direction: column; gap: 4px; min-width: 0; overflow-wrap: anywhere; }
.output-card__file-details strong { font-size: 0.875rem; }
.output-card__jump { padding: 0; border: 0; background: none; color: var(--accent); font: inherit; font-weight: 600; text-align: left; cursor: pointer; }
.output-card--compact { max-width: 360px; margin-top: 12px; padding: 12px; }
.output-card--compact :deep(.output-image__open img) { max-height: 160px; }
</style>
