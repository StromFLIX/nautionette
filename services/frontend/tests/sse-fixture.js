import { createServer } from 'node:http'

/** Persistent native SSE for UI fixtures. route.fulfill's EOF is a disconnect,
 * not a healthy stream, and must not be used as a high-frequency polling clock. */
export async function createSSEFixture (context) {
  const readers = new Map()
  let nextId = 0
  const server = createServer((request, response) => {
    const reader = readers.get(request.url)
    if (!reader) { response.writeHead(404); response.end(); return }
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache' })
    let previous = '', heartbeat = Date.now()
    function update () {
      const frame = reader.read()
      if (frame === null) { response.destroy(); return }
      if (frame !== previous || !reader.deduplicate) { response.write(frame); previous = frame }
      if (Date.now() - heartbeat >= 10_000) {
        response.write('event: heartbeat\ndata: {}\n\n')
        heartbeat = Date.now()
      }
    }
    update()
    const timer = setInterval(update, 100)
    response.on('close', () => { clearInterval(timer); readers.delete(request.url) })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  server.unref()
  context.on('close', () => { server.closeAllConnections(); server.close() })
  return async (route, read, { deduplicate = true } = {}) => {
    if (route.request().method() === 'HEAD') return route.fulfill({ status: 204 })
    const path = `/sse/${++nextId}`
    readers.set(path, { read, deduplicate })
    return route.fulfill({ status: 307, headers: { Location: `http://127.0.0.1:${server.address().port}${path}` } })
  }
}

export const snapshotFrame = data => `data: ${JSON.stringify({ type: 'snapshot', ...data })}\n\n`
