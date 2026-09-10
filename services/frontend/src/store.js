/**
 * One shared reactive store. Every view reads the same lists, and the live
 * event stream is what keeps them fresh, so nothing polls.
 */
import { computed, reactive } from 'vue'
import { api, auth, isNative, liveEvents, server } from './api'
import { delivery } from './delivery'
import { chatCache, chatCacheScope, warmChatCache } from './chat-cache'
import { agentConfig, globalChatConfig, resolveAgentConfig } from './agent-config'
import { createChatSettings } from './new-chat-settings'
import { preferenceError } from './preferences'
import router from './router'

export const chatSettings = createChatSettings({ scope: chatCacheScope, onError: message => { preferenceError.value = message } })

const emptyCatalog = () => ({ agent_sets: [], agents: [], models: [], tools: [], default_model: '', default_agent_set: 'default', default_agent_id: null })
const state = reactive({
  ready: false,
  catalogLoaded: false,
  catalogError: '',
  needsToken: false,
  // The app ships without a backend, so it cannot start until it is told where one is.
  needsServer: isNative && !server.url,
  system: { components: [], agent_sets: [] },
  catalog: emptyCatalog(),
  chats: [],
  projects: [],
  workflows: [],
  drafts: [],
  runs: [],
  events: []
})

const listeners = new Set()
let source = null
let catalogRequest = 0

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
    else if (!error.status) return null
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
    const scope = chatCacheScope()
    const request = ++catalogRequest
    try {
      const data = await guard(() => api.catalog(refresh))
      if (scope !== chatCacheScope() || request !== catalogRequest) return false
      if (data) { state.catalog = data; state.catalogLoaded = true; state.catalogError = ''; return true }
      state.catalogError = 'Could not load chat defaults.'
    } catch (error) { if (scope === chatCacheScope() && request === catalogRequest) state.catalogError = error.message }
    return false
  },

  // Successful writes are authoritative even if subsequent discovery is offline.
  syncAgentCatalog (patch) {
    catalogRequest++ // An older discovery response must not undo this saved edit.
    const catalog = { ...state.catalog, ...patch }
    const defaults = globalChatConfig(catalog)
    catalog.agents = (catalog.agents || []).map(agent => ({ ...agent, resolved: resolveAgentConfig(agent.config || {}, defaults) }))
    catalog.chat_defaults = agentConfig(catalog, catalog.default_agent_id ?? null)
    state.catalog = catalog
  },

  applyAgentSettings (settings) {
    const defaults = globalChatConfig(settings)
    actions.syncAgentCatalog({ global_chat_defaults: defaults, default_model: defaults.model,
      default_agent_set: defaults.agent_set, default_agent_id: settings.default_agent_id ?? null })
  },

  async loadChats () {
    const scope = chatCacheScope()
    const data = await guard(() => api.chats())
    if (data && scope === chatCacheScope()) {
      state.chats = data.chats
      await chatCache.saveList(data.chats, scope)
      warmChatCache(data.chats, scope)
    }
  },

  async loadProjects () {
    const data = await guard(() => api.projects())
    if (data) state.projects = data.projects
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
      actions.loadProjects(),
      actions.loadWorkflows(),
      actions.loadRuns()
    ])
    state.ready = true
  },

  openSettings (tab = 'general') {
    return router.push(`/settings/${tab}`)
  },

  setServer (value) {
    state.catalogError = ''
    state.catalogLoaded = false
    state.catalog = emptyCatalog()
    server.url = value
    state.needsServer = isNative && !server.url
    actions.connect()
    return actions.refreshAll()
  },

  setToken (value) {
    state.catalogError = ''
    state.catalogLoaded = false
    state.catalog = emptyCatalog()
    auth.token = value
    state.needsToken = false
    actions.connect()
    return actions.refreshAll()
  },

  connect () {
    if (state.needsServer) return
    source?.close()
    const scope = chatCacheScope()
    state.chats = []
    chatCache.list(scope).then((chats) => {
      if (scope === chatCacheScope() && !state.chats.length) state.chats = chats || []
    })
    delivery.connect()
    emit({ kind: 'client.reconnect' })
    source = liveEvents((event) => {
      state.events.unshift(event)
      state.events = state.events.slice(0, 200)
      const kind = event.kind || ''
      if (kind === 'connected') actions.refreshAll()
      if (kind.startsWith('run.')) actions.loadRuns()
      if (kind.startsWith('workflow.') || kind.startsWith('promote.')) actions.loadWorkflows()
      if (kind.startsWith('chat.')) actions.loadChats()
      if (kind.startsWith('project.')) actions.loadProjects()
      if (['model.integration.changed', 'mcp.server.changed', 'settings.changed', 'agent.profile.changed'].includes(kind)) actions.loadCatalog(true)
      emit(event)
    })
  },

  disconnect () {
    source?.close()
    source = null
    delivery.disconnect()
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
