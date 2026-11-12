import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { registerMcpTools } from '../../images/agent-sets/default/extensions/nautionette/mcp-tools.ts'

function mockPi(previous, { otherDeferred = false, customSearch = false } = {}) {
  const handlers = {}, branch = previous ? [{ type: 'custom', customType: 'nautionette.mcp-schema-mode', data: { mode: previous } }] : []
  const tools = new Map([
    ['read', { name: 'read' }], ['publish_output', { name: 'publish_output' }],
    ['tool_search', { name: 'tool_search', exposure: 'model-only', sourceInfo: { source: customSearch ? 'extension' : 'builtin' } }],
  ])
  if (otherDeferred) tools.set('package_tool', { name: 'package_tool', exposure: 'deferred' })
  let active = ['read', 'publish_output', 'tool_search', 'remote_lookup']
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

const definitions = ['remote_lookup', 'remote_secondary'].map(name => ({ name, parameters: {}, execute() {} }))

test('all selected schemas are direct and no prompt/discovery hook is installed', () => {
  const f = mockPi()
  registerMcpTools(f.pi, definitions)
  assert.ok(definitions.every(t => f.pi.getAllTools().find(d => d.name === t.name).exposure === 'direct'))
  assert.equal(f.handlers.before_agent_start, undefined)
  f.handlers.session_start({}, f.ctx)
  assert.equal(f.branch.length, 0) // Fresh ordinary sessions are not rewritten.
  const runner = readFileSync(new URL('../../images/pi-base/agent-run.mjs', import.meta.url), 'utf8')
  assert.doesNotMatch(runner, /builtin:tool-search|NAUTIONETTE_MCP_SCHEMA_MODE/)
})

test('old deferred sessions restore every currently allowed tool once and retire our builtin loader', () => {
  const f = mockPi('deferred')
  registerMcpTools(f.pi, definitions)
  f.handlers.session_start({}, f.ctx)
  assert.deepEqual(f.pi.getActiveTools(), ['read', 'publish_output', 'remote_lookup', 'remote_secondary'])
  assert.equal(f.branch.at(-1).data.mode, 'direct')
  f.handlers.session_start({}, f.ctx)
  assert.equal(f.branch.length, 2)
})

for (const options of [{ otherDeferred: true }, { customSearch: true }]) test('rollback does not disable another extension\'s discovery', () => {
  const f = mockPi('deferred', options)
  registerMcpTools(f.pi, definitions)
  f.handlers.session_start({}, f.ctx)
  assert.ok(f.pi.getActiveTools().includes('tool_search'))
  assert.ok(f.pi.getActiveTools().includes('remote_secondary'))
})

test('an empty current selection also retires our legacy loader without granting any MCP tool', () => {
  const f = mockPi('deferred')
  registerMcpTools(f.pi, [])
  f.handlers.session_start({}, f.ctx)
  assert.deepEqual(f.pi.getActiveTools(), ['read', 'publish_output'])
})

const extension = fileURLToPath(new URL('../../images/agent-sets/default/extensions/nautionette/index.ts', import.meta.url))

async function fixture(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'direct-mcp-'))
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
        { name: 'remote_secondary', description: 'Read another remote record', inputSchema: { type: 'object' } },
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
    } else if (!called && names.has('tool_search') && !body.messages.some(m => m.role === 'tool' && m.tool_call_id === `search-${runId}`)) {
      chunk({ tool_calls: [{ index: 0, id: `search-${runId}`, type: 'function', function: { name: 'tool_search', arguments: '{"query":"remote_lookup","limit":1}' } }] })
      chunk({}, 'tool_calls')
    } else { chunk({ content: 'READY' }); chunk({}, 'stop') }
    res.end('data: [DONE]\n\n')
  })
  let child
  try {
    server.listen(0, '127.0.0.1'); await once(server, 'listening')
    const origin = `http://127.0.0.1:${server.address().port}`
    // Seed a genuine native transcript with the experiment's loadout/marker,
    // without retaining the removed implementation in the shipped agent image.
    const legacy = join(dir, 'legacy-fixture.mjs')
    writeFileSync(legacy, `export default pi => {
      pi.registerProvider('nautionette', { baseUrl: '${origin}/v1', apiKey: 'fixture', api: 'openai-completions', models: [{ id: 'test-model', name: 'fixture', reasoning: false, input: ['text'], contextWindow: 128000, maxTokens: 1024, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }] });
      for (const name of ['remote_lookup','remote_secondary']) pi.registerTool({ name, description: name, exposure: 'deferred', parameters: { type: 'object', properties: { value: { type: 'string' } } }, async execute() { return { content: [{type:'text',text:'legacy fixture result'}], details: {} }; } });
      pi.on('session_start', () => { pi.setActiveTools([...pi.getActiveTools(), 'tool_search']); pi.appendEntry('nautionette.mcp-schema-mode', {mode:'deferred'}); });
    }`)
    async function run({ allowed = ['remote_lookup', 'remote_secondary'], seedLegacy = false, persistent = true, oldMode = 'deferred' } = {}) {
      runId++; called = false
      // Explicit native search here simulates its normal CLI registration; the
      // production supervisor must no longer force-load or activate this tool.
      child = spawn('pi', ['--mode', 'json', ...(persistent ? ['--session', join(dir, 'session.jsonl')] : ['--no-session']), '--no-extensions', '--no-context-files', '--no-skills', '--no-themes', '--no-prompt-templates', '-e', 'builtin:tool-search', '-e', seedLegacy ? legacy : extension, '--provider', 'nautionette', '--model', 'test-model', '--approve', '--', 'fixture request'], {
        cwd: dir, env: { ...process.env, HOME: dir, PI_CODING_AGENT_DIR: config, PI_OFFLINE: '1', PI_TELEMETRY: '0', AGENT_MODEL: 'test-model', AGENTGATEWAY_URL: origin, MCP_URL: origin + '/mcp', NAUTIONETTE_MODEL_API: 'openai-completions', NAUTIONETTE_MODEL_REASONING: '{"supported":false}', NAUTIONETTE_REASONING_EFFORT: '', NAUTIONETTE_MCP_SCHEMA_MODE: oldMode, NAUTIONETTE_MCP_TARGETS_JSON: '["remote"]', NAUTIONETTE_TOOLS_JSON: JSON.stringify(allowed), NAUTIONETTE_ARTCRAFT_JSON: '[]', NAUTIONETTE_OUTPUT_GRANT_FILE: join(dir, 'no-grant'), NODE_OPTIONS: '' }, stdio: ['ignore', 'pipe', 'pipe'],
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

function assertDirect(request) {
  const names = request.tools.map(t => t.function.name)
  assert.ok(names.includes('remote_lookup'))
  assert.ok(names.includes('remote_secondary'))
  assert.ok(!names.includes('tool_search'))
  assert.ok(!names.includes('remote_denied'))
}

test('real Pi calls selected MCP directly from its first request, including one-shot workflow calls', { timeout: 15000 }, () => fixture(async f => {
  const requests = await f.run({ persistent: false })
  assertDirect(requests[0])
  assert.equal(requests.length, 2)
  assert.equal(f.calls.length, 1)
  assert.equal(f.records.filter(r => r.name === 'backend_mcp_oauth_gate').length, 1)
  assert.ok(f.headers.filter(r => r.name === 'remote_lookup').every(r => r.value === 'remote'))
  assert.ok(f.headers.filter(r => r.name === 'backend_mcp_oauth_gate').every(r => r.value === ''))
}))

test('stale schema-mode environment settings cannot reactivate deferred schemas', { timeout: 15000 }, () => fixture(async f => {
  const requests = await f.run({ oldMode: 'invalid-old-value' })
  assertDirect(requests[0])
  assert.equal(requests.length, 2)
}))

test('native deferred history migrates to all current direct schemas without another search, then honors revocation', { timeout: 30000 }, () => fixture(async f => {
  const legacy = await f.run({ seedLegacy: true })
  assert.ok(legacy[0].tools.some(t => t.function.name === 'tool_search'))
  assert.ok(!legacy[0].tools.some(t => t.function.name === 'remote_secondary'))
  const restored = await f.run()
  assertDirect(restored[0])
  assert.equal(restored.length, 2)
  assert.equal(f.calls.length, 1)
  const resumed = await f.run()
  assertDirect(resumed[0])
  assert.equal(resumed.length, 2)
  assert.equal(f.calls.length, 2)
  const revoked = await f.run({ allowed: [] })
  assert.ok(!revoked[0].tools.some(t => t.function.name === 'remote_lookup'))
  assert.ok(!revoked[0].tools.some(t => t.function.name === 'remote_secondary'))
  assert.ok(!revoked[0].tools.some(t => t.function.name === 'tool_search'))
  assert.equal(f.calls.length, 2)
}))
