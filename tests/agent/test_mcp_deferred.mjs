import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { registerMcpTools } from '../../images/agent-sets/default/extensions/nautionette/deferred-tools.ts'

function mockPi(search = true, previous = undefined) {
  const handlers = {}, branch = previous ? [{ type: 'custom', customType: 'nautionette.mcp-schema-mode', data: { mode: previous } }] : []
  const tools = new Map([['read', { name: 'read' }], ['publish_output', { name: 'publish_output' }]])
  if (search) tools.set('tool_search', { name: 'tool_search', reachesTools: () => 'all' })
  let active = ['read', 'publish_output', 'remote_lookup']
  const pi = {
    registerTool(tool) { tools.set(tool.name, tool) },
    on(name, fn) { handlers[name] = fn },
    getAllTools() { return [...tools.values()] },
    getActiveTools() { return active },
    setActiveTools(names) { active = names.filter(name => tools.has(name)) },
    appendEntry(customType, data) { branch.push({ type: 'custom', customType, data }) },
  }
  return { pi, handlers, branch, ctx: { sessionManager: { getBranch: () => branch } } }
}

test('legacy eager transcripts migrate once; native loaded schemas survive resume and local tools stay active', () => {
  const f = mockPi()
  registerMcpTools(f.pi, [{ name: 'remote_lookup', parameters: {}, execute() {} }])
  f.handlers.session_start({}, f.ctx)
  assert.deepEqual(f.pi.getActiveTools(), ['read', 'publish_output', 'tool_search'])
  assert.equal(f.pi.getAllTools().find(t => t.name === 'remote_lookup').exposure, 'deferred')
  f.pi.setActiveTools([...f.pi.getActiveTools(), 'remote_lookup'])
  f.handlers.session_start({}, f.ctx)
  assert.ok(f.pi.getActiveTools().includes('remote_lookup'))
  assert.equal(f.branch.length, 1)
})

test('explicit tool-name preloading is bounded by the current allowlist and identifier boundaries', () => {
  const f = mockPi()
  registerMcpTools(f.pi, [{ name: 'remote_lookup', parameters: {}, execute() {} }])
  f.handlers.session_start({}, f.ctx)
  const event = prompt => ({ prompt, systemPromptOptions: { sections: {} } })
  f.handlers.before_agent_start(event('xremote_lookup and forbidden_delete'))
  assert.ok(!f.pi.getActiveTools().includes('remote_lookup'))
  const explicit = event('Please use remote_lookup.')
  f.handlers.before_agent_start(explicit)
  assert.ok(f.pi.getActiveTools().includes('remote_lookup'))
  assert.match(explicit.systemPromptOptions.sections.nautionette_mcp, /tool_search/)
})

test('custom hosts without native search retain direct capabilities rather than inaccessible tools', () => {
  const f = mockPi(false)
  registerMcpTools(f.pi, [{ name: 'remote_lookup', parameters: {}, execute() {} }])
  f.handlers.session_start({}, f.ctx)
  assert.equal(f.pi.getAllTools().find(t => t.name === 'remote_lookup').exposure, 'direct')
  assert.equal(f.branch[0].data.mode, 'direct')
  assert.throws(() => registerMcpTools(f.pi, [], 'invalid'), /Invalid MCP schema mode/)
})

const extension = fileURLToPath(new URL('../../images/agent-sets/default/extensions/nautionette/index.ts', import.meta.url))

async function fixture(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'deferred-mcp-'))
  const config = join(dir, 'config'); mkdirSync(config)
  writeFileSync(join(config, 'settings.json'), JSON.stringify({ retry: { enabled: false } }))
  const records = [], modelRequests = [], calls = [], headers = []
  let runId = 0, called = false, sessions = 0
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : {}
    if (req.url === '/mcp') {
      headers.push({ value: req.headers['x-nautionette-mcp-targets'], name: body.params?.name })
      records.push({ runId, method: body.method || req.method, name: body.params?.name })
      if (req.method === 'DELETE') { res.writeHead(204); return res.end() }
      if (body.method === 'notifications/initialized') { res.writeHead(202); return res.end() }
      let result
      if (body.method === 'initialize') {
        result = { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'fixture', version: '1' } }
        res.setHeader('mcp-session-id', `fixture-${++sessions}`)
      } else if (body.method === 'tools/list') result = { tools: [
        { name: 'remote_lookup', description: 'Look up a remote record', inputSchema: { type: 'object', properties: { value: { type: 'string' } } }, annotations: { readOnlyHint: true } },
        { name: 'remote_denied', description: 'This is not allowed', inputSchema: { type: 'object' } },
        { name: 'backend_mcp_oauth_gate', inputSchema: { type: 'object' } },
      ] }
      else if (body.params?.name === 'backend_mcp_oauth_tools') result = { content: [{ type: 'text', text: JSON.stringify({ tools: [], oauth_tools: ['remote_lookup'] }) }] }
      else if (body.params?.name === 'backend_mcp_oauth_gate') result = { content: [{ type: 'text', text: '{"status":"ready"}' }] }
      else {
        calls.push({ runId, name: body.params?.name })
        result = { content: [{ type: 'text', text: 'lookup succeeded' }] }
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result }))
    }
    modelRequests.push({ runId, body })
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    const chunk = (delta, finish_reason = null) => res.write(`data: ${JSON.stringify({ id: `m-${modelRequests.length}`, object: 'chat.completion.chunk', model: 'test-model', choices: [{ index: 0, delta, finish_reason }] })}\n\n`)
    chunk({ role: 'assistant', content: '' })
    const names = new Set((body.tools || []).map(t => t.function.name))
    if (!called && names.has('remote_lookup')) {
      called = true
      chunk({ tool_calls: [{ index: 0, id: `lookup-${runId}`, type: 'function', function: { name: 'remote_lookup', arguments: '{"value":"once"}' } }] })
      chunk({}, 'tool_calls')
    } else if (!called && names.has('tool_search') && !names.has('remote_lookup') && !body.messages.some(m => m.role === 'tool' && m.tool_call_id === `search-${runId}`)) {
      chunk({ tool_calls: [{ index: 0, id: `search-${runId}`, type: 'function', function: { name: 'tool_search', arguments: '{"query":"remote_lookup","limit":1}' } }] })
      chunk({}, 'tool_calls')
    } else { chunk({ content: 'READY' }); chunk({}, 'stop') }
    res.end('data: [DONE]\n\n')
  })
  let child
  try {
    server.listen(0, '127.0.0.1'); await once(server, 'listening')
    const origin = `http://127.0.0.1:${server.address().port}`
    async function run({ allowed = ['remote_lookup'], mode = 'deferred', prompt = 'fixture request' } = {}) {
      runId++; called = false
      child = spawn('pi', ['--mode', 'json', '--session', join(dir, 'session.jsonl'), '--no-extensions', '--no-context-files', '--no-skills', '--no-themes', '--no-prompt-templates', '-e', 'builtin:tool-search', '-e', extension, '--provider', 'nautionette', '--model', 'test-model', '--approve', '--', prompt], {
        cwd: dir, env: { ...process.env, HOME: dir, PI_CODING_AGENT_DIR: config, PI_OFFLINE: '1', PI_TELEMETRY: '0', AGENT_MODEL: 'test-model', AGENTGATEWAY_URL: origin, MCP_URL: origin + '/mcp', NAUTIONETTE_MODEL_API: 'openai-completions', NAUTIONETTE_MODEL_REASONING: '{"supported":false}', NAUTIONETTE_REASONING_EFFORT: '', NAUTIONETTE_MCP_SCHEMA_MODE: mode, NAUTIONETTE_MCP_TARGETS_JSON: '["remote"]', NAUTIONETTE_TOOLS_JSON: JSON.stringify(allowed), NAUTIONETTE_ARTCRAFT_JSON: '[]', NAUTIONETTE_OUTPUT_GRANT_FILE: join(dir, 'no-grant'), NODE_OPTIONS: '' }, stdio: ['ignore', 'pipe', 'pipe'],
      })
      let stdout = '', stderr = ''
      child.stdout.on('data', chunk => { stdout += chunk }); child.stderr.on('data', chunk => { stderr += chunk })
      const [code] = await once(child, 'close')
      assert.equal(code, 0, stderr)
      const events = stdout.trim().split('\n').map(JSON.parse)
      assert.ok(events.some(e => e.type === 'agent_settled'), stderr)
      assert.ok(!events.some(e => e.type === 'extension_error'), stderr)
      return modelRequests.filter(r => r.runId === runId).map(r => r.body)
    }
    await fn({ run, modelRequests, calls, records, headers })
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) { child.kill(); await once(child, 'close') }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
    rmSync(dir, { recursive: true, force: true })
  }
}

test('real Pi searches then calls allowed MCP once, preserves loaded schemas on resume, and revokes old access', { timeout: 30000 }, () => fixture(async f => {
  const first = await f.run()
  assert.ok(first[0].tools.some(t => t.function.name === 'tool_search'))
  assert.ok(!first[0].tools.some(t => t.function.name === 'remote_lookup'))
  assert.ok(first[1].tools.some(t => t.function.name === 'remote_lookup'))
  assert.equal(f.calls.length, 1)
  assert.equal(f.records.filter(r => r.name === 'backend_mcp_oauth_gate').length, 1)
  const resumed = await f.run()
  assert.ok(resumed[0].tools.some(t => t.function.name === 'remote_lookup'))
  assert.equal(resumed.length, 2)
  assert.equal(f.calls.length, 2)
  const revoked = await f.run({ allowed: [] })
  assert.ok(!revoked[0].tools.some(t => t.function.name === 'remote_lookup'))
  assert.equal(f.calls.length, 2)
  assert.ok(f.modelRequests.every(r => !r.body.tools.some(t => t.function.name === 'remote_denied')))
  assert.ok(f.headers.every(row => row.value === 'remote' || row.value === ''))
  assert.ok(f.headers.filter(row => row.name === 'remote_lookup').every(row => row.value === 'remote'))
  assert.ok(f.headers.filter(row => row.name === 'backend_mcp_oauth_gate').every(row => row.value === ''))
  assert.ok(f.records.some(r => r.method === 'DELETE'))
}))

test('real Pi exact requested tool name avoids a loader round trip', { timeout: 15000 }, () => fixture(async f => {
  const requests = await f.run({ prompt: 'fixture request: use remote_lookup exactly once' })
  assert.ok(requests[0].tools.some(t => t.function.name === 'remote_lookup'))
  assert.equal(requests.length, 2)
  assert.equal(f.calls.length, 1)
}))

test('operator direct-schema compatibility mode retains eager declarations', { timeout: 15000 }, () => fixture(async f => {
  const requests = await f.run({ mode: 'direct' })
  assert.ok(requests[0].tools.some(t => t.function.name === 'remote_lookup'))
  assert.equal(requests.length, 2)
  assert.equal(f.calls.length, 1)
}))
