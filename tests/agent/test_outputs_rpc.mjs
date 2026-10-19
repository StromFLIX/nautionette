// Real Pi -> production extension -> offline gateway fixture. No provider key.
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { digest, outputTransport } from './output_fixture.mjs'

const piAvailable = spawnSync('pi', ['--version']).status === 0
const extension = fileURLToPath(new URL('../../images/agent-sets/default/extensions/nautionette/index.ts', import.meta.url))
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==', 'base64')
const pdf = Buffer.from('%PDF-output-test')
const grant = 'private-test-capability-with-32-characters'
const output = { id: 'a'.repeat(32), revision: 1, title: 'Report', files: [
  { id: '1'.repeat(32), name: 'report.pdf', mime_type: 'application/pdf', size: pdf.length, role: 'file', previewable: false },
  { id: '2'.repeat(32), name: 'preview.png', mime_type: 'image/png', size: png.length, role: 'preview', previewable: true }
] }

async function exercisePi (selection) {
  const cwd = await mkdtemp(join(tmpdir(), 'pi-output-rpc-'))
  const config = join(cwd, 'private-config.json')
  await writeFile(config, JSON.stringify({ grant }), { mode: 0o600 })
  await writeFile(join(cwd, 'report.pdf'), pdf)
  await writeFile(join(cwd, 'preview.png'), png)
  const requests = [], events = []
  const longCallId = 'publish-call'.repeat(100)
  const transfer = await outputTransport({ onPublish: args => {
    assert.equal(args.request_id, `sha256:${digest(longCallId)}`)
    assert.deepEqual(args.files.map(file => Buffer.from(file.data, 'base64')), [pdf, png])
  } })
  const publications = transfer.records
  const server = createServer(async (req, res) => {
    try {
      let raw = ''
      for await (const chunk of req) raw += chunk
      const body = raw ? JSON.parse(raw) : {}
      if (req.url === '/mcp') {
        let result = {}
        if (body.method === 'tools/list') result = { tools: [
          { name: 'backend_prepare_chat_output', description: 'Private prepare', inputSchema: { type: 'object' } },
          { name: 'backend_publish_chat_output', description: 'Private publisher', inputSchema: { type: 'object' } },
          { name: 'backend_read_chat_output', description: 'Private reader', inputSchema: { type: 'object' } },
          { name: 'publish_output', description: 'Remote name must not shadow the local publisher', inputSchema: { type: 'object' } }
        ] }
        if (body.method === 'tools/call') {
          assert.equal(body.params.arguments.body.grant, grant)
          result = await transfer.rpc(body.method, body.params, AbortSignal.timeout(20000))
        }
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result }))
        return
      }
      requests.push(body)
      const names = body.tools.map(tool => tool.function.name)
      assert.ok(names.includes('publish_output') && names.includes('read_output') && names.includes('read'))
      assert.ok(!names.some(name => name.startsWith('backend_')))
      assert.ok(!JSON.stringify(body).includes(grant))
      res.setHeader('content-type', 'text/event-stream')
      const chunk = (delta, finish_reason = null) => res.write(`data: ${JSON.stringify({
        id: 'chat-output', object: 'chat.completion.chunk', model: 'test/model',
        choices: [{ index: 0, delta, finish_reason }]
      })}\n\n`)
      const call = (id, name, args) => {
        chunk({ role: 'assistant', tool_calls: [{ index: 0, id, type: 'function', function: { name, arguments: JSON.stringify(args) } }] })
        chunk({}, 'tool_calls')
      }
      assert.ok(!JSON.stringify(body).includes('private-upload-capability'))
      assert.ok(!JSON.stringify(body).includes('private-download-capability'))
      if (requests.length === 1) call(longCallId, 'publish_output', {
        title: 'Report', files: [{ path: 'report.pdf' }, { path: 'preview.png', role: 'preview' }]
      })
      else if (requests.length === 2) call('restore-call', 'read_output', { output_id: output.id, file_id: output.files[1].id })
      else if (requests.length === 3) {
        const restored = JSON.parse(body.messages.findLast(message => message.role === 'tool').content)
        assert.equal(restored.file.data, undefined)
        assert.deepEqual(await readFile(restored.file.path), png)
        call('inspect-call', 'read', { path: restored.file.path })
      } else {
        assert.ok(JSON.stringify(body.messages).includes('data:image/png;base64,'))
        chunk({ role: 'assistant', content: 'Published and inspected.' })
        chunk({}, 'stop')
      }
      res.end('data: [DONE]\n\n')
    } catch (error) {
      res.statusCode = 500
      res.end(error.message)
    }
  })
  let child, stderr = ''
  try {
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const gateway = `http://127.0.0.1:${server.address().port}`
    child = spawn('pi', ['--mode', 'rpc', '--no-session', '--no-extensions', '-e', extension,
      '--no-context-files', '--no-skills', '--no-prompt-templates',
      '--provider', 'nautionette', '--model', 'test/model'], {
      cwd, env: { ...process.env, PI_CODING_AGENT_DIR: cwd, PI_OFFLINE: '1', PI_TELEMETRY: '0',
        AGENTGATEWAY_URL: gateway, MCP_URL: `${gateway}/mcp`, AGENT_MODEL: 'test/model',
        NAUTIONETTE_MODEL_API: 'openai-completions', NAUTIONETTE_TOOLS_JSON: JSON.stringify(selection),
        NAUTIONETTE_OUTPUT_GRANT_FILE: config }, stdio: ['pipe', 'pipe', 'pipe']
    })
    child.stderr.on('data', chunk => { stderr += chunk })
    let buffer = ''
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Pi output test timed out: ${stderr}`)), 20000)
      child.on('error', reject)
      child.on('close', code => { clearTimeout(timer); reject(new Error(`Pi exited ${code}: ${stderr}`)) })
      child.stdout.setEncoding('utf8')
      child.stdout.on('data', chunk => {
        buffer += chunk
        let index
        while ((index = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
          if (!line.trim()) continue
          const event = JSON.parse(line)
          events.push(event)
          if (event.type === 'agent_settled') { clearTimeout(timer); resolve() }
        }
      })
      child.stdin.write(JSON.stringify({ type: 'prompt', message: 'Publish this report with its preview, then inspect the saved preview.' }) + '\n')
    })
    assert.equal(publications.length, 1, JSON.stringify(events.filter(event => event.type === 'tool_execution_end' || (event.type === 'message_end' && event.message.stopReason === 'error'))))
    assert.equal(requests.length, 4)
    assert.ok(!JSON.stringify(events).includes(grant))
    const results = events.filter(event => event.type === 'tool_execution_end')
    assert.deepEqual(results.map(result => result.toolName), ['publish_output', 'read_output', 'read'])
    assert.ok(results.every(result => !result.isError))
    await assert.rejects(stat(config), { code: 'ENOENT' })
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) { const closed = once(child, 'close'); child.kill(); await closed }
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
    await transfer.close()
    await rm(cwd, { force: true, recursive: true })
  }
}

for (const selection of [null, []]) {
  test(`real Pi publishes, restores and visually reads a preview (MCP selection: ${JSON.stringify(selection)})`,
    { skip: !piAvailable, timeout: 30000 }, () => exercisePi(selection))
}
