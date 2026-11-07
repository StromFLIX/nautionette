import { ref } from 'vue'
import { Capacitor } from '@capacitor/core'

export const MAX_RECORDING_BYTES = 12 * 1024 * 1024

export function wavAudio (samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const label = (offset, text) => { for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i)) }
  label(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); label(8, 'WAVE'); label(12, 'fmt ')
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true)
  label(36, 'data'); view.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i++) view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, samples[i])) * 32767), true)
  return new Blob([buffer], { type: 'audio/wav' })
}

/** Join overlapping hard-split windows without repeating their shared words. */
export function joinTranscript (previous, next, overlap = false) {
  if (!previous) return next.trim()
  if (!next.trim()) return previous
  const left = previous.trim().split(/\s+/), right = next.trim().split(/\s+/)
  const normalize = word => word.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '')
  if (overlap) {
    for (let n = Math.min(15, left.length, right.length); n > 0; n--) {
      if (left.slice(-n).map(normalize).join(' ') === right.slice(0, n).map(normalize).join(' ')) {
        return [...left, ...right.slice(n)].join(' ')
      }
    }
  }
  return `${previous.trim()} ${next.trim()}`
}

/** Live PCM windows: fresh partials every 2s, pause boundaries, bounded 12s windows.
 * One-second overlap at forced boundaries protects words crossing the split.
 * The caller serializes/coalesces inference; no incomplete WebM fragments are sent.
 */
export function createRecorder ({ onSnapshot, onFinish, onError, maxSeconds = 120 }) {
  const state = ref('idle'), seconds = ref(0), level = ref(0)
  let generation = 0, stream, context, source, node, mute, timer, flushTimer
  let chunks = [], count = 0, total = 0, windowId = 0, overlap = false, silence = 0, lastPartial = 0
  let flushDone
  const release = () => {
    clearInterval(timer); clearTimeout(flushTimer)
    source?.disconnect(); node?.disconnect(); mute?.disconnect()
    if (node?.port) node.port.onmessage = null
    if (node) node.onaudioprocess = null
    stream?.getTracks().forEach(track => track.stop())
    stream = null
    context?.close().catch(() => {})
    context = null
    level.value = 0
  }
  const collect = () => {
    const audio = new Float32Array(count)
    let position = 0
    for (const chunk of chunks) { audio.set(chunk, position); position += chunk.length }
    return audio
  }
  const snapshot = (final, forced = false) => {
    if (!count) return
    const samples = collect()
    onSnapshot({ audio: wavAudio(samples, context.sampleRate), id: windowId, final, overlap })
    lastPartial = count
    if (final) {
      const tail = forced ? samples.slice(-context.sampleRate) : new Float32Array(0)
      chunks = tail.length ? [tail] : []; count = tail.length; lastPartial = count
      overlap = forced; silence = 0; windowId++
    }
  }
  const finish = () => {
    if (state.value !== 'stopping') return
    snapshot(true)
    release()
    chunks = []; count = 0
    state.value = 'idle'
    onFinish()
  }
  const stop = () => {
    if (state.value !== 'recording') return
    state.value = 'stopping'
    source?.disconnect()
    clearInterval(timer)
    if (node?.port) {
      flushDone = finish
      node.port.postMessage('flush')
      flushTimer = setTimeout(finish, 500)
    } else finish()
  }
  const cancel = () => {
    generation++
    release(); chunks = []; count = 0
    state.value = 'idle'
  }
  async function start () {
    if (state.value !== 'idle') return
    const id = ++generation
    state.value = 'requesting'
    try {
      const Context = globalThis.AudioContext || globalThis.webkitAudioContext
      if (!globalThis.isSecureContext || !navigator.mediaDevices?.getUserMedia || !Context) {
        throw new Error('Live dictation needs HTTPS and a browser with microphone and Web Audio support.')
      }
      const acquired = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
      if (id !== generation) { acquired.getTracks().forEach(track => track.stop()); return }
      stream = acquired
      context = new Context({ sampleRate: 16000 })
      await context.resume()
      if (id !== generation) return
      source = context.createMediaStreamSource(stream)
      chunks = []; count = 0; total = 0; silence = 0; lastPartial = 0; windowId = 0; overlap = false; seconds.value = 0
      const receive = data => {
        if (id !== generation || !['recording', 'stopping'].includes(state.value)) return
        const samples = new Float32Array(data)
        const rms = Math.sqrt(samples.reduce((sum, n) => sum + n * n, 0) / samples.length)
        level.value = Math.min(1, rms * 5)
        chunks.push(samples); count += samples.length; total += samples.length
        silence = rms < 0.012 ? silence + samples.length : 0
        // Bound memory even if a background tab's wall-clock timers are throttled.
        if (total >= context.sampleRate * maxSeconds && state.value === 'recording') { stop(); return }
        if (state.value === 'stopping') return
        const duration = count / context.sampleRate
        if (duration >= 12) snapshot(true, true)
        else if (duration >= 2 && silence / context.sampleRate >= 0.7) snapshot(true)
        else if (duration >= 1 && (count - lastPartial) / context.sampleRate >= 2) snapshot(false)
      }
      try {
        if (!context.audioWorklet || !globalThis.AudioWorkletNode) throw new Error('Use compatibility capture')
        await context.audioWorklet.addModule(new URL('./speech-worklet.js', import.meta.url))
        if (id !== generation) return
        node = new AudioWorkletNode(context, 'dictation-pcm')
        node.port.onmessage = event => event.data === 'flushed' ? flushDone?.() : receive(event.data)
      } catch {
        if (id !== generation) return
        // Older Safari/webviews: same PCM protocol, with main-thread capture.
        node = context.createScriptProcessor(4096, 1, 1)
        node.onaudioprocess = event => receive(event.inputBuffer.getChannelData(0))
      }
      mute = context.createGain(); mute.gain.value = 0
      source.connect(node); node.connect(mute); mute.connect(context.destination)
      state.value = 'recording'
      const started = Date.now()
      timer = setInterval(() => {
        seconds.value = Math.floor((Date.now() - started) / 1000)
        if (seconds.value >= maxSeconds) stop()
      }, 250)
    } catch (error) {
      if (id !== generation) return
      cancel()
      const permissionMessage = Capacitor.isNativePlatform()
        ? 'Microphone permission denied. Allow microphone access for Nautionette in your phone’s app settings and check that system microphone access is enabled, then try again.'
        : 'Microphone permission denied. Allow microphone access in your browser settings.'
      onError(new Error(error.name === 'NotAllowedError' ? permissionMessage : error.name === 'NotFoundError' ? 'No microphone found.' : error.message || 'Could not start recording.'))
    }
  }
  return { state, seconds, level, start, stop, cancel, dispose: cancel }
}

/** Decode bounded newline-delimited SSE frames, including split UTF-8 characters. */
export async function readSpeechEvents (body, onEvent) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', doneEvent = false
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      if (buffer.length > 128 * 1024) throw new Error('Invalid dictation response.')
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        const event = JSON.parse(line.slice(5))
        if (event.type === 'done') doneEvent = true
        onEvent(event)
      }
      if (done) break
    }
    if (!doneEvent) throw new Error('Dictation was interrupted. Please try again.')
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
