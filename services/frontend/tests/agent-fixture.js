import { mockDesign } from './design-fixture.js'

const fields = ['model', 'agent_set', 'reasoning_effort', 'tools', 'project_ids']
export const projectId = 'a'.repeat(32)

export async function mockAgents (context) {
  const state = await mockDesign(context)
  Object.assign(state.settings, { default_agent_id: null, default_reasoning_effort: null, default_tools: null, default_project_ids: [] })
  state.factoryDefaults = structuredClone(state.settings)
  state.agents = []
  state.agentWrites = []
  state.created = []
  state.chatWrites = []
  state.chats = { alpha: state.data }
  state.pendingEvents = []
  state.catalog.models.push({ id: 'test/plain', name: 'Plain', provider: 'Test', gateway: 'gateway', reasoning_efforts: [] })
  const globalDefaults = () => Object.fromEntries(fields.map(key => [key, state.settings[`default_${key}`]]))
  const resolved = (overrides, base = globalDefaults()) => {
    const result = structuredClone({ ...base, ...overrides })
    if (result.model !== base.model && !Object.hasOwn(overrides, 'reasoning_effort')) result.reasoning_effort = null
    return result
  }
  const selection = (id) => {
    const agent = state.agents.find(item => item.id === id)
    if (id && !agent) throw new Error('Agent not found')
    return { agent_id: agent?.id ?? null, agent_name: agent?.name ?? null, ...resolved(agent?.config || {}) }
  }
  const catalog = () => ({
    ...state.catalog, default_model: state.settings.default_model, default_agent_set: state.settings.default_agent_set,
    default_agent_id: state.settings.default_agent_id, global_chat_defaults: globalDefaults(),
    agents: state.agents.map(agent => ({ ...agent, resolved: resolved(agent.config) })),
    chat_defaults: selection(state.settings.default_agent_id)
  })
  state.addAgent = (name, config = {}) => {
    const agent = { id: `agent-${state.agents.length + 1}`, name, description: '', config: structuredClone(config) }
    state.agents.push(agent)
    return agent
  }
  state.catalogSnapshot = catalog
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const body = method === 'GET' ? {} : route.request().postDataJSON() || {}
    const reply = (json, status = 200) => route.fulfill({ json, status })
    if (path === '/api/catalog') {
      state.catalogReads = (state.catalogReads || 0) + 1
      const snapshot = structuredClone(catalog())
      const failed = state.catalogFailure
      if (state.catalogGate) await state.catalogGate
      if (failed) return reply({ detail: 'Catalog unavailable' }, 503)
      return reply(snapshot)
    }
    if (path === '/api/events') {
      const events = state.pendingEvents.splice(0)
      return route.fulfill({ contentType: 'text/event-stream', body: `retry: 250\n${events.map(kind => `data: ${JSON.stringify({ kind })}\n\n`).join('')}\n` })
    }
    if (path === '/api/settings') {
      if (method === 'PUT') {
        state.writes.push(body)
        if (state.settingsFailure) return reply({ detail: 'Defaults could not be saved' }, 422)
        for (const [key, value] of Object.entries(body)) state.settings[key] = value ?? state.factoryDefaults[key]
      }
      return reply({ settings: state.settings, defaults: state.factoryDefaults })
    }
    if (path === '/api/projects') return reply({ projects: [{ id: projectId, full_name: 'owner/repository', status: 'ready' }] })
    if (path === '/api/agents') {
      if (method === 'POST') {
        state.agentWrites.push({ method, ...body })
        if (state.agentFailure) return reply({ detail: 'Agent could not be saved' }, 422)
        if (state.agents.some(agent => agent.name.toLowerCase() === body.name.toLowerCase())) return reply({ detail: 'An agent with that name already exists' }, 409)
        const agent = state.addAgent(body.name, body.config)
        agent.description = body.description
        return reply({ ...agent, resolved: resolved(agent.config) }, 201)
      }
      return reply(catalog())
    }
    if (path.startsWith('/api/agents/')) {
      const id = path.split('/').at(-1)
      const agent = state.agents.find(agent => agent.id === id)
      if (!agent) return reply({ detail: 'Agent not found' }, 404)
      state.agentWrites.push({ method, id, ...body })
      if (method === 'DELETE') {
        state.agents = state.agents.filter(agent => agent.id !== id)
        if (state.settings.default_agent_id === id) state.settings.default_agent_id = null
        return reply({ ok: true })
      }
      if (state.agentFailure) return reply({ detail: 'Agent could not be saved' }, 422)
      Object.assign(agent, body)
      return reply({ ...agent, resolved: resolved(agent.config) })
    }
    if (path === '/api/chats') {
      if (method === 'POST') {
        if (state.chatFailure) return reply({ detail: 'A selected project is no longer ready; update the project selection' }, 422)
        const config = selection(Object.hasOwn(body, 'agent_id') ? body.agent_id : state.settings.default_agent_id)
        const id = `created-${state.created.length + 1}`
        const chat = { id, title: 'New chat', read_revision: 0, ...resolved(body, config), updated_at: Date.now() / 1000 }
        state.created.push(body)
        state.chats[id] = { chat, messages: [], active_turn: null }
        return reply(chat)
      }
      return reply({ chats: Object.values(state.chats).map(data => data.chat) })
    }
    const match = path.match(/^\/api\/chats\/([^/]+)(?:\/(stream|messages|read-state))?$/)
    if (match) {
      const [, id, operation] = match
      const data = state.chats[id]
      if (!data) return reply({ detail: 'Chat not found' }, 404)
      if (operation === 'stream') return route.fulfill({ contentType: 'text/event-stream', body: `retry: 500\ndata: ${JSON.stringify({ type: 'snapshot', ...data })}\n\n` })
      if (operation === 'messages') {
        state.sent.push(body)
        const message = { id: body.message_id, chat_id: id, role: 'user', content: body.text, meta: {}, created_at: Date.now() / 1000 }
        data.messages.push(message)
        return reply({ message, turn_id: body.message_id }, 202)
      }
      if (operation === 'read-state') return reply(data.chat)
      if (method === 'PATCH') {
        state.chatWrites.push(body)
        if (state.chatPatchGate) await state.chatPatchGate
        if (state.chatPatchFailure) return reply({ detail: 'Chat settings could not be saved' }, 422)
        if (Object.hasOwn(body, 'agent_id')) Object.assign(data.chat, selection(body.agent_id))
        Object.assign(data.chat, body)
        return reply(data.chat)
      }
      return reply(data)
    }
    return route.fallback()
  })
  return state
}
