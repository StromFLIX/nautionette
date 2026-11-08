import test from 'node:test'
import assert from 'node:assert/strict'
import { connectSpeechStream } from './speech-stream.js'

function fixture () {
  const sockets = [], events = [], abort = new AbortController()
  class Socket {
    constructor (url) { this.url = url; this.readyState = 1; this.bufferedAmount = 0; this.sent = []; sockets.push(this) }
    send (data) { this.sent.push(data) }
    close () { this.closed = true }
    receive (event) { this.onmessage({ data: JSON.stringify(event) }) }
  }
  const pending = connectSpeechStream('wss://our-server/api/speech/stream', 'single-use-ticket', { signal: abort.signal, onEvent: event => events.push(event), WebSocketImpl: Socket })
  const socket = sockets[0]
  socket.onopen()
  return { socket, pending, events, abort }
}

test('auth is a first-frame ticket, audio is PCM16 exactly once, Stop sends only end', async () => {
  const { socket, pending, events } = fixture()
  assert.equal(socket.url, 'wss://our-server/api/speech/stream')
  assert.deepEqual(JSON.parse(socket.sent[0]), { type: 'auth', ticket: 'single-use-ticket' })
  socket.receive({ type: 'ready' })
  const session = await pending
  session.send(new Float32Array([0, 0.5, -0.5]), 16000)
  assert.deepEqual(JSON.parse(socket.sent[1]), { type: 'format', sample_rate: 16000 })
  const audio = new DataView(socket.sent[2])
  assert.equal(audio.byteLength, 10)
  assert.equal(audio.getUint32(0, true), 0)
  assert.equal(audio.getInt16(6, true), 16384)
  assert.equal(audio.getInt16(8, true), -16383)
  socket.receive({ type: 'transcript', revision: 1, text: 'Grüße' })
  assert.equal(events[0].text, 'Grüße')
  const final = session.finish()
  assert.equal(session.finish(), final)
  assert.deepEqual(JSON.parse(socket.sent[3]), { type: 'end', sequence: 1 })
  socket.receive({ type: 'done', revision: 2, text: 'Grüße.' })
  assert.equal(await final, 'Grüße.')
  assert.equal(socket.sent.filter(x => x instanceof ArrayBuffer).length, 1)
  assert.equal(socket.closed, true)
  socket.onclose()
  assert.equal(events.length, 1, 'a normal final close is not an error')
})

test('cancelled handshakes and completions release the connection without replay', async () => {
  let f = fixture()
  f.abort.abort()
  await assert.rejects(f.pending, { name: 'AbortError' })
  assert.equal(f.socket.closed, true)
  f = fixture()
  f.socket.receive({ type: 'ready' })
  const session = await f.pending
  const final = session.finish()
  session.cancel()
  await assert.rejects(final, { name: 'AbortError' })
  f.socket.receive({ type: 'done', revision: 1, text: 'late text' })
  assert.deepEqual(f.events, [])
})

test('slow connections and unsupported sample rates never silently drop audio', async () => {
  const { socket, pending } = fixture()
  socket.receive({ type: 'ready' })
  const session = await pending
  assert.throws(() => session.send(new Float32Array(1), 192000), /sample rate/)
  socket.bufferedAmount = 256 * 1024 + 1
  assert.throws(() => session.send(new Float32Array(1), 16000), /too slow/)
  session.cancel()
})

for (const bad of [{ type: 'transcript', revision: 1, text: 'stale' }, { type: 'transcript', revision: 2, text: 'x'.repeat(16001) }, { type: 'error', text: 'upstream secret' }]) test('invalid responses are fenced and sanitized: ' + bad.type + '/' + bad.revision, async () => {
  const { socket, pending, events } = fixture()
  socket.receive({ type: 'ready' })
  await pending
  socket.receive({ type: 'transcript', revision: 1, text: 'acknowledged' })
  socket.receive(bad)
  assert.equal(socket.closed, true)
  assert.equal(events[0].text, 'acknowledged')
  assert.equal(events[1].type, 'error')
  assert.doesNotMatch(events[1].message, /upstream secret/)
})
