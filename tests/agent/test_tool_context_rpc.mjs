// Real Pi: a newest tool batch larger than the context window must be bounded
// before threshold compaction, while its raw evidence survives fresh processes.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { SessionManager } from '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js'
import { prepareSession } from '../../images/pi-base/chat-session.mjs'
import { MAX_TOOL_CONTEXT_CHARS } from '../../images/pi-base/tool-context.mjs'

const extension = fileURLToPath(new URL('../../images/pi-base/chat-session-extension.mjs', import.meta.url))

test('oversized parallel tools retain raw evidence, stay bounded after compaction and never replay on resume', { timeout: 30000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'tool-context-rpc-'))
  const config = join(dir, 'agent'), state = join(dir, 'state'), operation = join(dir, 'operation.txt')
  mkdirSync(config)
  const evidence = 'HEAD' + 'x'.repeat(2_000_000) + 'TAIL'
  let phase = 'first', child, calls = 0
  const requests = [], events = []
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk
    const request = JSON.parse(raw); requests.push({ phase, request }); calls++
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    const chunk = (delta, finish_reason = null) => res.write(`data: ${JSON.stringify({
      id: `response-${calls}`, object: 'chat.completion.chunk', model: 'test-model',
      choices: [{ index: 0, delta, finish_reason }]
    })}\n\n`)
    chunk({ role: 'assistant', content: '' })
    if (calls === 1) {
      chunk({ tool_calls: [0, 1].map(index => ({ index, id: `once-${index}`, type: 'function',
        function: { name: 'large_result', arguments: JSON.stringify({ fail: index === 1 }) } })) })
      chunk({}, 'tool_calls')
    } else {
      chunk({ content: phase === 'compact' ? 'Summary: both original tools completed; read their evidence with chat_history.' : `Completed ${phase}.` })
      chunk({}, 'stop')
    }
    res.end('data: [DONE]\n\n')
  })
  try {
    server.listen(0, '127.0.0.1'); await once(server, 'listening')
    writeFileSync(join(config, 'settings.json'), JSON.stringify({ compaction: { reserveTokens: 1000, keepRecentTokens: 1 } }))
    const provider = join(dir, 'provider.mjs')
    writeFileSync(provider, `import { appendFileSync } from 'node:fs';
      export default pi => {
        pi.registerProvider('nautionette', { baseUrl: 'http://127.0.0.1:${server.address().port}/v1',
          apiKey: 'offline', api: 'openai-completions', models: [{ id: 'test-model', name: 'Test',
          reasoning: false, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 100000, maxTokens: 2048 }] });
        pi.registerTool({ name: 'large_result', label: 'Large result', description: 'Offline oversized fixture',
          parameters: { type: 'object', properties: { fail: { type: 'boolean' } } },
          async execute(_id, args) {
            appendFileSync(${JSON.stringify(operation)}, 'once\\n');
            if (args.fail) throw new Error('HEAD' + 'x'.repeat(2000000) + 'TAIL');
            return { content: [{ type: 'text', text: 'HEAD' + 'x'.repeat(2000000) + 'TAIL' }], details: {} };
          } });
      }`)
    async function run(id, prompt) {
      const session = prepareSession({ turn_id: id }, dir, state)
      if (!prompt.startsWith('/compact')) session.beginInput(id)
      const output = []; events.push(output)
      child = spawn('pi', ['--mode', 'rpc', '--session', session.file, '--provider', 'nautionette', '--model', 'test-model',
        '--no-extensions', '-e', provider, '-e', extension, '--no-context-files', '--no-skills', '--no-prompt-templates'], {
        cwd: dir, env: { ...process.env, HOME: dir, PI_CODING_AGENT_DIR: config,
          NAUTIONETTE_TURN_ID: id, PI_OFFLINE: '1', PI_TELEMETRY: '0' }, stdio: ['pipe', 'pipe', 'pipe']
      })
      let buffer = '', stderr = ''
      child.stderr.on('data', chunk => { stderr += chunk })
      child.stdout.on('data', chunk => {
        buffer += chunk
        let end
        while ((end = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, end); buffer = buffer.slice(end + 1)
          if (!line.trim()) continue
          const event = JSON.parse(line); output.push(event)
          if (event.type === 'agent_settled' || (event.type === 'response' && event.id === 'compact')) child.kill()
        }
      })
      child.stdin.write(JSON.stringify(prompt.startsWith('/compact') ? { id: 'compact', type: 'compact' } : { type: 'prompt', message: prompt }) + '\n')
      await once(child, 'close')
      assert.equal(output.some(e => e.type === 'extension_error'), false, stderr)
      assert.ok(output.some(e => e.type === (prompt.startsWith('/compact') ? 'response' : 'agent_settled')), stderr)
      return session
    }
    const session = await run('one', 'Run both fixtures once, then answer.')
    assert.equal(requests.length, 2, 'No automatic compaction of an oversized newest batch')
    const tools = requests[1].request.messages.filter(m => m.role === 'tool')
    assert.equal(tools.length, 2)
    for (const tool of tools) {
      assert.ok(tool.content.length <= MAX_TOOL_CONTEXT_CHARS)
      assert.match(tool.content, /chat_history/)
      assert.match(tool.content, /HEAD/)
      assert.match(tool.content, /TAIL/)
    }
    const native = SessionManager.open(session.file)
    const results = native.getEntries().filter(e => e.message?.role === 'toolResult')
    assert.equal(results.length, 2)
    assert.equal(results[0].message.content[0].text, evidence)
    assert.equal(results[1].message.isError, true)
    assert.equal(native.getEntries().filter(e => e.type === 'context_edit').length, 2)
    phase = 'compact'
    await run('two', '/compact')
    assert.ok(events[1].some(e => e.type === 'response' && e.id === 'compact' && e.success))
    phase = 'resumed'
    await run('three', 'Continue from the completed results without running them again.')
    for (const { request } of requests.slice(1)) assert.ok(JSON.stringify(request).length < 150000)
    assert.equal(readFileSync(operation, 'utf8'), 'once\nonce\n')
    const reopened = SessionManager.open(session.file)
    assert.equal(reopened.getEntry(results[0].id).message.content[0].text, evidence)
    assert.ok(events[2].some(e => e.type === 'message_end' && e.message.role === 'assistant' &&
      e.message.content.some(p => p.type === 'text' && p.text === 'Completed resumed.')))
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) { const closed = once(child, 'close'); child.kill(); await closed }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
    rmSync(dir, { recursive: true, force: true })
  }
})
