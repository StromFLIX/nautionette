/**
 * One shared reactive store. Every view reads the same lists, and the live
 * event stream is what keeps them fresh, so nothing polls.
 */
import { computed, reactive } from 'vue'
import { api, auth, isNative, liveEvents, server } from './api'

const state = reactive({
  ready: false,
  needsToken: false,
  // The app ships without a backend, so it cannot start until it is told where one is.
  needsServer: isNative && !server.url,
  system: { components: [], agent_sets: [] },
  catalog: { agent_sets: [], models: [], tools: [], default_model: '', default_agent_set: 'default', context_window: 24000 },
  chats: [],
  workflows: [],
  drafts: [],
  runs: [],
  events: [],
  settingsOpen: false,
  settingsTab: 'general'
})

const listeners = new Set()
let source = null

function emit (event) {
  listeners.forEach((fn) => fn(event))
}

export function onLiveEvent (fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

async function guard (loader) {
  try {
    return await loader()
  } catch (error) {
    if (error.status === 401) state.needsToken = true
    else if (!error.status) state.needsServer = true  // never reached the instance
    else throw error
    return null
  }
}

export const actions = {
  async loadSystem () {
    const data = await guard(() => api.system())
    if (data) state.system = data
  },

  async loadCatalog (refresh = false) {
    const data = await guard(() => api.catalog(refresh))
    if (data) state.catalog = data
  },

  async loadChats () {
    const data = await guard(() => api.chats())
    if (data) state.chats = data.chats
  },

  async loadWorkflows () {
    const [workflows, drafts] = await Promise.all([
      guard(() => api.workflows()),
      guard(() => api.drafts())
    ])
    if (workflows) state.workflows = workflows.workflows
    if (drafts) state.drafts = drafts.drafts
  },

  async loadRuns () {
    const data = await guard(() => api.runs())
    if (data) state.runs = data.runs
  },

  async loadEvents () {
    const data = await guard(() => api.events())
    if (data) state.events = data.events.slice().reverse()
  },

  async refreshAll () {
    if (state.needsServer) return
    await Promise.all([
      actions.loadSystem(),
      actions.loadCatalog(),
      actions.loadChats(),
      actions.loadWorkflows(),
      actions.loadRuns()
    ])
    state.ready = true
  },

  openSettings (tab = 'general') {
    state.settingsTab = tab
    state.settingsOpen = true
  },

  closeSettings () {
    state.settingsOpen = false
  },

  setServer (value) {
    server.url = value
    state.needsServer = isNative && !server.url
    actions.connect()
    return actions.refreshAll()
  },

  setToken (value) {
    auth.token = value
    state.needsToken = false
    actions.connect()
    return actions.refreshAll()
  },

  connect () {
    if (state.needsServer) return
    source?.close()
    source = liveEvents((event) => {
      state.events.unshift(event)
      state.events = state.events.slice(0, 200)
      const kind = event.kind || ''
      if (kind.startsWith('run.')) actions.loadRuns()
      if (kind.startsWith('workflow.') || kind.startsWith('promote.')) actions.loadWorkflows()
      if (kind.startsWith('chat.')) actions.loadChats()
      if (kind === 'model.integration.changed') actions.loadCatalog(true)
      emit(event)
    })
  },

  disconnect () {
    source?.close()
    source = null
  }
}

export const store = state

export const health = computed(() => {
  const components = state.system.components || []
  if (!components.length) return 'unknown'
  return components.every((component) => component.status === 'ok') ? 'ok' : 'degraded'
})

export const draftCount = computed(() => state.drafts.length)

/** What this model can actually be handed, using the backend's own arithmetic. */
export function historyBudget (modelId) {
  const context = state.catalog.context || {}
  if (context.override) return context.override
  const id = modelId || state.catalog.default_model
  const model = (state.catalog.models || []).find((item) => item.id === id)
  if (model?.context_length) {
    return Math.round(model.context_length * context.chars_per_token * context.history_share)
  }
  return context.fallback || 200000
}
