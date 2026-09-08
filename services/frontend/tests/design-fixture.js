export async function mockDesign (context) {
  const now = Date.now() / 1000
  const settings = { default_model: 'test/model', default_agent_set: 'default', history_chars: 0,
    git_authorship_mode: 'automation', git_human_name: '', git_human_email: '',
    git_automation_name: 'Nautionette', git_automation_email: 'nautionette@users.noreply.github.com' }
  const graph = {
    mode: 'definition', warnings: [],
    nodes: [
      { id: 'start', kind: 'workflow', label: 'Morning digest', details: [] },
      { id: 'fetch', kind: 'activity', label: 'Read updates', activity_type: 'http_fetch', details: [{ label: 'Method', value: 'GET' }] },
      { id: 'done', kind: 'return', label: 'Deliver digest', details: [] }
    ],
    edges: [{ id: 'start-fetch', source: 'start', target: 'fetch' }, { id: 'fetch-done', source: 'fetch', target: 'done' }]
  }
  const workflow = { name: 'digest', title: 'Morning digest', description: 'The updates that matter.', graph, code: `# A deliberately long source line\nsource = '${'a'.repeat(250)}'\nasync def digest():\n    return source\n`, manifest: { inputs: { type: 'object', properties: {} } }, runs: [], settings: {} }
  const data = {
    chat: { id: 'alpha', title: 'Design review', agent_set: 'default', model: 'test/model', tools: null, project_ids: [], updated_at: now, read_revision: 0, last_read_message_id: 'answer' },
    messages: [
      { id: 'question', role: 'user', content: 'Make room for what matters.', meta: {}, created_at: now - 120 },
      { id: 'answer', role: 'assistant', content: '## A quieter workspace\n\nKeep the power. Lose the clutter.\n\n```javascript\nconst theme = "orbit"\n```', meta: {}, created_at: now - 60 }
    ], active_turn: null
  }
  const state = { settings, data, writes: [], sent: [], workflow }
  const catalog = {
    default_model: 'test/model', default_agent_set: 'default',
    models: [{ id: 'test/model', name: 'Astra', provider: 'Test', gateway: 'gateway', supports_images: true, context_length: 128000, reasoning_efforts: ['low', 'high'] }],
    agent_sets: [{ name: 'default', ready: true }, { name: 'research', ready: true }],
    tools: [{ name: 'mail_search', server: 'mail', description: 'Search your mail' }, { name: 'mail_read', server: 'mail', description: 'Read a message' }],
    tool_servers: [{ name: 'mail', status: 'ok' }]
  }
  state.catalog = catalog
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const reply = json => route.fulfill({ json })
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/events/recent') return reply({ events: [{ kind: 'workflow.completed', at: now, workflow: 'digest' }] })
    if (path === '/api/system') return reply({ version: 'test', components: [{ name: 'temporal', status: 'ok', detail: 'temporal:7233' }], agent_sets: catalog.agent_sets, model_key_present: true, auth_enabled: true })
    if (path === '/api/catalog') return reply(catalog)
    if (path === '/api/settings') {
      if (method === 'PUT') { const payload = route.request().postDataJSON(); state.writes.push(payload); Object.assign(settings, payload) }
      return reply({ settings, defaults: settings })
    }
    if (path === '/api/workflows') return reply({ workflows: [workflow] })
    if (path === '/api/workflows/digest') return reply(workflow)
    if (path === '/api/chats') return reply({ chats: [data.chat] })
    if (path === '/api/chats/alpha/stream') return route.fulfill({ contentType: 'text/event-stream', body: `retry: 1000\ndata: ${JSON.stringify({ type: 'snapshot', ...data })}\n\n` })
    if (path === '/api/chats/alpha/messages' && method === 'POST') {
      const payload = route.request().postDataJSON()
      state.sent.push(payload)
      const message = { id: payload.message_id, chat_id: 'alpha', role: 'user', content: payload.text, meta: {}, created_at: now }
      data.messages.push(message)
      return route.fulfill({ status: 202, json: { message, turn_id: payload.message_id } })
    }
    if (path === '/api/chats/alpha/read-state') return reply(data.chat)
    if (path === '/api/chats/alpha') {
      if (method === 'PATCH') { Object.assign(data.chat, route.request().postDataJSON()); return reply(data.chat) }
      return reply(data)
    }
    if (path === '/api/chats/recent') return reply({ chats: [data] })
    if (path.endsWith('/project-changes')) return reply({ projects: [] })
    if (path === '/api/model-integrations') return reply({ integrations: [], available: [], writable: true, storage_mode: 'managed' })
    if (path === '/api/mcp-servers') return reply({ servers: [], fields: [], writable: true, storage_mode: 'managed' })
    if (path === '/api/projects/github-app') return reply({ configured: false, registered: false })
    const key = path.split('/').pop()
    return reply({ [key]: [] })
  })
  return state
}
