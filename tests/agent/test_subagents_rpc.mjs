// Opt-in integration with the real pi-subagents package. No downloads or paid models.
// PATH=<Pi 0.85.1 bin>:$PATH NAUTIONETTE_SUBAGENTS_DIR=<installed pi-subagents> node --test this-file
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const packageDir = process.env.NAUTIONETTE_SUBAGENTS_DIR
const provider = fileURLToPath(new URL('../../images/agent-sets/default/extensions/nautionette/index.ts', import.meta.url))
for (const persistent of [false, true]) test(`real async subagent completes through the gateway (persistent parent: ${persistent})`, { skip: !packageDir, timeout: 60000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'nautionette-subagents-')), agentDir = join(dir, 'agent')
  mkdirSync(agentDir)
  // Ambient provider discovery is required by the detached child, not just -e on the parent.
  writeFileSync(join(agentDir, 'settings.json'), JSON.stringify({ packages: [packageDir], extensions: [provider], retry: { enabled: false } }))
  let parentRequests = 0, childRequests = 0
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw || '{}')
    if (req.url === '/mcp') {
      res.setHeader('content-type', 'application/json')
      res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result: { tools: [] } })); return
    }
    const parent = JSON.stringify(body.messages).includes('PARENT_REQUEST')
    let tool, text = 'CHILD_COMPLETE'
    if (parent) {
      parentRequests++
      if (parentRequests === 1) tool = { name: 'subagent', arguments: JSON.stringify({ agent: 'delegate', task: 'CHILD_REQUEST', context: 'fresh', model: 'nautionette/test/model', async: true }) }
      else if (parentRequests === 2) tool = { name: 'bg_wait', arguments: JSON.stringify({ id: JSON.stringify(body.messages).match(/Async: delegate \[([a-f0-9-]+)\]/)?.[1], timeoutMs: 20000 }) }
      else text = 'PARENT_COMPLETE'
    } else childRequests++
    res.setHeader('content-type', 'text/event-stream')
    const delta = tool ? { role: 'assistant', tool_calls: [{ index: 0, id: `call_${parentRequests}`, type: 'function', function: tool }] } : { role: 'assistant', content: text }
    const chunk = (delta, finish_reason) => `data: ${JSON.stringify({ id: 'test', object: 'chat.completion.chunk', model: 'test/model', choices: [{ index: 0, delta, finish_reason }] })}\n\n`
    res.write(chunk(delta, null)); res.write(chunk({}, tool ? 'tool_calls' : 'stop')); res.end('data: [DONE]\n\n')
  })
  let child, timer
  const events = []
  try {
    server.listen(0, '127.0.0.1'); await once(server, 'listening')
    const url = `http://127.0.0.1:${server.address().port}`
    child = spawn('pi', ['--mode', 'rpc', ...(persistent ? ['--session-dir', join(dir, 'sessions')] : ['--no-session']), '--no-context-files', '--provider', 'nautionette', '--model', 'test/model'], {
      cwd: dir, env: { PATH: process.env.PATH, HOME: dir, TMPDIR: dir, PI_CODING_AGENT_DIR: agentDir, PI_OFFLINE: '1', PI_TELEMETRY: '0',
        AGENTGATEWAY_URL: url, MCP_URL: `${url}/mcp`, AGENT_MODEL: 'test/model', NAUTIONETTE_MODEL_API: 'openai-completions' }, stdio: ['pipe', 'pipe', 'pipe']
    })
    let buffer = '', stderr = ''
    child.stderr.setEncoding('utf8'); child.stdout.setEncoding('utf8')
    child.stderr.on('data', chunk => { stderr += chunk })
    const done = new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error(`Subagent timeout: ${stderr}\n${JSON.stringify(events.filter(event => ['tool_execution_end', 'extension_error'].includes(event.type)))}`)), 45000)
      child.on('error', reject)
      child.on('close', code => reject(new Error(`Pi exited before settling (${code}): ${stderr}`)))
      child.stdout.on('data', chunk => {
        buffer += chunk
        let index
        while ((index = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
          if (!line.trim()) continue
          let event; try { event = JSON.parse(line) } catch { continue }
          events.push(event)
          if (event.type === 'agent_settled') resolve()
        }
      })
    })
    child.stdin.write(JSON.stringify({ id: 'initial', type: 'prompt', message: 'PARENT_REQUEST' }) + '\n')
    await done
    const tools = events.filter(event => event.type === 'tool_execution_end')
    assert.ok(tools.some(event => event.toolName === 'subagent'), JSON.stringify(events))
    assert.ok(tools.every(event => !event.isError), JSON.stringify(tools))
    assert.ok(childRequests > 0, `Child never reached model gateway: ${JSON.stringify(tools)} ${stderr}`)
    const completion = tools.find(event => event.toolName === 'bg_wait')?.result.details.completions?.[0]
    assert.equal(completion?.success, true, JSON.stringify(tools))
    const output = completion.results[0].artifactPaths.outputPath
    assert.ok(output.startsWith(dir + '/'))
    assert.match(readFileSync(output, 'utf8'), /CHILD_COMPLETE/)
    assert.ok(!JSON.stringify(events).includes('does not provide @earendil-works/chord'))
  } finally {
    clearTimeout(timer)
    if (child && child.exitCode === null && child.signalCode === null) { const closed = once(child, 'close'); child.kill(); await closed }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
    rmSync(dir, { recursive: true, force: true })
  }
})
