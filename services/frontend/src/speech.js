import { ref } from 'vue'
import { Capacitor } from '@capacitor/core'
import { brand } from './brand.js'

/** Bounded live mono PCM capture. Stop flushes the last worklet block before ending input. */
export function createRecorder ({ onFinish, onError, maxSeconds = 120 }) {
  const state = ref('idle'), seconds = ref(0), level = ref(0)
  let generation = 0, stream, context, source, node, mute, timer, flushTimer, audioSink, total = 0
  const release = () => {
    clearInterval(timer); clearTimeout(flushTimer)
    source?.disconnect(); node?.disconnect(); mute?.disconnect()
    if (node?.port) node.port.onmessage = null
    if (node) node.onaudioprocess = null
    stream?.getTracks().forEach(track => track.stop())
    stream = null
    context?.close().catch(() => {})
    context = null; level.value = 0
  }
  const finish = () => {
    if (state.value !== 'stopping') return
    release(); state.value = 'idle'; onFinish()
  }
  const stop = () => {
    if (state.value !== 'recording') return
    state.value = 'stopping'; source?.disconnect(); clearInterval(timer)
    if (node?.port) {
      node.port.postMessage('flush')
      flushTimer = setTimeout(() => {
        if (state.value !== 'stopping') return
        cancel(); onError(new Error('Microphone flush interrupted; recognized text was kept.'))
      }, 1000)
    } else finish()
  }
  const cancel = () => { generation++; release(); state.value = 'idle' }
  async function start ({ onAudio } = {}) {
    if (state.value !== 'idle') return
    audioSink = onAudio
    const id = ++generation
    state.value = 'requesting'
    try {
      const Context = globalThis.AudioContext || globalThis.webkitAudioContext
      if (!globalThis.isSecureContext || !navigator.mediaDevices?.getUserMedia || !Context) throw new Error('Live dictation needs HTTPS and Web Audio support.')
      const acquired = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
      if (id !== generation) { acquired.getTracks().forEach(track => track.stop()); return }
      stream = acquired; context = new Context({ sampleRate: 16000 })
      await context.resume()
      if (id !== generation) return
      source = context.createMediaStreamSource(stream); total = 0; seconds.value = 0
      const receive = data => {
        if (id !== generation || !['recording', 'stopping'].includes(state.value)) return
        const samples = new Float32Array(data)
        if (!samples.length) return
        const remaining = context.sampleRate * maxSeconds - total
        const bounded = samples.subarray(0, Math.max(0, remaining))
        total += bounded.length
        level.value = Math.min(1, Math.sqrt(samples.reduce((sum, n) => sum + n * n, 0) / samples.length) * 5)
        try { if (bounded.length) audioSink(bounded, context.sampleRate) } catch (error) { cancel(); onError(error); return }
        if (total >= context.sampleRate * maxSeconds && state.value === 'recording') stop()
      }
      try {
        if (!context.audioWorklet || !globalThis.AudioWorkletNode) throw new Error('Use compatibility capture')
        await context.audioWorklet.addModule(new URL('./speech-worklet.js', import.meta.url))
        if (id !== generation) return
        node = new AudioWorkletNode(context, 'dictation-pcm')
        node.port.onmessage = event => event.data === 'flushed' ? finish() : receive(event.data)
      } catch {
        if (id !== generation) return
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
        ? `Microphone permission denied. Allow microphone access for ${brand.name} in your phone’s app settings and check that system microphone access is enabled, then try again.`
        : 'Microphone permission denied. Allow microphone access in your browser settings.'
      onError(new Error(error.name === 'NotAllowedError' ? permissionMessage : error.name === 'NotFoundError' ? 'No microphone found.' : error.message || 'Could not start recording.'))
    }
  }
  return { state, seconds, level, start, stop, cancel, dispose: cancel }
}
