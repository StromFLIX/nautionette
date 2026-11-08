import test from 'node:test'
import assert from 'node:assert/strict'
import { createRecorder } from './speech.js'
import { Capacitor } from '@capacitor/core'

test('microphone denial gives platform-specific guidance and remains retryable', async () => {
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  const originalContext = globalThis.AudioContext
  const secure = globalThis.isSecureContext
  const isNativePlatform = Capacitor.isNativePlatform
  globalThis.isSecureContext = true
  globalThis.AudioContext = class {}
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { async getUserMedia () { throw new DOMException('Denied', 'NotAllowedError') } } } })
  try {
    for (const native of [false, true]) {
      Capacitor.isNativePlatform = () => native
      const errors = []
      const recorder = createRecorder({ onError: error => errors.push(error.message) })
      await recorder.start()
      assert.equal(recorder.state.value, 'idle')
      assert.match(errors[0], native ? /phone’s app settings/ : /browser settings/)
      if (native) {
        assert.doesNotMatch(errors[0], /browser/)
        assert.match(errors[0], /system microphone access/)
      }
      await recorder.start()
      assert.equal(errors.length, 2, 'a denied request must not prevent another attempt')
      recorder.dispose()
    }
  } finally {
    if (navigatorDescriptor) Object.defineProperty(globalThis, 'navigator', navigatorDescriptor)
    else delete globalThis.navigator
    globalThis.AudioContext = originalContext
    globalThis.isSecureContext = secure
    Capacitor.isNativePlatform = isNativePlatform
  }
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
    const recorder = createRecorder({ onError: error => { throw error } })
    const pending = recorder.start({ onAudio: () => uploaded++ })
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
