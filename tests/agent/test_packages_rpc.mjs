// Real installed Pi + local package + production provider, without a paid model.
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { once } from 'node:events'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { preparePackages } from '../../images/pi-base/package-runtime.mjs'

const available = spawnSync('pi', ['--version']).status === 0
const provider = fileURLToPath(new URL('../../images/agent-sets/default/extensions/nautionette/index.ts', import.meta.url))

test('real Pi discovers filtered package resources and expands a template after native history', { skip: !available, timeout: 30000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'managed-pi-rpc-'))
  const agentDir = join(dir, 'agent'), artifactDir = join(dir, 'artifacts'), id = 'a'.repeat(32)
  const root = join(artifactDir, id, 'package')
  for (const path of [agentDir, join(root, 'prompts'), join(root, 'skills', 'demo'), join(root, 'extensions')]) mkdirSync(path, { recursive: true })
  writeFileSync(join(root, 'package.json'), JSON.stringify({ name: 'test-package', pi: {
    extensions: ['extensions'], prompts: ['prompts'], skills: ['skills']
  } }))
  writeFileSync(join(root, 'prompts', 'review.md'), '---\ndescription: Review one file\n---\nReview target: $1\n')
  writeFileSync(join(root, 'prompts', 'disabled.md'), 'Must not load')
  writeFileSync(join(root, 'skills', 'demo', 'SKILL.md'), '---\nname: demo\ndescription: Test skill\n---\nA test skill.')
  writeFileSync(join(root, 'extensions', 'demo.ts'), `export default function(pi) {
    pi.registerCommand('configured', { description: 'Test config', handler: async (_args, ctx) => {
      ctx.ui.notify(process.env.SERVICE_API_KEY === 'private' ? 'Configured' : 'Missing');
    } });
  }`)
  const delivery = join(dir, 'private.json')
  writeFileSync(delivery, JSON.stringify([{ installation_id: id, root: 'package', filters: { prompts: ['prompts/review.md'] },
    configuration: { env: { SERVICE_API_KEY: 'private' }, files: {} } }]))
  const env = preparePackages({ file: delivery, agentDir, artifactDir, inheritedEnv: {} })
  const history = join(dir, 'history.jsonl')
  writeFileSync(history, [
    { type: 'session', version: 3, id: '12345678-1234-1234-1234-123456789012', timestamp: new Date().toISOString(), cwd: dir },
    { type: 'message', id: 'aaaaaaaa', parentId: null, timestamp: new Date().toISOString(), message: { role: 'user', content: 'Earlier question', timestamp: Date.now() } }
  ].map(JSON.stringify).join('\n') + '\n')
  const requests = []
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw || '{}')
    if (req.url === '/mcp') {
      res.setHeader('content-type', 'application/json')
      res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result: { tools: [] } })); return
    }
    requests.push(body)
    res.setHeader('content-type', 'text/event-stream')
    res.write(`data: ${JSON.stringify({ id: 'test', object: 'chat.completion.chunk', model: 'test/model', choices: [{ index: 0, delta: { role: 'assistant', content: 'Reviewed' }, finish_reason: null }] })}\n\n`)
    res.write(`data: ${JSON.stringify({ id: 'test', object: 'chat.completion.chunk', model: 'test/model', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })}\n\n`)
    res.end('data: [DONE]\n\n')
  })
  let child, timer
  try {
    server.listen(0, '127.0.0.1'); await once(server, 'listening')
    const url = `http://127.0.0.1:${server.address().port}`
    child = spawn('pi', ['--mode', 'rpc', '--session', history, '-e', provider, '--no-context-files', '--no-tools', '--provider', 'nautionette', '--model', 'test/model'], {
      cwd: dir, env: { ...process.env, ...env, PI_CODING_AGENT_DIR: agentDir, PI_OFFLINE: '1', PI_TELEMETRY: '0',
        AGENTGATEWAY_URL: url, MCP_URL: `${url}/mcp`, AGENT_MODEL: 'test/model', NAUTIONETTE_MODEL_API: 'openai-completions' },
      stdio: ['pipe', 'pipe', 'pipe']
    })
    let buffer = '', stderr = ''
    const events = []
    child.stderr.on('data', chunk => { stderr += chunk })
    const send = command => child.stdin.write(JSON.stringify(command) + '\n')
    const done = new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error(`Pi timed out: ${stderr}`)), 20000)
      child.on('error', reject)
      child.stdout.on('data', chunk => {
        buffer += chunk
        let index
        while ((index = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
          if (!line.trim()) continue
          const event = JSON.parse(line); events.push(event)
          if (event.id === 'commands' && event.type === 'response') {
            send({ id: 'config', type: 'prompt', message: '/configured' })
          }
          if (event.id === 'config' && event.type === 'response') {
            send({ id: 'review', type: 'prompt', message: '/review file.ts' })
          }
          if (event.type === 'agent_settled') resolve()
        }
      })
    })
    send({ id: 'commands', type: 'get_commands' })
    await done
    const commands = events.find(event => event.id === 'commands').data.commands.map(command => command.name)
    assert.ok(commands.includes('review') && commands.includes('skill:demo') && commands.includes('configured'))
    assert.ok(!commands.includes('disabled'))
    assert.ok(events.some(event => event.type === 'extension_ui_request' && event.message === 'Configured'))
    const users = requests[0].messages.filter(message => message.role === 'user')
    const text = JSON.stringify(users)
    assert.match(text, /Earlier question/)
    assert.match(text, /Review target: file.ts/)
    assert.ok(!text.includes('/review file.ts'))
    assert.ok(!readFileSync(join(agentDir, 'settings.json'), 'utf8').includes('private'))
  } finally {
    clearTimeout(timer)
    if (child && child.exitCode === null && child.signalCode === null) { const closed = once(child, 'close'); child.kill(); await closed }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
    rmSync(dir, { recursive: true, force: true })
  }
})
