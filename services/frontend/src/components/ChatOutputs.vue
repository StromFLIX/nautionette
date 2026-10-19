<template>
  <section class="chat-outputs scroll-y grow" aria-label="Chat outputs">
    <div class="chat-outputs__inner">
      <div v-if="!outputs.length" class="chat-outputs__empty">
        <span class="material-icons" aria-hidden="true">inventory_2</span>
        <h2>No outputs yet</h2>
        <p class="dim">Generated files and preview images will appear here when the agent publishes them. You can close the chat without interrupting its work.</p>
      </div>
      <template v-else>
        <div class="chat-outputs__controls">
          <label>Output
            <select :value="selected?.id" @change="selectOutput($event.target.value)">
              <option v-for="output in outputs" :key="output.id" :value="output.id">{{ output.title }}</option>
            </select>
          </label>
          <label>Revision
            <select v-model="revision">
              <option :value="null">Latest · {{ selected?.revision }}</option>
              <option v-for="item in history.filter(item => item.revision !== selected?.revision)" :key="item.revision" :value="item.revision">Revision {{ item.revision }}</option>
            </select>
          </label>
          <button v-if="selected?.revision > 1" type="button" class="btn btn--sm btn--outline" :disabled="loading" @click="loadHistory">{{ loading ? 'Loading…' : 'Load revision history' }}</button>
        </div>
        <p class="caption dim" role="status">{{ revision === null ? 'Showing the latest published snapshot. New revisions update here automatically.' : 'Viewing a saved revision. Select Latest to follow new results.' }}</p>
        <p v-if="working" class="caption dim" role="status">The agent is working. Only committed outputs are shown.</p>
        <p v-if="error" class="caption" role="alert">{{ error }}</p>
        <OutputCard v-if="displayed" :chat-id="chatId" :output="displayed" />
      </template>
    </div>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import OutputCard from './OutputCard.vue'
import { api } from '../api'
import { onLiveEvent } from '../store'

const props = defineProps({
  chatId: { type: String, required: true },
  outputs: { type: Array, default: () => [] },
  selectedId: { type: String, default: '' },
  selectedRevision: { type: Number, default: null },
  working: { type: Boolean, default: false }
})
const emit = defineEmits(['update:selectedId', 'update:selectedRevision'])
const selected = computed(() => props.outputs.find(output => output.id === props.selectedId) || props.outputs[0])
const revision = computed({ get: () => props.selectedRevision, set: value => emit('update:selectedRevision', value) })
const history = ref([])
const loading = ref(false)
const error = ref('')
const displayed = computed(() => revision.value === null ? selected.value : history.value.find(item => item.revision === revision.value))
let controller
function selectOutput (id) {
  emit('update:selectedRevision', null)
  emit('update:selectedId', id)
}
watch(() => selected.value?.id, id => {
  controller?.abort()
  history.value = []
  loading.value = false
  error.value = ''
  // Pin identity rather than position in a list reordered by publication. Keep
  // a historical revision across tab switches and links from older replies.
  if (id && id !== props.selectedId) selectOutput(id)
  else if (id && revision.value !== null) loadHistory()
}, { immediate: true })
async function loadHistory () {
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  try {
    const data = await api.outputRevisions(props.chatId, selected.value.id, AbortSignal.any([request.signal, AbortSignal.timeout(15000)]))
    if (!request.signal.aborted) {
      history.value = data.revisions
      if (revision.value !== null && !data.revisions.some(item => item.revision === revision.value)) {
        revision.value = null
        error.value = 'That revision was removed in Storage. Showing the latest retained snapshot.'
      }
    }
  } catch (err) {
    if (!request.signal.aborted) error.value = `Could not load revisions: ${err.message}. Try again.`
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}
const unsubscribe = onLiveEvent(event => {
  if (event.kind === 'chat.output.deleted' && event.chat_id === props.chatId && selected.value) {
    history.value = []
    loadHistory()
  }
})
onUnmounted(() => { controller?.abort(); unsubscribe() })
</script>

<style scoped>
.chat-outputs { min-height: 0; padding: 18px 24px; }
.chat-outputs__inner { max-width: calc(var(--content-width) + 48px); margin: 0 auto; }
.chat-outputs__controls { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.chat-outputs__controls label { display: flex; flex-direction: column; gap: 5px; min-width: 0; font-size: 0.75rem; color: var(--text-muted); }
.chat-outputs__controls label:first-child { flex: 1; }
.chat-outputs__controls select { max-width: 100%; min-height: 40px; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-input); color: var(--text); font: inherit; font-size: 0.875rem; }
.chat-outputs__empty { max-width: 460px; margin: 60px auto; text-align: center; }
.chat-outputs__empty > .material-icons { font-size: 2rem; color: var(--accent); }
.chat-outputs__empty h2 { font-size: 1.125rem; }
@media (max-width: 900px) {
  .chat-outputs { padding: 12px max(12px, env(safe-area-inset-right)) 12px max(12px, env(safe-area-inset-left)); }
}
</style>
