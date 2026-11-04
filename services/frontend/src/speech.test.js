import test from 'node:test'
import assert from 'node:assert/strict'
import { createRecorder, readSpeechEvents, wavAudio, joinTranscript } from './speech.js'

function stream (...chunks) {
  return new ReadableStream({ start (controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close() } })
}

test('live PCM is encoded as independently decodable, bounded mono WAV', async () => {
  const blob = wavAudio(new Float32Array([0, 0.5, -0.5]), 16000)
  const bytes = await blob.arrayBuffer()
  assert.equal(blob.type, 'audio/wav')
  assert.equal(blob.size, 50)
  const view = new DataView(bytes)
  assert.equal(view.getUint32(24, true), 16000)
  assert.equal(view.getUint32(40, true), 6)
  assert.equal(view.getInt16(46, true), 16384)
})

test('forced window overlap joins shared words without duplicating them', () => {
  assert.equal(joinTranscript('Fix the API endpoint.', 'API endpoint and tests.', true), 'Fix the API endpoint. and tests.')
  assert.equal(joinTranscript('Do it.', 'Do it.', false), 'Do it. Do it.')
  assert.equal(joinTranscript('Keep this.', '', true), 'Keep this.')
  assert.equal(joinTranscript('', ' First words. '), 'First words.')
})

test('dictation stream preserves split UTF-8 and requires a final completion frame', async () => {
  const data = new TextEncoder().encode('data: {"type":"delta","text":"Grüße"}\n\ndata: {"type":"done","text":"Grüße"}\n\n')
  const events = []
  await readSpeechEvents(stream(data.slice(0, 35), data.slice(35, 36), data.slice(36)), event => events.push(event))
  assert.equal(events[0].text, 'Grüße')
  assert.equal(events[1].type, 'done')
  await assert.rejects(readSpeechEvents(stream(new TextEncoder().encode('data: {"type":"delta","text":"partial"}\n\n')), () => {}), /interrupted/)
})

test('dictation rejects unbounded/malformed events', async () => {
  await assert.rejects(readSpeechEvents(stream(new TextEncoder().encode('x'.repeat(128 * 1024 + 1))), () => {}), /Invalid/)
  await assert.rejects(readSpeechEvents(stream(new TextEncoder().encode('data: not json\n\n')), () => {}), SyntaxError)
})

test('disposing a pending microphone permission request releases late tracks', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  const originalContext = globalThis.AudioContext
  const secure = globalThis.isSecureContext
  globalThis.isSecureContext = true
  globalThis.AudioContext = class {}
  let resolvePermission, stopped = 0, uploaded = 0
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: () => new Promise(resolve => { resolvePermission = resolve }) } } })
  const originalCancel = globalThis.cancelAnimationFrame
  globalThis.cancelAnimationFrame = () => {}
  try {
    const recorder = createRecorder({ onSnapshot: () => uploaded++, onError: error => { throw error } })
    const pending = recorder.start()
    assert.equal(recorder.state.value, 'requesting')
    recorder.dispose()
    resolvePermission({ getTracks: () => [{ stop () { stopped++ } }] })
    await pending
    assert.equal(stopped, 1)
    assert.equal(uploaded, 0)
    assert.equal(recorder.state.value, 'idle')
  } finally {
    if (previous) Object.defineProperty(globalThis, 'navigator', previous)
    else delete globalThis.navigator
    globalThis.AudioContext = originalContext
    globalThis.isSecureContext = secure
    globalThis.cancelAnimationFrame = originalCancel
  }
})
