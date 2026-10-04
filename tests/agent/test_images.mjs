import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { createChatRecovery } from '../../images/pi-base/chat-recovery.mjs'

const source = ['chat-files.mjs', 'agent-run.mjs'].map(name =>
  readFileSync(new URL(`../../images/pi-base/${name}`, import.meta.url), 'utf8')
    .replace(/^#!.*\n/, '').replace(/^import .*;\n/gm, '').replace(/^export /gm, '')
).join('\n')

async function run (job, events = null) {
  const commands = [], output = [], writes = [], removed = []
  let spawnArgs, spawnOptions
  await runInNewContext(source, {
    Buffer, console, createChatRecovery,
    mkdirSync () {}, existsSync () { return false },
    writeFileSync (path, value, options) { writes.push([path, value, options]) },
    readFileSync (path) { assert.equal(path, '/tmp/nautionette-job.json'); return JSON.stringify(job) },
    unlinkSync (path) { removed.push(path) },
    projectEnvironment () { return {} }, contextUsage () { return null },
    preparePackages () { return {} }, randomUUID () { return 'a'.repeat(32) },
    createChatControl () { return { receive () {}, close () {} } },
    listenForChatControl () { return { close () {} } },
    process: { env: { AGENT_JOB_FILE: '/tmp/nautionette-job.json' }, stdout: { write (line) { output.push(JSON.parse(line)) } } },
    spawn (_command, args, options) {
      spawnArgs = args; spawnOptions = options
      const child = new EventEmitter()
      let killed = false
      child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.stdin = new EventEmitter()
      child.stdout.setEncoding = child.stderr.setEncoding = () => {}
      child.stdin.write = (line) => {
        const command = JSON.parse(line)
        commands.push(command)
        if (command.type === 'prompt') setImmediate(() => {
          if (events) {
            for (const event of events) {
              if (killed) break
              child.stdout.emit('data', JSON.stringify(event) + '\n')
            }
            return
          }
          child.stdout.emit('data', JSON.stringify({ type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text: 'Seen' }] } }) + '\n')
          child.stdout.emit('data', '{"type":"agent_end"}\n')
          child.stdout.emit('data', '{"type":"agent_settled"}\n')
        })
      }
      child.kill = () => { killed = true; child.emit('close', 0) }
      return child
    }
  })
  return { commands, output, writes, removed, spawnArgs, spawnOptions }
}

test('raw current and historical files reach the workspace, never model bytes or JOB.json', async () => {
  const bytes = Buffer.from('%PDF-1.7\noriginal\x00\xff', 'latin1')
  const file = (id, name) => ({ id: id.repeat(32), name, mime_type: 'application/pdf', data: bytes.toString('base64') })
  const result = await run({ chat_id: 'chat', supports_images: false, prompt: 'Inspect these',
    files: [file('a', '../../AGENTS.md')],
    history: [{ role: 'user', content: 'Previous PDF', files: [file('b', 'previous.pdf')] }] })
  const current = `/workspace/attachments/${'a'.repeat(32)}.md`
  const previous = `/workspace/attachments/${'b'.repeat(32)}.pdf`
  for (const path of [current, previous]) {
    const [, saved, options] = result.writes.find(([name]) => name === path)
    assert.ok(saved.equals(bytes))
    assert.equal(options.mode, 0o600)
    assert.equal(options.flag, 'wx')
  }
  const command = result.commands.find(c => c.type === 'prompt')
  assert.ok(command.message.includes(current))
  assert.equal(command.images, undefined)
  assert.equal(command.message.includes(bytes.toString('base64')), false)
  const history = result.writes.find(([path]) => path.endsWith('history.jsonl'))[1]
  assert.ok(history.includes(previous))
  assert.equal(history.includes(bytes.toString('base64')), false)
  const stored = JSON.parse(result.writes.find(([path]) => path.endsWith('JOB.json'))[1])
  assert.equal(stored.files[0].path, current)
  assert.equal(stored.files[0].data, undefined)
  assert.equal(result.output.at(-1).ok, true)
})

const image = (data) => ({ type: 'image', mimeType: 'image/png', data })
test('history has native roles/images and current images reach RPC, never argv or JOB.json', async () => {
  const result = await run({ chat_id: 'chat', prompt: 'Compare these', model: 'vision',
    history: [{ role: 'user', content: 'Before', images: [image('older')] }, { role: 'assistant', content: 'Reply' }],
    images: [image('newer'), image('last')], model_api: 'openai-completions', reasoning_effort: 'max',
    model_reasoning: { supported: true, efforts: ['low', 'max'], format: 'openrouter' } })
  const command = result.commands.find((c) => c.type === 'prompt')
  assert.deepEqual(command.images, [image('newer'), image('last')])
  assert.match(command.message, /Compare these\n\[Attached image 1\]\n\[Attached image 2\]/)
  const history = result.writes.find(([path]) => path.endsWith('history.jsonl'))[1].trim().split('\n').map(JSON.parse)
  assert.equal(history[1].message.role, 'user')
  assert.deepEqual(history[1].message.content, [{ type: 'text', text: 'Before' }, image('older')])
  assert.equal(history[2].message.role, 'assistant')
  assert.ok(result.spawnArgs.includes('--session'))
  assert.equal(result.spawnOptions.env.NAUTIONETTE_MODEL_IMAGES, 'true')
  assert.equal(result.spawnOptions.env.NAUTIONETTE_MODEL_API, 'openai-completions')
  assert.equal(result.spawnOptions.env.NAUTIONETTE_REASONING_EFFORT, 'max')
  assert.deepEqual(JSON.parse(result.spawnOptions.env.NAUTIONETTE_MODEL_REASONING), {
    supported: true, efforts: ['low', 'max'], format: 'openrouter'
  })
  assert.equal(result.spawnArgs.includes('older'), false)
  const stored = JSON.parse(result.writes.find(([path]) => path.endsWith('JOB.json'))[1])
  assert.equal(stored.images, undefined)
  assert.equal(stored.history, undefined)
  assert.deepEqual(result.removed, ['/tmp/nautionette-job.json'])
  assert.equal(result.output.at(-1).ok, true)
})

test('image-only prompts have useful text and text-only model declarations stay explicit', async () => {
  const result = await run({ chat_id: 'chat', images: [image('data')] })
  assert.match(result.commands.find((c) => c.type === 'prompt').message, /Please examine/)
  const textOnly = await run({ chat_id: 'chat', prompt: 'hello', supports_images: false })
  assert.equal(textOnly.spawnOptions.env.NAUTIONETTE_MODEL_IMAGES, 'false')
  assert.equal(textOnly.spawnOptions.env.NAUTIONETTE_MODEL_API, '')
  assert.equal(textOnly.spawnOptions.env.NAUTIONETTE_REASONING_EFFORT, '')
  assert.equal(textOnly.spawnOptions.env.NAUTIONETTE_MODEL_REASONING, '{}')
  assert.equal(textOnly.commands.find((c) => c.type === 'prompt').images, undefined)
})

for (const tools of [undefined, null, [], ['read'], ['name,with,commas']]) {
  test(`the wrapper transports tool selection ${JSON.stringify(tools)} without broadening it`, async () => {
    const result = await run({ chat_id: 'chat', prompt: 'hi', tools })
    assert.equal(result.spawnOptions.env.NAUTIONETTE_TOOLS_JSON, JSON.stringify(tools ?? null))
  })
}

test('selected packages receive same-turn background guidance without replacing the agent prompt', async () => {
  const result = await run({ chat_id: 'chat', prompt: 'hi', system_prompt: 'Review carefully.', packages: ['revision'] })
  const prompt = result.spawnArgs[result.spawnArgs.indexOf('--append-system-prompt') + 1]
  assert.match(prompt, /^Review carefully\./)
  assert.match(prompt, /bg_wait with the returned run ID/)
  assert.match(prompt, /cannot survive across turns/)
  const withoutPackages = await run({ chat_id: 'chat', prompt: 'hi', system_prompt: 'Review carefully.', packages: [] })
  assert.equal(withoutPackages.spawnArgs[withoutPackages.spawnArgs.indexOf('--append-system-prompt') + 1], 'Review carefully.')
})

test('RPC waits for settled after retries and never publishes a recovered error as terminal', async () => {
  const result = await run({ chat_id: 'chat', prompt: '/review' }, [
    { type: 'message_end', message: { role: 'assistant', stopReason: 'error', errorMessage: 'temporary overload' } },
    { type: 'agent_end' },
    { type: 'auto_retry_end', success: true },
    { type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text: 'Recovered' }] } },
    { type: 'agent_end' },
    { type: 'agent_settled' }
  ])
  assert.equal(result.output.at(-1).ok, true)
  assert.equal(result.output.at(-1).text, 'Recovered')
  assert.equal(result.output.some(event => event.type === 'error'), false)
  assert.equal(result.commands.find(command => command.type === 'prompt').message, '/review')
})

test('unsupported extension dialogs are cancelled and fail with a Settings-directed message', async () => {
  for (const method of ['select', 'confirm', 'input', 'editor']) {
    const result = await run({ chat_id: 'chat', prompt: 'hello' }, [
      { type: 'extension_ui_request', id: 'dialog', method }
    ])
    assert.equal(result.output.at(-1).ok, false)
    assert.match(result.output.at(-1).error, /Settings/)
    assert.deepEqual(result.commands.at(-1), { type: 'extension_ui_response', id: 'dialog', cancelled: true })
  }
})

test('extension command discovery and model-free completion do not hang or invoke the model', async () => {
  const commands = [{ name: 'test', source: 'extension', description: 'A command' }]
  const result = await run({ chat_id: 'chat', prompt: '/test' }, [
    { type: 'response', id: 'commands', success: true, data: { commands } },
    { type: 'response', id: 'initial', success: true },
    { type: 'response', id: 'command-completion', success: true, data: { isStreaming: false, pendingMessageCount: 0 } }
  ])
  assert.deepEqual(result.output.find(event => event.type === 'commands').commands, commands)
  assert.ok(result.commands.some(command => command.type === 'get_state'))
  assert.equal(result.output.at(-1).text, 'Extension command completed.')
})
