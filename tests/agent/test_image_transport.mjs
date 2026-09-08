// Real Pi RPC -> production extension -> fake gateway. No model key or internet.
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const piAvailable = spawnSync('pi', ['--version']).status === 0
const extension = fileURLToPath(new URL('../../images/agent-sets/default/extensions/nautionette/index.ts', import.meta.url))
const images = [
  { type: 'image', mimeType: 'image/png', data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=' },
  { type: 'image', mimeType: 'image/gif', data: 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7' }
]

for (const [model, endpoint, pinnedApi] of [
  ['copilot/claude-sonnet-5', '/chat/completions'],
  ['copilot/gpt-6-astra', '/responses'],
  ['copilot/gpt-5', '/chat/completions', 'openai-completions'],
  ['copilot/claude-sonnet-5', '/responses', 'openai-responses']
]) {
  test(`real Pi preserves images on the ${pinnedApi ? 'pinned' : 'advertised'} ${model} endpoint`, { skip: !piAvailable, timeout: 30000 }, async () => {
    const requests = []
    let catalogRequests = 0
    const server = createServer(async (req, res) => {
      let raw = ''
      for await (const chunk of req) raw += chunk
      const body = raw ? JSON.parse(raw) : {}
      if (req.url.endsWith('/models')) {
        catalogRequests++
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify({ data: [{ id: model.slice(8), supported_endpoints: [endpoint] }] }))
        return
      }
      if (req.url === '/mcp') {
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result: { tools: [] } }))
        return
      }
      requests.push({ path: req.url, body })
      res.setHeader('content-type', 'text/event-stream')
      const emit = (event) => res.write(`data: ${JSON.stringify(event)}\n\n`)
      if (req.url === '/v1/chat/completions') {
        emit({ id: 'chat_test', object: 'chat.completion.chunk', model, choices: [{ index: 0, delta: { role: 'assistant', content: 'Seen' }, finish_reason: null }] })
        emit({ id: 'chat_test', object: 'chat.completion.chunk', model, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })
        res.end('data: [DONE]\n\n')
      } else {
        const item = { type: 'message', id: 'msg_test', role: 'assistant', status: 'in_progress', content: [] }
        emit({ type: 'response.created', response: { id: 'resp_test', model, status: 'in_progress', output: [] } })
        emit({ type: 'response.output_item.added', output_index: 0, item })
        emit({ type: 'response.content_part.added', item_id: item.id, output_index: 0, content_index: 0, part: { type: 'output_text', text: '', annotations: [] } })
        emit({ type: 'response.output_text.delta', item_id: item.id, output_index: 0, content_index: 0, delta: 'Seen' })
        item.content = [{ type: 'output_text', text: 'Seen', annotations: [] }]
        item.status = 'completed'
        emit({ type: 'response.output_item.done', output_index: 0, item })
        emit({ type: 'response.completed', response: { id: 'resp_test', model, status: 'completed', output: [item], usage: { input_tokens: 20, output_tokens: 1, total_tokens: 21 } } })
        res.end()
      }
    })
    const dir = await mkdtemp(join(tmpdir(), 'pi-image-transport-'))
    let child
    try {
      server.listen(0, '127.0.0.1')
      await once(server, 'listening')
      const gateway = `http://127.0.0.1:${server.address().port}`
      child = spawn('pi', [
        '--mode', 'rpc', '--no-session', '--no-extensions', '-e', extension,
        '--no-context-files', '--no-skills', '--no-prompt-templates', '--no-tools',
        '--provider', 'nautionette', '--model', model
      ], {
        cwd: dir,
        env: { ...process.env, PI_CODING_AGENT_DIR: dir, PI_OFFLINE: '1', PI_TELEMETRY: '0',
          AGENTGATEWAY_URL: gateway, MCP_URL: `${gateway}/mcp`, AGENT_MODEL: model, NAUTIONETTE_MODEL_IMAGES: 'true', NAUTIONETTE_MODEL_API: pinnedApi || '' },
        stdio: ['pipe', 'pipe', 'pipe']
      })
      let stderr = '', buffer = '', answer = ''
      child.stderr.on('data', (chunk) => { stderr += chunk })
      const settled = new Promise((resolve, reject) => {
        const timer = setTimeout(() => { child.kill(); reject(new Error(`Pi timed out: ${stderr}`)) }, 20000)
        child.on('error', (error) => { clearTimeout(timer); reject(error) })
        child.on('close', (code) => { clearTimeout(timer); reject(new Error(`Pi exited ${code}: ${stderr}`)) })
        child.stdout.setEncoding('utf8')
        child.stdout.on('data', (chunk) => {
          buffer += chunk
          let index
          while ((index = buffer.indexOf('\n')) !== -1) {
            const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
            if (!line.trim()) continue
            const event = JSON.parse(line)
            if (event.type === 'message_end' && event.message.role === 'assistant') {
              answer = event.message.content.filter((part) => part.type === 'text').map((part) => part.text).join('')
            }
            if (event.type === 'agent_settled') { clearTimeout(timer); resolve() }
          }
        })
      })
      child.stdin.write(JSON.stringify({ type: 'prompt', message: 'Describe these images.', images }) + '\n')
      await settled
      assert.equal(answer, 'Seen')
      assert.equal(requests.length, 1)
      assert.equal(catalogRequests, pinnedApi ? 0 : 1)
      assert.equal(requests[0].path, `/v1${endpoint}`)
      const body = requests[0].body
      assert.equal(body.model, model)
      const user = (body.messages || body.input).find((message) => message.role === 'user')
      const urls = user.content.filter((part) => ['image_url', 'input_image'].includes(part.type))
        .map((part) => typeof part.image_url === 'string' ? part.image_url : part.image_url.url)
      assert.deepEqual(urls, images.map((image) => `data:${image.mimeType};base64,${image.data}`))
    } finally {
      if (child && child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close'); child.kill(); await closed
      }
      server.closeAllConnections()
      await new Promise((resolve) => server.close(resolve))
      await rm(dir, { recursive: true, force: true })
    }
  })
}
