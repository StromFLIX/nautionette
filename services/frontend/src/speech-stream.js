/** Ephemeral, bounded PCM16 streaming. Never reconnect/replay captured audio. */
export function connectSpeechStream (url, ticket, { signal, onEvent, WebSocketImpl = globalThis.WebSocket } = {}) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocketImpl(url)
    let ready = false, ended = false, cancelled = false, sequence = 0, rate, revision = -1, completion
    let timeout = setTimeout(() => fail(new Error('Live dictation connection timed out.')), 8000)
    function cleanup () { clearTimeout(timeout); signal?.removeEventListener('abort', cancel) }
    function fail (error) {
      if (ended || cancelled) return
      ended = true; cleanup(); socket.close()
      if (!ready) reject(error)
      else { completion?.reject(error); onEvent?.({ type: 'error', message: error.message }) }
    }
    function cancel () {
      if (cancelled) return
      cancelled = true; cleanup(); socket.close()
      const error = new DOMException('Dictation cancelled.', 'AbortError')
      if (!ready) reject(error)
      completion?.reject(error)
    }
    socket.onopen = () => {
      if (cancelled || ended) return
      // A single-use ticket goes in the first frame, not into an access-logged URL.
      socket.send(JSON.stringify({ type: 'auth', ticket }))
    }
    socket.onerror = () => fail(new Error('Live dictation connection unavailable.'))
    socket.onclose = () => fail(new Error('Live dictation interrupted; recognized text was kept.'))
    socket.onmessage = ({ data }) => {
      if (ended || cancelled) return
      try {
        if (typeof data !== 'string' || data.length > 20000) throw new Error('Invalid live dictation response.')
        const event = JSON.parse(data)
        if (!ready && event.type === 'ready') {
          ready = true; clearTimeout(timeout)
          resolve({
            send (samples, sampleRate) {
              if (ended || cancelled || completion || socket.readyState !== 1) throw new Error('Live dictation interrupted.')
              if (!Number.isInteger(sampleRate) || sampleRate < 8000 || sampleRate > 48000) throw new Error('Unsupported microphone sample rate.')
              if (socket.bufferedAmount > 256 * 1024) throw new Error('Live dictation connection is too slow; recognized text was kept.')
              if (!rate) { rate = sampleRate; socket.send(JSON.stringify({ type: 'format', sample_rate: rate })) }
              if (rate !== sampleRate || !samples.length || samples.length > 32768) throw new Error('Invalid microphone audio.')
              const frame = new ArrayBuffer(4 + samples.length * 2), view = new DataView(frame)
              view.setUint32(0, sequence++, true)
              for (let i = 0; i < samples.length; i++) view.setInt16(4 + i * 2, Math.round(Math.max(-1, Math.min(1, samples[i])) * 32767), true)
              socket.send(frame)
            },
            finish () {
              if (ended || cancelled) return Promise.reject(new Error('Live dictation interrupted.'))
              if (completion) return completion.promise
              let resolve, reject
              const promise = new Promise((yes, no) => { resolve = yes; reject = no })
              completion = { promise, resolve, reject }
              timeout = setTimeout(() => fail(new Error('Live dictation finalization timed out; recognized text was kept.')), 15000)
              try { socket.send(JSON.stringify({ type: 'end', sequence })) } catch { fail(new Error('Live dictation interrupted.')) }
              return promise
            },
            cancel
          })
          return
        }
        if (event.type === 'error') throw new Error('Live dictation unavailable; recognized text was kept.')
        if (!ready || !['transcript', 'done'].includes(event.type) || typeof event.text !== 'string' || event.text.length > 16000 || !Number.isInteger(event.revision) || event.revision <= revision) throw new Error('Invalid live dictation response.')
        revision = event.revision
        if (event.type === 'done') {
          if (!completion) throw new Error('Live dictation ended unexpectedly.')
          ended = true; cleanup(); completion.resolve(event.text); socket.close()
        } else onEvent?.(event)
      } catch (error) { fail(error) }
    }
    signal?.addEventListener('abort', cancel, { once: true })
    if (signal?.aborted) cancel()
  })
}
