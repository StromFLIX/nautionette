const TOKEN_KEY = 'nautionette.token'
const SERVER_KEY = 'nautionette.server'

/** The packaged app is served from its own webview origin and has no backend. */
export const isNative = Boolean(globalThis.Capacitor?.isNativePlatform?.())

export const server = {
  get url () {
    return localStorage.getItem(SERVER_KEY) || ''
  },
  set url (value) {
    const trimmed = (value || '').trim().replace(/\/+$/, '')
    if (trimmed) localStorage.setItem(SERVER_KEY, trimmed)
    else localStorage.removeItem(SERVER_KEY)
  }
}

/** Same-origin on the web, an absolute instance URL in the app. */
export function endpoint (path) {
  return `${server.url}${path}`
}

export const auth = {
  get token () {
    return localStorage.getItem(TOKEN_KEY) || ''
  },
  set token (value) {
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  }
}

function headers (extra = {}) {
  const out = { ...extra }
  if (auth.token) out.Authorization = `Bearer ${auth.token}`
  return out
}

export class ApiError extends Error {
  constructor (message, status) {
    super(message)
    this.status = status
  }
}

async function request (path, options = {}) {
  const response = await fetch(endpoint(path), {
    ...options,
    headers: headers(options.body ? { 'Content-Type': 'application/json', ...options.headers } : options.headers)
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* keep the status line */ }
    throw new ApiError(detail, response.status)
  }
  if (response.status === 204) return null
  return response.json()
}

const json = (body) => ({ body: JSON.stringify(body ?? {}) })

export const api = {
  system: () => request('/api/system'),
  catalog: (refresh = false) => request(`/api/catalog${refresh ? '?refresh=true' : ''}`),
  events: () => request('/api/events/recent'),

  chats: () => request('/api/chats'),
  createChat: (payload) => request('/api/chats', { method: 'POST', ...json(payload) }),
  chat: (id) => request(`/api/chats/${id}`),
  updateChat: (id, payload) => request(`/api/chats/${id}`, { method: 'PATCH', ...json(payload) }),
  deleteChat: (id) => request(`/api/chats/${id}`, { method: 'DELETE' }),

  workflows: () => request('/api/workflows'),
  workflow: (name) => request(`/api/workflows/${name}`),
  deleteWorkflow: (name) => request(`/api/workflows/${name}`, { method: 'DELETE' }),
  runWorkflow: (name, input) => request(`/api/workflows/${name}/run`, { method: 'POST', ...json({ input }) }),
  schedule: (name, cron, input) => request(`/api/workflows/${name}/schedule`, { method: 'POST', ...json({ cron, input }) }),
  unschedule: (name) => request(`/api/workflows/${name}/schedule`, { method: 'DELETE' }),
  validate: (name, code) => request('/api/workflows/validate', { method: 'POST', ...json({ name, code }) }),

  drafts: () => request('/api/drafts'),
  draft: (name) => request(`/api/drafts/${name}`),
  approveDraft: (name) => request(`/api/drafts/${name}/approve`, { method: 'POST' }),
  discardDraft: (name) => request(`/api/drafts/${name}`, { method: 'DELETE' }),

  runs: (workflow) => request(`/api/runs${workflow ? `?workflow=${encodeURIComponent(workflow)}` : ''}`),
  run: (id) => request(`/api/runs/${id}`),
  cancelRun: (id) => request(`/api/runs/${id}/cancel`, { method: 'POST' }),
  terminateRun: (id) => request(`/api/runs/${id}/terminate`, { method: 'POST', ...json({}) })
}

/** POST that streams server-sent events back, so a chat answer arrives as it is written. */
export async function streamMessage (chatId, text, onEvent) {
  const response = await fetch(endpoint(`/api/chats/${chatId}/messages`), {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text })
  })
  if (!response.ok || !response.body) {
    throw new ApiError(`stream failed: ${response.status}`, response.status)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index
    while ((index = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue
        try {
          onEvent(JSON.parse(line.slice(5).trim()))
        } catch { /* keep-alive or partial frame */ }
      }
    }
  }
}

/** Live system events. EventSource cannot set headers, so the token rides along. */
export function liveEvents (onEvent) {
  const url = endpoint(auth.token ? `/api/events?token=${encodeURIComponent(auth.token)}` : '/api/events')
  const source = new EventSource(url)
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data))
    } catch { /* ignore */ }
  }
  return source
}
