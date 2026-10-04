// Real Pi RPC + the production runner, with a loopback-only model. No provider
// keys, paid calls or Docker: prove that the tool history and workspace survive.
import assert from 'node:assert/strict'
import { spawn as spawnChild, spawnSync } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { createChatControl } from '../../images/pi-base/chat-control.mjs'
import { prepareFiles, fileReferences } from '../../images/pi-base/chat-files.mjs'
import { createChatRecovery } from '../../images/pi-base/chat-recovery.mjs'

const piAvailable = spawnSync('pi', ['--version']).status === 0
const source = (await readFile(new URL('../../images/pi-base/agent-run.mjs', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gm, '')

for (const outcome of ['recovered', 'exhausted', 'permanent']) {
  test(`real RPC ${outcome}: completed tool runs once and continuation retains its result`, { skip: !piAvailable, timeout: 30000 }, async () => {
    const dir = await mkdtemp(join(tmpdir(), 'pi-chat-recovery-'))
    const operation = join(dir, 'operation.txt')
    const requests = [], output = []
    let child, stderr = ''
    const server = createServer(async (req, res) => {
      let raw = ''
      for await (const chunk of req) raw += chunk
      const request = JSON.parse(raw)
      requests.push(request)
      if (requests.length > 1 && !(outcome === 'recovered' && requests.length === 3)) {
        res.writeHead(outcome === 'permanent' ? 403 : 503, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ error: { message: outcome === 'permanent' ? 'Permission denied' : 'Service unavailable' } }))
        return
      }
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      const chunk = (delta, finish_reason = null) => res.write(`data: ${JSON.stringify({
        id: `response-${requests.length}`, object: 'chat.completion.chunk', model: 'test-model',
        choices: [{ index: 0, delta, finish_reason }]
      })}\n\n`)
      chunk({ role: 'assistant', content: '' })
      if (requests.length === 1) {
        chunk({ tool_calls: [{ index: 0, id: 'write-once', type: 'function', function: {
          name: 'bash', arguments: JSON.stringify({ command: `printf 'once\\n' >> '${operation}'; printf 'operation-complete'` })
        } }] })
        chunk({}, 'tool_calls')
      } else {
        chunk({ content: 'Resumed from the completed tool without repeating it.' })
        chunk({}, 'stop')
      }
      res.end('data: [DONE]\n\n')
    })
    try {
      server.listen(0, '127.0.0.1')
      await once(server, 'listening')
      const config = join(dir, 'agent')
      await mkdir(config)
      // Force the terminal-failure path rather than let native retries conceal it.
      await writeFile(join(config, 'settings.json'), JSON.stringify({ retry: { enabled: false, provider: { maxRetries: 0 } } }))
      const provider = join(dir, 'provider.ts')
      await writeFile(provider, `export default function (pi) {
        pi.registerProvider('nautionette', {
          baseUrl: 'http://127.0.0.1:${server.address().port}/v1', apiKey: 'test', api: 'openai-completions',
          models: [{ id: 'test-model', name: 'Test model', reasoning: false, input: ['text'],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 32000, maxTokens: 2048 }]
        })
      }`)
      // Only file/system setup is isolated. Real commands, events, recovery logic
      // and the actual Pi subprocess are unchanged; no /workspace files are touched.
      await runInNewContext(source, {
        Buffer, console, createChatControl, createChatRecovery, prepareFiles, fileReferences,
        mkdirSync () {}, existsSync () { return false }, writeFileSync () {},
        preparePackages () { return {} }, projectEnvironment () { return {} }, contextUsage () { return null },
        listenForChatControl () { return { close () {} } },
        process: { env: { AGENT_JOB: Buffer.from(JSON.stringify({ chat_id: 'recovery-test',
          prompt: 'Perform the operation once, then report it.', model: 'test-model' })).toString('base64') },
        stdout: { write (line) { output.push(JSON.parse(line)) } } },
        spawn (command, args, options) {
          child = spawnChild(command, [...args, '--no-extensions', '-e', provider,
            '--no-context-files', '--no-skills', '--no-prompt-templates'], {
            ...options, cwd: dir,
            env: { ...options.env, PATH: process.env.PATH, HOME: dir, PI_CODING_AGENT_DIR: config,
              PI_OFFLINE: '1', PI_TELEMETRY: '0' }
          })
          child.stderr.on('data', chunk => { stderr += chunk })
          return child
        }
      })
      const result = output.at(-1)
      assert.equal(result.type, 'result', stderr)
      assert.equal(result.ok, outcome === 'recovered', JSON.stringify(result) + stderr)
      assert.equal(requests.length, { recovered: 3, exhausted: 4, permanent: 2 }[outcome])
      assert.equal(await readFile(operation, 'utf8'), 'once\n')
      assert.equal(output.filter(event => event.type === 'tool').length, 1)
      assert.equal(output.filter(event => event.type === 'tool_done').length, 1)
      assert.equal(output.filter(event => event.type === 'input_consumed').length, 0)
      assert.equal(output.filter(event => event.type === 'result').length, 1)
      assert.equal(output.filter(event => event.type === 'recovery').length, { recovered: 1, exhausted: 2, permanent: 0 }[outcome])
      for (const request of requests.slice(2)) {
        assert.ok(request.messages.some(message => message.role === 'tool' && message.tool_call_id === 'write-once' && message.content.includes('operation-complete')))
        assert.ok(request.messages.some(message => message.role === 'user' && JSON.stringify(message.content).includes('automatic recovery notice')))
      }
      if (outcome === 'recovered') {
        assert.equal(result.text, 'Resumed from the completed tool without repeating it.')
        assert.equal(output.some(event => event.type === 'error'), false)
      } else if (outcome === 'exhausted') assert.match(result.error, /stopped after 2 attempt/)
    } finally {
      if (child && child.exitCode === null && child.signalCode === null) {
        const closed = once(child, 'close'); child.kill(); await closed
      }
      server.closeAllConnections()
      await new Promise(resolve => server.close(resolve))
      await rm(dir, { recursive: true, force: true })
    }
  })
}
