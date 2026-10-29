// Real production runner + Pi RPC + loopback model: fresh processes resume the
// exact native session. No Docker, credentials, network or paid provider needed.
import assert from 'node:assert/strict'
import { spawn as spawnChild } from 'node:child_process'
import { once } from 'node:events'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { createChatControl } from '../../images/pi-base/chat-control.mjs'
import { prepareFiles, fileReferences } from '../../images/pi-base/chat-files.mjs'
import { createChatRecovery } from '../../images/pi-base/chat-recovery.mjs'
import { prepareSession } from '../../images/pi-base/chat-session.mjs'

const runner = readFileSync(new URL('../../images/pi-base/agent-run.mjs', import.meta.url), 'utf8').replace(/^import .*;\n/gm, '')
const extension = fileURLToPath(new URL('../../images/pi-base/chat-session-extension.mjs', import.meta.url))

test('native RPC continuity across tools, manual compaction, model change and attachment restore', { timeout: 30000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'native-chat-rpc-'))
  const workspace = join(dir, 'workspace'), config = join(dir, 'agent'), state = join(dir, 'state')
  mkdirSync(workspace); mkdirSync(config)
  const operation = join(dir, 'operation.txt')
  const evidence = 'long-tool-evidence-' + 'X'.repeat(5000) + '-END-EVIDENCE'
  let phase = 'first', calls = 0, child
  const requests = [], outputs = []
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk
    const request = JSON.parse(raw); requests.push({ phase, request }); calls++
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    const chunk = (delta, finish_reason = null) => res.write(`data: ${JSON.stringify({
      id: `response-${calls}`, object: 'chat.completion.chunk', model: 'test-model',
      choices: [{ index: 0, delta, finish_reason }]
    })}\n\n`)
    chunk({ role: 'assistant', content: '' })
    if (phase === 'first' && calls === 1) {
      chunk({ tool_calls: [{ index: 0, id: 'write-once', type: 'function', function: {
        name: 'bash', arguments: JSON.stringify({ command: `printf once >> '${operation}'; printf '${evidence}'` })
      } }] }); chunk({}, 'tool_calls')
    } else if (phase === 'resumed' && !request.messages.some(m => m.role === 'tool' && m.content?.includes('END-EVIDENCE'))) {
      chunk({ tool_calls: [{ index: 0, id: 'lookup', type: 'function', function: {
        name: 'chat_history', arguments: JSON.stringify({ query: 'END-EVIDENCE' })
      } }] }); chunk({}, 'tool_calls')
    } else {
      chunk({ content: phase === 'compact' ? 'Summary: a write-once operation completed; verify old evidence with chat_history.' : `Completed ${phase}.` })
      chunk({}, 'stop')
    }
    res.end('data: [DONE]\n\n')
  })
  try {
    server.listen(0, '127.0.0.1'); await once(server, 'listening')
    writeFileSync(join(config, 'settings.json'), JSON.stringify({ retry: { enabled: false }, compaction: { keepRecentTokens: 1, reserveTokens: 1000 } }))
    const provider = join(dir, 'provider.mjs')
    writeFileSync(provider, `export default pi => { pi.registerProvider('nautionette', {
      baseUrl: 'http://127.0.0.1:${server.address().port}/v1', apiKey: 'offline-test', api: 'openai-completions',
      models: ['test-model', 'text-model'].map(id => ({ id, name: id, reasoning: false, input: ['text'],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 32000, maxTokens: 2048 }))
    }); pi.on('session_start', (_e, ctx) => {
      if (!ctx.sessionManager.getEntries().some(e => e.customType === 'test.state')) pi.appendEntry('test.state', { counter: 7 });
    }); }`)
    async function run(job) {
      const output = []; outputs.push(output)
      await runInNewContext(runner.replace('const workspace = "/workspace";', `const workspace = ${JSON.stringify(workspace)};`), {
        Buffer, console, createChatControl, createChatRecovery, fileReferences,
        prepareSession: (job, ws) => prepareSession(job, ws, state),
        prepareFiles: (job, ws) => prepareFiles(job, ws, join(state, 'attachments')),
        createTelemetry () { return { start () {}, end () {}, finish () {}, async flush () {} } },
        mkdirSync, existsSync: path => path === '/workspace-defaults' || path === '/root/.pi/agent' ? false : existsSync(path), writeFileSync,
        readFileSync, preparePackages () { return {} }, projectEnvironment () { return {} }, contextUsage () { return null },
        listenForChatControl () { return { close () {} } },
        process: { env: { AGENT_JOB: Buffer.from(JSON.stringify({ chat_id: 'native-test', model: 'test-model', ...job })).toString('base64') },
          stdout: { write (line) { output.push(JSON.parse(line)) } } },
        spawn (command, args, options) {
          args = args.map(arg => arg.endsWith('/chat-session-extension.mjs') ? extension : arg)
          child = spawnChild(command, [...args, '--no-extensions', '-e', provider, '--no-context-files', '--no-skills', '--no-prompt-templates'], {
            ...options, env: { ...options.env, PATH: process.env.PATH, HOME: dir, PI_CODING_AGENT_DIR: config, PI_OFFLINE: '1', PI_TELEMETRY: '0' }
          }); return child
        }
      })
      assert.equal(output.at(-1).ok, true, JSON.stringify(output))
      return output
    }
    const attachment = { id: 'a'.repeat(32), name: 'source.txt', mime_type: 'text/plain', data: Buffer.from('original bytes').toString('base64') }
    const first = await run({ turn_id: 'one', prompt: 'Perform this once.', files: [attachment] })
    assert.equal(first.filter(e => e.type === 'tool').length, 1)
    assert.ok(first.find(e => e.type === 'tool_done').result.length < evidence.length)
    phase = 'compact'
    const compact = await run({ turn_id: 'two', prompt: '/compact Preserve the completed operation.' })
    assert.ok(compact.some(e => e.type === 'compaction' && e.state === 'completed'))
    // Delete scratch state; originals survive outside /workspace.
    rmSync(workspace, { recursive: true }); mkdirSync(workspace)
    phase = 'resumed'
    await run({ turn_id: 'three', prompt: 'Inspect the previous evidence, never repeat the operation.', model: 'text-model', supports_images: false,
      system_prompt: 'Current permissions and fresh instructions apply.' })
    assert.equal(readFileSync(operation, 'utf8'), 'once')
    assert.equal(readFileSync(join(state, `attachments/${'a'.repeat(32)}.txt`), 'utf8'), 'original bytes')
    assert.equal(new Set(outputs.map(out => out.find(e => e.type === 'session_ready').id)).size, 1)
    const native = readFileSync(join(state, 'session.jsonl'), 'utf8').trim().split('\n').map(JSON.parse)
    assert.ok(native.some(e => e.type === 'compaction'))
    assert.ok(native.some(e => e.customType === 'test.state' && e.data.counter === 7))
    assert.ok(native.some(e => e.message?.role === 'toolResult' && JSON.stringify(e).includes(evidence)))
    for (const id of ['one', 'three']) assert.ok(native.some(e => e.customType === 'nautionette.input-entry' && e.data.inputId === id))
    const resumed = requests.filter(r => r.phase === 'resumed').map(r => r.request)
    assert.ok(resumed[0].messages.some(m => JSON.stringify(m).includes('Summary: a write-once operation')))
    assert.ok(resumed[0].messages.some(m => JSON.stringify(m).includes('Current permissions and fresh instructions')))
    assert.ok(resumed.at(-1).messages.some(m => m.role === 'tool' && m.content.includes('END-EVIDENCE')))
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) { const closed = once(child, 'close'); child.kill(); await closed }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
    rmSync(dir, { recursive: true, force: true })
  }
})
