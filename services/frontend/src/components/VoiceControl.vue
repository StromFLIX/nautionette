<template>
  <div class="voice-control" :class="{ 'voice-control--active': active }">
    <template v-if="active">
      <button class="voice-control__cancel" type="button" aria-label="Cancel dictation" @click="cancel"><span class="material-icons" aria-hidden="true">close</span></button>
      <div v-if="recording" class="voice-control__wave" aria-hidden="true" :style="{ '--voice-level': level }">
        <i v-for="n in 9" :key="n" :style="{ height: `${5 + level * (n % 3 + 1) * 8}px` }" />
      </div>
      <span class="voice-control__status" role="status"><span v-if="recording" class="sr-only">Recording · </span>{{ recording ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : processing ? (editing ? 'Editing…' : 'Finalizing…') : 'Microphone…' }}</span>
    </template>
    <button class="voice-control__mic" :class="{ 'voice-control__mic--recording': recording }" type="button"
      :disabled="processing || state === 'requesting' || state === 'stopping' || (!recording && (disabled || !available))"
      :aria-label="recording ? 'Finish recording' : 'Dictate message'" :aria-pressed="recording"
      :aria-description="unavailable || undefined" @click="recording ? stop() : begin()">
      <span class="material-icons" aria-hidden="true">{{ recording ? 'stop' : processing ? 'hourglass_top' : 'mic' }}</span>
      <q-tooltip>{{ recording ? 'Finish recording · 2 minute limit' : unavailable || 'Dictate a message' }}</q-tooltip>
    </button>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import { createRecorder, joinTranscript } from '../speech'

const props = defineProps({ modelValue: { type: String, default: '' }, model: { type: String, default: '' }, disabled: Boolean, cleanup: Boolean, language: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue', 'active', 'processing', 'notice', 'completed'])
const available = ref(false), unavailable = ref('Checking dictation availability…')
const processing = ref(false), editing = ref(false)
let disposed = false, controller, deadline, base = '', inserted = '', committed = '', queue = [], working = false, ended = false
const { state, seconds, level, start, stop, cancel: cancelRecording, dispose } = createRecorder({ onSnapshot: enqueue, onFinish: endRecording, onError: fail })
const recording = computed(() => state.value === 'recording')
const active = computed(() => processing.value || state.value !== 'idle')
watch(active, value => emit('active', value), { flush: 'sync' })
watch(processing, value => emit('processing', value), { flush: 'sync' })

onMounted(async () => {
  try {
    const result = await api.speech()
    if (disposed) return
    available.value = Boolean(result.enabled && globalThis.isSecureContext && navigator.mediaDevices?.getUserMedia && (globalThis.AudioContext || globalThis.webkitAudioContext))
    unavailable.value = available.value ? '' : result.enabled ? 'Live dictation requires HTTPS and Web Audio support.' : 'Dictation is not configured on this server.'
  } catch { unavailable.value = 'Dictation is currently unavailable.' }
})
function append (text) { return base + (base && text && !/\s$/.test(base) ? '\n' : '') + text }
function publish (text) { inserted = text; emit('update:modelValue', append(text)) }
function begin () {
  base = props.modelValue; inserted = ''; committed = ''; queue = []; working = false; ended = false
  controller = new AbortController()
  emit('notice', '')
  clearTimeout(deadline)
  deadline = setTimeout(() => fail(new Error('Dictation timed out. Please try a shorter recording.')), 300000)
  start()
}
function cancel () {
  controller?.abort(); clearTimeout(deadline); queue = []
  cancelRecording()
  if (props.modelValue === append(inserted)) emit('update:modelValue', base)
  processing.value = false; editing.value = false
  emit('notice', '')
}
function fail (error) {
  if (disposed) return
  controller?.abort(); clearTimeout(deadline); queue = []
  cancelRecording(); processing.value = false; editing.value = false
  // Keep the text already recognized; never discard the pre-existing typed draft.
  emit('notice', error.message)
}
function enqueue (snapshot) {
  if (disposed || controller?.signal.aborted) return
  // Only the newest pending snapshot of a window matters. Final windows are never dropped.
  const last = queue.at(-1)
  if (last && last.id === snapshot.id && !last.final) queue[queue.length - 1] = snapshot
  else queue.push(snapshot)
  drain()
}
async function drain () {
  if (working) return
  const request = controller
  working = true
  try {
    while (queue.length && !request.signal.aborted) {
      const snapshot = queue.shift()
      let text = ''
      await api.transcribe(snapshot.audio, { language: props.language, signal: request.signal,
        onEvent (event) { if (event.type === 'done') text = event.text }
      })
      if (disposed || controller !== request || request.signal.aborted) return
      const combined = joinTranscript(committed, text, snapshot.overlap)
      if (snapshot.final) committed = combined
      publish(combined)
    }
  } catch (error) {
    if (!disposed && controller === request && !request.signal.aborted) fail(error)
  } finally {
    if (controller === request) {
      working = false
      if (ended && !request.signal.aborted) finishSession(request)
    }
  }
}
function endRecording () {
  ended = true; processing.value = true
  if (!working) finishSession(controller)
}
async function finishSession (request) {
  if (editing.value || request.signal.aborted) return
  const original = committed || inserted
  try {
    if (!original) { emit('notice', 'No speech detected. Please try again.'); return }
    if (!props.cleanup) return
    editing.value = true
    let edited = ''
    await api.cleanupDictation(original, { model: props.model, signal: request.signal,
      onEvent (event) {
        if (disposed || request.signal.aborted) return
        if (event.type === 'delta') { edited += event.text; publish(edited) }
        if (event.type === 'done') {
          publish(event.text)
          emit('notice', event.warning || '')
          if (event.text !== original) emit('completed', { original: append(original), cleaned: append(event.text) })
        }
      }
    })
  } catch {
    if (!disposed && controller === request && !request.signal.aborted) {
      publish(original)
      emit('notice', 'Cleanup interrupted; the original transcript was kept.')
    }
  } finally {
    if (controller === request) { clearTimeout(deadline); processing.value = false; editing.value = false }
  }
}
onUnmounted(() => { disposed = true; clearTimeout(deadline); controller?.abort(); dispose() })
</script>

<style scoped>
.voice-control { display: flex; align-items: center; min-width: 0; }
.voice-control__mic, .voice-control__cancel { display: grid; place-items: center; flex: none; width: 40px; min-height: 38px; border: 0; background: transparent; color: var(--text-muted); cursor: pointer; border-radius: var(--radius-sm); }
.voice-control__mic:hover:not(:disabled), .voice-control__cancel:hover { background: var(--surface-active); color: var(--text); }
.voice-control__mic:disabled { opacity: .45; cursor: default; }
.voice-control__mic--recording { color: var(--danger); background: var(--danger-soft); }
.voice-control__mic .material-icons { font-size: 20px; }
.voice-control__cancel { width: 28px; }
.voice-control__cancel .material-icons { font-size: 16px; }
.voice-control__status { font-size: .6875rem; color: var(--text-muted); white-space: nowrap; padding: 0 6px; font-variant-numeric: tabular-nums; }
.voice-control__wave { display: flex; align-items: center; justify-content: center; gap: 2px; width: 36px; height: 32px; }
.voice-control__wave i { display: block; width: 2px; border-radius: 2px; background: var(--accent); transition: height 80ms linear; max-height: 30px; }
@media (pointer: coarse) { .voice-control__mic, .voice-control__cancel { min-width: 44px; min-height: 44px; } }
@media (max-width: 380px) { .voice-control__wave { width: 24px; gap: 1px; } .voice-control__status { max-width: 88px; overflow: hidden; text-overflow: ellipsis; } }
@media (prefers-reduced-motion: reduce) { .voice-control__wave i { transition: none; } }
</style>
