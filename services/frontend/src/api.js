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
  return options.responseType === 'blob' ? response.blob() : response.json()
}

const json = (body) => ({ body: JSON.stringify(body ?? {}) })

export const api = {
  health: () => request('/healthz', { signal: AbortSignal.timeout(5000) }),
  system: () => request('/api/system'),
  catalog: (refresh = false) => request(`/api/catalog${refresh ? '?refresh=true' : ''}`),
  events: () => request('/api/events/recent'),
  settings: () => request('/api/settings'),
  saveSettings: (payload) => request('/api/settings', { method: 'PUT', ...json(payload) }),
  modelIntegrations: () => request('/api/model-integrations'),
  addModelIntegration: (id, config = {}) =>
    request(`/api/model-integrations/${encodeURIComponent(id)}`, { method: 'PUT', ...json(config) }),
  removeModelIntegration: (id) =>
    request(`/api/model-integrations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  testModelIntegration: (id) =>
    request(`/api/model-integrations/${encodeURIComponent(id)}/test`, { method: 'POST' }),

  mcpServers: () => request('/api/mcp-servers'),
  saveMcpServer: (name, config = {}) =>
    request(`/api/mcp-servers/${encodeURIComponent(name)}`, { method: 'PUT', ...json(config) }),
  removeMcpServer: (name) =>
    request(`/api/mcp-servers/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  testMcpServer: (name) =>
    request(`/api/mcp-servers/${encodeURIComponent(name)}/test`, { method: 'POST' }),

  projects: () => request('/api/projects'),
  projectApp: () => request('/api/projects/github-app'),
  connectProjectApp: (payload) => request('/api/projects/github-app/connect', { method: 'POST', ...json(payload) }),
  projectRepositories: (page = 1) => request(`/api/projects/repositories?page=${page}`),
  addProject: (fullName) => request('/api/projects', { method: 'POST', ...json({ full_name: fullName }) }),
  removeProject: (id) => request(`/api/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  chats: () => request('/api/chats'),
  createChat: (payload) => request('/api/chats', { method: 'POST', ...json(payload) }),
  chat: (id, signal = AbortSignal.timeout(15000)) => request(`/api/chats/${id}`, { signal }),
  projectChanges: (id, signal) => request(`/api/chats/${encodeURIComponent(id)}/project-changes`, { signal }),
  uploadImage: (id, file) => request(`/api/chats/${id}/images?name=${encodeURIComponent(file.name)}`, {
    method: 'POST', body: file, headers: { 'Content-Type': file.type }, signal: AbortSignal.timeout(60000)
  }),
  image: (chatId, imageId, signal) => request(`/api/chats/${chatId}/images/${imageId}`, { responseType: 'blob', signal }),
  discardImage: (chatId, imageId) => request(`/api/chats/${chatId}/images/${imageId}`, { method: 'DELETE' }),
  sendMessage: (id, text, messageId, projectIds, attachments = []) => request(`/api/chats/${id}/messages`, {
    method: 'POST', headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(20000), ...json({ text, message_id: messageId, project_ids: projectIds,
      attachment_ids: attachments.map((image) => image.id), queue: true })
  }),
  stopChat: (id, turnId) => request(`/api/chats/${id}/stop`, { method: 'POST', ...json({ turn_id: turnId }) }),
  resumeChatQueue: (id) => request(`/api/chats/${id}/queue/resume`, { method: 'POST' }),
  discardQueuedMessage: (id, messageId) => request(`/api/chats/${id}/queue/${encodeURIComponent(messageId)}`, { method: 'DELETE' }),
  updateChat: (id, payload) => request(`/api/chats/${id}`, { method: 'PATCH', ...json(payload) }),
  regenerateChatTitle: (id) => request(`/api/chats/${id}/title/regenerate`, { method: 'POST' }),
  updateChatReadState: (id, payload) => request(`/api/chats/${id}/read-state`, { method: 'PATCH', ...json(payload) }),
  decideInternet: (id, turnId, allowed) => request(`/api/chats/${id}/internet`, {
    method: 'POST', ...json({ turn_id: turnId, allowed })
  }),
  deleteChat: (id) => request(`/api/chats/${id}`, { method: 'DELETE' }),

  workflows: () => request('/api/workflows'),
  workflow: (name) => request(`/api/workflows/${name}`),
  workflowSettings: (name, payload) =>
    request(`/api/workflows/${name}/settings`, { method: 'PATCH', ...json(payload) }),
  deleteWorkflow: (name) => request(`/api/workflows/${name}`, { method: 'DELETE' }),
  runWorkflow: (name, input) => request(`/api/workflows/${name}/run`, { method: 'POST', ...json({ input }) }),
  schedule: (name, schedule) => request(`/api/workflows/${name}/schedule`, { method: 'POST', ...json(schedule) }),
  unschedule: (name) => request(`/api/workflows/${name}/schedule`, { method: 'DELETE' }),
  validate: (name, code) => request('/api/workflows/validate', { method: 'POST', ...json({ name, code }) }),

  drafts: () => request('/api/drafts'),
  draft: (name) => request(`/api/drafts/${name}`),
  approveDraft: (name) => request(`/api/drafts/${name}/approve`, { method: 'POST' }),
  discardDraft: (name) => request(`/api/drafts/${name}`, { method: 'DELETE' }),

  runs: (workflow) => request(`/api/runs${workflow ? `?workflow=${encodeURIComponent(workflow)}` : ''}`),
  run: (id) => request(`/api/runs/${id}`),
  runGraph: (id, signal) => request(`/api/runs/${encodeURIComponent(id)}/graph`, { signal }),
  cancelRun: (id) => request(`/api/runs/${id}/cancel`, { method: 'POST' }),
  terminateRun: (id) => request(`/api/runs/${id}/terminate`, { method: 'POST', ...json({}) })
}

export function chatStream (chatId, onSnapshot, onError) {
  const path = `/api/chats/${encodeURIComponent(chatId)}/stream`
  const source = new EventSource(endpoint(auth.token ? `${path}?token=${encodeURIComponent(auth.token)}` : path))
  source.onmessage = (event) => {
    const data = JSON.parse(event.data)
    if (data.type === 'snapshot') onSnapshot(data)
  }
  source.onerror = onError
  return source
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
