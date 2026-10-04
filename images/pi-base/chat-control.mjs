import { createServer } from 'node:net'

export function createChatControl({ send, emit }) {
  const requests = new Map()
  const queued = []
  let initialMessage = true
  let closed = false
  let stopping = false
  let recovering = false
  let recoveryInput = false

  function receive(event) {
    if (event.type === 'response' && requests.has(event.id)) {
      const request = requests.get(event.id)
      if (!event.success) {
        const index = queued.findIndex((item) => item.id === event.id)
        if (index >= 0) queued.splice(index, 1)
        request.resolve({ ok: false, error: event.error || 'Agent rejected the message' })
      } else request.resolve({ ok: true })
    }
    if (event.type === 'message_start' && event.message?.role === 'user') {
      // Only the internal recovery prompt can arrive here while steering is
      // held. It must never acknowledge a user's queued message by accident.
      if (recoveryInput) {
        recoveryInput = recovering = false
        return
      }
      if (initialMessage) initialMessage = false
      else {
        const message = queued.shift()
        if (message) emit({ type: 'input_consumed', id: message.id })
      }
    }
    // agent_end is only a low-level boundary: retries/compaction can follow.
    // The process owner closes controls after terminal settlement or exit.
  }

  function close() {
    closed = true
    for (const request of requests.values()) request.resolve({ ok: false, error: 'Agent finished' })
  }

  function command(input) {
    if (requests.has(input.id)) return requests.get(input.id).promise
    if (closed || stopping) return Promise.resolve({ ok: false, error: 'Agent is stopping or finished' })
    if (recovering && input.type !== 'stop') return Promise.resolve({ ok: false, error: 'Agent is recovering; keep this input queued' })
    let resolve
    const promise = new Promise((done) => { resolve = done })
    requests.set(input.id, { promise, resolve })
    if (input.type === 'stop') {
      stopping = true
      send({ type: 'clear_queue' })
      send({ id: input.id, type: 'abort' })
      emit({ type: 'interrupted' })
    } else if (input.type === 'steer' && typeof input.text === 'string' && input.text.trim()) {
      queued.push(input)
      send({ id: input.id, type: 'steer', message: input.text })
    } else resolve({ ok: false, error: 'Invalid chat command' })
    return promise
  }

  function beginRecovery() {
    // Do not race unacknowledged user inputs or a stop request. No new steering
    // is accepted until the internal prompt has entered the same Pi session.
    if (closed || stopping || recovering || queued.length) return false
    recovering = true
    return true
  }

  function continueRecovery(id, message) {
    if (closed || stopping || !recovering) return false
    recoveryInput = true
    send({ id, type: 'prompt', message })
    return true
  }

  function cancelRecovery() { recovering = recoveryInput = false }

  return { receive, command, close, beginRecovery, continueRecovery, cancelRecovery }
}

export function listenForChatControl(control, path = '/tmp/nautionette-chat.sock') {
  const server = createServer((socket) => {
    let buffer = ''
    socket.setEncoding('utf8')
    socket.setTimeout(10000, () => socket.destroy())
    socket.on('error', () => {})
    socket.on('data', async (chunk) => {
      buffer += chunk
      if (buffer.length > 256000) return socket.destroy()
      const boundary = buffer.indexOf('\n')
      if (boundary < 0) return
      const line = buffer.slice(0, boundary)
      buffer = ''
      try {
        const input = JSON.parse(line)
        if (typeof input.id !== 'string' || !input.id) throw new Error('Command ID is required')
        socket.end(JSON.stringify(await control.command(input)) + '\n')
      } catch (error) {
        socket.end(JSON.stringify({ ok: false, error: error.message }) + '\n')
      }
    })
  })
  server.listen(path)
  return server
}