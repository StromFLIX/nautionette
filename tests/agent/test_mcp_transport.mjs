import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createMcpTransport } from '../../images/agent-sets/default/extensions/nautionette/mcp-transport.ts'
import { waitForMcpAuth } from '../../images/agent-sets/default/extensions/nautionette/mcp-auth.ts'

function fixture (tool) {
  const requests = []
  let sessions = 0
  const transport = createMcpTransport('http://gateway/mcp', async (_url, options) => {
    const request = JSON.parse(options.body)
    requests.push({ ...request, session: options.headers['mcp-session-id'] })
    if (request.method === 'initialize') {
      sessions++
      assert.equal(options.headers['mcp-session-id'], undefined)
      return Response.json({ result: { protocolVersion: '2025-06-18' } }, { headers: { 'mcp-session-id': `session-${sessions}` } })
    }
    if (request.method === 'notifications/initialized') return new Response(null, { status: 202 })
    if (request.method === 'tools/list') return Response.json({ result: { tools: [] } })
    return tool(request, options, sessions)
  })
  transport.setReadOnlyTools(['read'])
  return { transport, requests, sessions: () => sessions }
}

for (const status of [400, 404]) test(`stale ${status} session reinitializes once and retries an explicitly read-only tool`, async () => {
  const { transport, requests, sessions } = fixture((_request, _options, session) => session === 1
    ? new Response('private upstream details', { status })
    : Response.json({ result: { content: [{ type: 'text', text: 'healthy' }] } }))
  const result = await transport.rpc('tools/call', { name: 'read' })
  assert.equal(result.content[0].text, 'healthy')
  assert.equal(sessions(), 2)
  assert.deepEqual(requests.filter(r => r.method === 'tools/call').map(r => r.session), ['session-1', 'session-2'])
})

test('a rejected mutation restores the session but is never replayed', async () => {
  let calls = 0
  const { transport, sessions } = fixture(() => { calls++; return new Response('secret', { status: 400 }) })
  await assert.rejects(transport.rpc('tools/call', { name: 'deploy' }), /NOT replayed; inspect/)
  assert.equal(calls, 1)
  assert.equal(sessions(), 2)
  // Subsequent independent calls use the restored session.
  await transport.rpc('tools/list')
  assert.equal(sessions(), 2)
})

for (const status of [401, 403, 429, 500, 503]) test(`HTTP ${status} is not mistaken for an expired session`, async () => {
  const { transport, sessions } = fixture(() => new Response('upstream secret', { status }))
  await assert.rejects(transport.rpc('tools/call', { name: 'read' }), error => {
    assert.match(error.message, new RegExp(`HTTP ${status}`))
    assert.ok(!error.message.includes('secret'))
    return true
  })
  assert.equal(sessions(), 1)
})

test('network failures and JSON-RPC errors are never automatically repeated', async () => {
  for (const fail of [() => { throw new Error('network failed') }, () => Response.json({ error: { code: -32602, message: 'invalid parameters' } })]) {
    let calls = 0
    const { transport, sessions } = fixture(() => { calls++; return fail() })
    await assert.rejects(transport.rpc('tools/call', { name: 'read' }))
    assert.equal(calls, 1)
    assert.equal(sessions(), 1)
  }
})

test('simultaneous stale read calls share one fresh handshake', async () => {
  const { transport, sessions } = fixture((_request, options) => options.headers['mcp-session-id'] === 'session-1'
    ? new Response('', { status: 400 }) : Response.json({ result: { ok: true } }))
  await transport.connect()
  const results = await Promise.all([transport.rpc('tools/call', { name: 'read' }), transport.rpc('tools/call', { name: 'read' })])
  assert.deepEqual(results, [{ ok: true }, { ok: true }])
  assert.equal(sessions(), 2)
})

test('a second stale response is surfaced rather than looping forever', async () => {
  let calls = 0
  const { transport, sessions } = fixture(() => { calls++; return new Response('', { status: 400 }) })
  await assert.rejects(transport.rpc('tools/call', { name: 'read' }), /HTTP 400/)
  assert.equal(sessions(), 2)
  assert.equal(calls, 2)
})

test('cancellation prevents both initial handshake and tool execution', async () => {
  const { transport, requests } = fixture(() => assert.fail('must not execute'))
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(transport.rpc('tools/call', { name: 'read' }, controller.signal), /abort/i)
  assert.equal(requests.length, 0)
})

test('authenticated dispatch uses a fresh session without disturbing the pre-login session', async () => {
  const calls = [], deleted = []
  let sessions = 0
  const transport = createMcpTransport('http://gateway/mcp', async (_url, options) => {
    if (options.method === 'DELETE') {
      deleted.push(options.headers['mcp-session-id'])
      return new Response(null, { status: 204 })
    }
    const request = JSON.parse(options.body)
    if (request.method === 'initialize') {
      sessions++
      return Response.json({ result: {} }, { headers: { 'mcp-session-id': `session-${sessions}` } })
    }
    if (request.method === 'notifications/initialized') return new Response(null, { status: 202 })
    calls.push([request.method, options.headers['mcp-session-id']])
    return Response.json({ result: {} })
  })
  await transport.connect() // Established before the OAuth token reached federation.
  await transport.rpcFresh('tools/call', { name: 'write_once' })
  await transport.rpc('tools/list')
  assert.deepEqual(calls, [['tools/call', 'session-2'], ['tools/list', 'session-1']])
  assert.deepEqual(deleted, ['session-2'])
})

for (const status of [400, 500]) test(`fresh authenticated dispatch HTTP ${status} is never replayed`, async () => {
  let calls = 0, deleted = 0
  const transport = createMcpTransport('http://gateway/mcp', async (_url, options) => {
    if (options.method === 'DELETE') { deleted++; throw new Error('cleanup failed') }
    const request = JSON.parse(options.body)
    if (request.method === 'initialize') return Response.json({ result: {} }, { headers: { 'mcp-session-id': 'fresh' } })
    if (request.method === 'notifications/initialized') return new Response(null, { status: 202 })
    calls++
    return new Response('', { status })
  })
  await assert.rejects(transport.rpcFresh('tools/call', { name: 'write_once' }), new RegExp(`HTTP ${status}`))
  assert.equal(calls, 1)
  assert.equal(deleted, 1)
})

for (const selection of [null, [], ['github', 'backend']]) test(`scope ${JSON.stringify(selection)} survives every request, recovery and fresh OAuth dispatch`, async () => {
  const requests = []
  let sessions = 0, rejected = false
  const mutable = selection === null ? null : [...selection]
  const transport = createMcpTransport('http://gateway/mcp', async (_url, options) => {
    const request = options.method === 'DELETE' ? { method: 'DELETE' } : JSON.parse(options.body)
    requests.push({ ...request, headers: options.headers })
    if (request.method === 'initialize') {
      sessions++
      return Response.json({ result: {} }, { headers: { 'mcp-session-id': `scope-${sessions}` } })
    }
    if (request.method === 'notifications/initialized') return new Response(null, { status: 202 })
    if (request.method === 'tools/list' && !rejected) {
      rejected = true
      return new Response('', { status: 404 })
    }
    if (request.method === 'DELETE') return new Response(null, { status: 204 })
    return Response.json({ result: { tools: [] } })
  }, mutable)
  mutable?.push('must-not-retarget-session')
  await transport.rpc('tools/list')
  await transport.rpcFresh('tools/call', { name: 'backend_read_chat_output', arguments: {} })
  await transport.close()
  assert.equal(sessions, 3)
  for (const request of requests) {
    const expected = selection === null ? undefined : [...selection].sort().join(',')
    assert.equal(request.headers['x-nautionette-mcp-targets'], expected, request.method)
  }
  assert.equal(requests.filter(r => r.method === 'DELETE').length, 2)
})

for (const names of [['bad,name'], ['bad\r\nheader'], [''], ['x'.repeat(129)], Array.from({ length: 100 }, (_, i) => `${i}`.repeat(100))]) {
  test('invalid target header cannot fall back to unrestricted discovery', () => {
    assert.throws(() => createMcpTransport('http://gateway/mcp', fetch, names), /Invalid MCP target selection/)
  })
}

const gateResult = status => ({ content: [{ type: 'text', text: JSON.stringify({ status }) }] })
test('OAuth gate holds dispatch until sign-in, then permits exactly one tool call', async () => {
  const requests = [], statuses = ['waiting', 'ready']
  const rpc = async (_method, params) => {
    requests.push(params)
    return params.name === 'backend_mcp_oauth_gate' ? gateResult(statuses.shift()) : { content: [] }
  }
  await waitForMcpAuth(rpc, { grant: 'private' }, 'example_write', 'call_provider|' + 'signed-state'.repeat(100))
  await rpc('tools/call', { name: 'example_write', arguments: { text: 'once' } })
  assert.deepEqual(requests.map(item => item.name), ['backend_mcp_oauth_gate', 'backend_mcp_oauth_gate', 'example_write'])
  assert.equal(requests[0].arguments.body.tool_call_id, requests[1].arguments.body.tool_call_id)
  assert.match(requests[0].arguments.body.tool_call_id, /^[a-f0-9]{64}$/)
})

for (const status of ['failed', 'timed_out', 'unknown']) test(`OAuth ${status} never dispatches the held call`, async () => {
  const requests = []
  await assert.rejects(async () => {
    const rpc = async (_method, params) => { requests.push(params.name); return gateResult(status) }
    await waitForMcpAuth(rpc, { grant: 'private' }, 'example_write', 'call-1')
    await rpc('tools/call', { name: 'example_write' })
  }, /tool was not called/)
  assert.deepEqual(requests, ['backend_mcp_oauth_gate'])
})

test('signed provider call IDs stay bounded, stable and distinct across gate polls and cancellation', async () => {
  // Responses providers can append a signature much longer than the gate API's
  // 256-character limit. Truncating it would conflate calls sharing a prefix.
  const prefix = 'call_provider|' + 'signed-provider-state/'.repeat(100)
  const ids = [], requests = []
  const rpc = async (_method, params) => {
    const body = params.arguments.body
    assert.match(body.tool_call_id, /^[a-f0-9]{64}$/)
    assert.ok(!body.tool_call_id.includes('signed-provider-state'))
    ids.push(body.tool_call_id)
    requests.push(body)
    return gateResult('ready')
  }
  for (const id of [prefix + 'a', prefix + 'a', prefix + 'b', 'ordinary-call'])
    await waitForMcpAuth(rpc, { grant: 'private' }, 'example_write', id)
  assert.equal(ids[0], ids[1])
  assert.equal(new Set(ids).size, 3)

  const controller = new AbortController()
  await assert.rejects(waitForMcpAuth(async (method, params) => {
    await rpc(method, params)
    controller.abort()
    return gateResult('waiting')
  }, { grant: 'private' }, 'example_write', prefix + 'a', controller.signal), /abort/i)
  assert.equal(requests.at(-2).tool_call_id, ids[0])
  assert.equal(requests.at(-1).tool_call_id, ids[0])
  assert.equal(requests.at(-1).cancel, true)
})

test('OAuth wait is abortable and private error bodies never reach model context', async () => {
  const controller = new AbortController()
  const rpc = async () => { controller.abort(); return gateResult('waiting') }
  await assert.rejects(waitForMcpAuth(rpc, { grant: 'private' }, 'example_write', 'call-1', controller.signal), /abort/i)
  await assert.rejects(waitForMcpAuth(async () => ({ isError: true, content: [{ type: 'text', text: 'private' }] }),
    { grant: 'private' }, 'example_write', 'call-1'), error => !error.message.includes('private'))
})
