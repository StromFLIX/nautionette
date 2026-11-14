/** Opaque-origin documents only. Never serve generated executable assets on the host origin. */
const CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
function bootstrap () {
  let port, resolveReady
  const pending = new Map()
  let counter = 0
  const ready = new Promise(resolve => { resolveReady = resolve })
  const request = async (path, options = {}) => {
    await ready
    const id = String(++counter)
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('App request timed out; inspect state before retrying a mutation')) }, 40000)
      pending.set(id, { resolve, reject, timer })
      port.postMessage({ kind: 'request', id, path, method: options.method || 'GET', body: options.body ?? null, request_id: options.request_id || '' })
    })
  }
  const api = Object.freeze({ ready, request })
  Object.defineProperty(window, 'denkzeug', { value: api })
  // Saved mini-app source is user data. Keep the old API as the SAME bridge,
  // not a second connection or request stream; never rewrite published apps.
  Object.defineProperty(window, 'nautionette', { value: api })
  window.addEventListener('message', event => {
    if (port || event.source !== parent || !['denkzeug:init', 'nautionette:init'].includes(event.data?.kind) || !event.ports[0]) return
    port = event.ports[0]
    port.onmessage = ({ data }) => {
      if (data.kind === 'theme') {
        for (const [key, value] of Object.entries(data.tokens || {})) {
          if (/^--[a-z0-9-]+$/.test(key) && typeof value === 'string') document.documentElement.style.setProperty(key, value)
        }
        document.documentElement.style.colorScheme = data.dark ? 'dark' : 'light'
        if (/^[0-9]+%$/.test(data.fontSize)) document.documentElement.style.fontSize = data.fontSize
        window.dispatchEvent(new CustomEvent('denkzeug-theme', { detail: data.tokens }))
        window.dispatchEvent(new CustomEvent('nautionette-theme', { detail: data.tokens }))
      } else if (data.kind === 'response') {
        const item = pending.get(data.id)
        if (!item) return
        pending.delete(data.id); clearTimeout(item.timer)
        if (data.error) item.reject(new Error(data.error))
        else item.resolve(data.result)
      }
    }
    port.start()
    resolveReady()
  })
}
const script = value => String(value || '').replace(/<\/script/gi, '<\\/script')
const style = value => String(value || '').replace(/<\/style/gi, '<\\/style')
export function miniappDocument (frontend) {
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CSP}"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1"><script>(${bootstrap.toString()})()<\/script><style>html{min-height:100%}body{margin:0;overflow-wrap:anywhere}${style(frontend.css)}</style></head><body>${frontend.html || ''}<script>${script(frontend.js)}<\/script></body></html>`
}
export function requestID () {
  return `${Date.now()}-${crypto.randomUUID().replaceAll('-', '')}`
}
export function connectMiniapp (frame, invoke, theme) {
  const channel = new MessageChannel()
  const controllers = new Set()
  let closed = false, windowStart = Date.now(), count = 0
  channel.port1.onmessage = async ({ data }) => {
    if (closed || !data || data.kind !== 'request' || typeof data.id !== 'string' || data.id.length > 64) return
    const reply = result => { if (!closed) channel.port1.postMessage({ kind: 'response', id: data.id, ...result }) }
    if (Date.now() - windowStart > 60000) { windowStart = Date.now(); count = 0 }
    if (++count > 64 || controllers.size >= 4) return reply({ error: 'App request capacity reached' })
    let serialized
    try { serialized = JSON.stringify(data) } catch { return reply({ error: 'Provide JSON data, not cyclic objects or BigInt' }) }
    if (typeof data.path !== 'string' || !/^\/[a-zA-Z0-9/_.-]*$/.test(data.path) || data.path.split('/').includes('..') || !['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(data.method) || serialized.length > 48000) return reply({ error: 'Invalid app request' })
    const controller = new AbortController()
    controllers.add(controller)
    const timer = setTimeout(() => controller.abort(), 35000)
    try {
      const result = await invoke({ path: data.path, method: data.method, body: data.body ?? null, request_id: data.request_id || (data.method === 'GET' ? '' : requestID()) }, controller.signal)
      reply({ result })
    } catch (error) { reply({ error: error.message || 'App request failed; inspect state before retrying' }) }
    finally { clearTimeout(timer); controllers.delete(controller) }
  }
  channel.port1.start()
  frame.contentWindow.postMessage({ kind: 'denkzeug:init' }, '*', [channel.port2])
  const setTheme = value => { if (!closed) channel.port1.postMessage({ kind: 'theme', ...value }) }
  setTheme(theme)
  return { setTheme, close () { closed = true; channel.port1.close(); controllers.forEach(item => item.abort()); controllers.clear() } }
}
