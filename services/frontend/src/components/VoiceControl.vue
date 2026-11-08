<template>
  <div class="voice-control" :class="{ 'voice-control--active': active }">
    <template v-if="active">
      <button class="voice-control__cancel" type="button" aria-label="Cancel dictation" @click="cancel"><span class="material-icons" aria-hidden="true">close</span></button>
      <div v-if="recording" class="voice-control__wave" aria-hidden="true" :style="{ '--voice-level': level }">
        <i v-for="n in 9" :key="n" :style="{ height: `${5 + level * (n % 3 + 1) * 8}px` }" />
      </div>
      <span class="voice-control__status" role="status"><span v-if="recording" class="sr-only">Recording · </span>{{ recording ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : processing ? 'Finalizing…' : 'Microphone…' }}</span>
    </template>
    <button class="voice-control__mic" :class="{ 'voice-control__mic--recording': recording }" type="button"
      :disabled="processing || connecting || state === 'requesting' || state === 'stopping' || (!recording && (disabled || !available))"
      :aria-label="recording ? 'Finish recording' : 'Dictate message'" :aria-pressed="recording"
      :aria-description="unavailable || undefined" @click="recording ? stop() : begin()">
      <span class="material-icons" aria-hidden="true">{{ recording ? 'stop' : processing ? 'hourglass_top' : 'mic' }}</span>
      <q-tooltip>{{ recording ? 'Finish recording · 2 minute limit' : unavailable || 'Dictate a message · processed locally' }}</q-tooltip>
    </button>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import { createRecorder } from '../speech'

const props = defineProps({ modelValue: { type: String, default: '' }, disabled: Boolean, language: { type: String, default: 'en' } })
const emit = defineEmits(['update:modelValue', 'active', 'notice'])
const available = ref(false), unavailable = ref('Checking dictation availability…')
const processing = ref(false), connecting = ref(false)
let disposed = false, controller, session, deadline, base = '', inserted = ''
const { state, seconds, level, start, stop, cancel: cancelRecording, dispose } = createRecorder({ onFinish: endRecording, onError: fail })
const recording = computed(() => state.value === 'recording')
const active = computed(() => connecting.value || processing.value || state.value !== 'idle')
watch(active, value => emit('active', value), { flush: 'sync' })

onMounted(async () => {
  try {
    await api.speech()
    if (disposed) return
    available.value = Boolean(globalThis.isSecureContext && navigator.mediaDevices?.getUserMedia && (globalThis.AudioContext || globalThis.webkitAudioContext) && globalThis.WebSocket)
    unavailable.value = available.value ? '' : 'Live dictation requires HTTPS and Web Audio support.'
  } catch { unavailable.value = 'Dictation is currently unavailable.' }
})
function append (text) { return base + (base && text && !/\s$/.test(base) ? '\n' : '') + text }
function publish (text) { inserted = text; emit('update:modelValue', append(text)) }
async function begin () {
  if (active.value || disposed) return
  base = props.modelValue; inserted = ''
  const request = controller = new AbortController()
  connecting.value = true
  emit('notice', '')
  clearTimeout(deadline)
  deadline = setTimeout(() => fail(new Error('Dictation timed out. Please try a shorter recording.')), 180000)
  try {
    const opened = await api.streamDictation(props.language, { signal: request.signal,
      onEvent (event) {
        if (disposed || controller !== request || request.signal.aborted) return
        if (event.type === 'transcript') publish(event.text)
        if (event.type === 'error') fail(new Error(event.message))
      }
    })
    if (disposed || controller !== request || request.signal.aborted) { opened.cancel(); return }
    session = opened
    // Request microphone access only once the private recognizer is ready.
    await start({ onAudio: (samples, rate) => opened.send(samples, rate) })
  } catch (error) {
    if (controller === request && !request.signal.aborted) fail(error)
  } finally {
    if (controller === request) connecting.value = false
  }
}
function cancel () {
  controller?.abort(); clearTimeout(deadline); session?.cancel(); session = null
  cancelRecording()
  if (props.modelValue === append(inserted)) emit('update:modelValue', base)
  processing.value = false; connecting.value = false
  emit('notice', '')
}
function fail (error) {
  if (disposed) return
  controller?.abort(); clearTimeout(deadline); session?.cancel(); session = null
  cancelRecording(); processing.value = false; connecting.value = false
  // Keep recognized text and the original typed draft. Never replay audio after failure.
  emit('notice', error.message)
}
async function endRecording () {
  const request = controller, opened = session
  processing.value = true
  try {
    const text = await opened.finish()
    if (disposed || controller !== request || request.signal.aborted) return
    publish(text)
    if (!text) emit('notice', 'No speech detected. Please try again.')
  } catch (error) {
    if (!disposed && controller === request && !request.signal.aborted) fail(error)
  } finally {
    if (controller === request) { clearTimeout(deadline); processing.value = false; session = null }
  }
}
onUnmounted(() => { disposed = true; clearTimeout(deadline); controller?.abort(); session?.cancel(); dispose() })
</script>

<style scoped>
.voice-control { display: flex; align-items: center; min-width: 0; }
.voice-control__mic, .voice-control__cancel { display: grid; place-items: center; flex: none; width: 40px; min-height: 38px; border: 0; background: transparent; color: var(--text-muted); cursor: pointer; border-radius: var(--radius-sm); }
.voice-control__mic:hover:not(:disabled), .voice-control__cancel:hover { background: var(--surface-active); color: var(--text); }
.voice-control__mic:disabled { opacity: .45; cursor: default; }
.voice-control__mic--recording { color: var(--danger); background: var(--danger-soft); }
.voice-control__mic .material-icons { font-size: 20px; }
.voice-control__cancel { width: 28px; min-height: 38px; font-size: 16px; }
.voice-control__status { font-size: .6875rem; color: var(--text-muted); white-space: nowrap; padding: 0 6px; font-variant-numeric: tabular-nums; }
.voice-control__wave { display: flex; align-items: center; justify-content: center; gap: 2px; width: 36px; height: 32px; }
.voice-control__wave i { display: block; width: 2px; border-radius: 2px; background: var(--accent); transition: height 80ms linear; max-height: 30px; }
@media (pointer: coarse) { .voice-control__mic, .voice-control__cancel { min-width: 44px; min-height: 44px; } }
@media (max-width: 380px) { .voice-control__wave { width: 24px; gap: 1px; } .voice-control__status { max-width: 88px; overflow: hidden; text-overflow: ellipsis; } }
@media (prefers-reduced-motion: reduce) { .voice-control__wave i { transition: none; } }
</style>
