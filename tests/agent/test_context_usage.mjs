import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { contextUsage } from '../../images/pi-base/context-usage.mjs'

const message = (usage) => ({ role: 'assistant', content: [{ type: 'text', text: 'hello' }], usage })
const usage = { input: 1000, output: 200, cacheRead: 3000, cacheWrite: 400, totalTokens: 4600 }

test('context includes cached input and output without double counting totalTokens', () => {
  assert.deepEqual(contextUsage(message(usage), 'test/model'), {
    model: 'test/model', tokens: 4600, input_tokens: 4400, output_tokens: 200,
    cache_read_tokens: 3000, cache_write_tokens: 400, source: 'provider'
  })
  assert.equal(contextUsage(message({ ...usage, input: 0 }), 'test/model').tokens, 3600)
})

test('missing, zero, malformed and non-assistant usage stay unknown', () => {
  for (const invalid of [null, {}, { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    { ...usage, input: -1 }, { ...usage, input: '1000' }, { ...usage, input: NaN }]) {
    assert.equal(contextUsage(message(invalid), 'test/model'), null)
  }
  assert.equal(contextUsage({ role: 'user', usage }, 'test/model'), null)
})

// Run the real wrapper with a fake Pi process: no model, Docker or network needed.
async function runAgent (events, job = {}) {
  const output = []
  const source = readFileSync(new URL('../../images/pi-base/agent-run.mjs', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '')
  await runInNewContext(source, {
    Buffer, console, contextUsage,
    mkdirSync () {}, writeFileSync () {}, existsSync () { return false },
    projectEnvironment () { return {} },
    preparePackages () {}, randomUUID () { return 'test-session'; },
    setTimeout, clearTimeout,
    process: {
      env: { AGENT_JOB: Buffer.from(JSON.stringify({ model: 'test/model', prompt: 'test', ...job })).toString('base64') },
      stdout: { write (line) { output.push(JSON.parse(line)) } }
    },
    spawn () {
      const child = new EventEmitter()
      child.stdout = new EventEmitter()
      child.stderr = new EventEmitter()
      child.stdout.setEncoding = child.stderr.setEncoding = () => {}
      setImmediate(() => {
        child.stdout.emit('data', events.map((event) => JSON.stringify(event)).join('\n') + '\n')
        child.emit('close', 0)
      })
      return child
    }
  })
  return output
}

test('reported thinking and reply boundaries are forwarded as timing phases', async () => {
  const events = await runAgent([
    ...['thinking_start', 'thinking_end', 'text_start', 'text_end', 'toolcall_start'].map(type => ({
      type: 'message_update', assistantMessageEvent: { type }
    })),
    { type: 'message_end', message: message(usage) }
  ])
  assert.deepEqual(events.filter(event => event.type === 'phase'), [
    { type: 'phase', phase: 'thinking' }, { type: 'phase', phase: 'other' },
    { type: 'phase', phase: 'reply' }, { type: 'phase', phase: 'other' },
    { type: 'phase', phase: 'other' }
  ])
})

test('tool-loop usage is streamed and the final result keeps only the latest request', async () => {
  const smaller = { ...usage, input: 50, cacheRead: 0, cacheWrite: 0 }
  const events = await runAgent([
    { type: 'message_end', message: message(usage) },
    { type: 'tool_execution_end', toolCallId: '1', toolName: 'read', result: 'x'.repeat(10000) },
    { type: 'message_end', message: message(smaller) }
  ])
  assert.deepEqual(events.filter((event) => event.type === 'usage').map((event) => event.context.tokens), [4600, 250])
  assert.equal(events.at(-1).context.tokens, 250)
  assert.deepEqual(events.at(-1).usage, smaller)
})

test('structured output uses the final object, never an earlier fenced placeholder', async () => {
  const final = { summary: '# Real digest\nCommit details with {braces} and "quotes".', nested: { count: 2 } }
  for (const text of [
    JSON.stringify(final),
    '```json\n' + JSON.stringify(final) + '\n```',
    'Enough data.\n```json\n{"summary":"placeholder"}\n```\nCompiling now.' + JSON.stringify(final),
    '```json\n{"summary":"placeholder"}\n```\n```json\n' + JSON.stringify(final) + '\n```'
  ]) {
    const events = await runAgent([
      { type: 'message_end', message: { ...message(usage), content: [{ type: 'text', text }] } }
    ], { output_schema: { type: 'object', required: ['summary'] } })
    assert.equal(events.at(-1).ok, true)
    assert.deepEqual(events.at(-1).output, final)
  }
})

test('malformed final output cannot fall back to an earlier valid draft', async () => {
  for (const ending of ['{"summary":"unfinished', 'Actual answer is not JSON.', '{"other":"wrong schema"}']) {
    const text = '```json\n{"summary":"placeholder"}\n```\n' + ending
    const events = await runAgent([
      { type: 'message_end', message: { ...message(usage), content: [{ type: 'text', text }] } }
    ], { output_schema: { type: 'object', required: ['summary'] } })
    assert.equal(events.at(-1).ok, false)
  }
})

test('usage survives structured-output failure and missing upstream usage clears old counts', async () => {
  const failed = await runAgent([{ type: 'message_end', message: message(usage) }], { output_schema: { type: 'object' } })
  assert.equal(failed.at(-1).ok, false)
  assert.equal(failed.at(-1).context.tokens, 4600)
  const missing = await runAgent([
    { type: 'message_end', message: message(usage) },
    { type: 'message_end', message: message(null) }
  ])
  assert.equal(missing.at(-1).context, null)
})
