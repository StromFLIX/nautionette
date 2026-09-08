import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'

const source = readFileSync(new URL('../../images/pi-base/agent-run.mjs', import.meta.url), 'utf8').replace(/^import .*;\n/gm, '')

async function run (job) {
  const commands = [], output = [], writes = [], removed = []
  let spawnArgs, spawnOptions
  await runInNewContext(source, {
    Buffer, console,
    mkdirSync () {}, existsSync () { return false },
    writeFileSync (path, value) { writes.push([path, value]) },
    readFileSync (path) { assert.equal(path, '/tmp/nautionette-job.json'); return JSON.stringify(job) },
    unlinkSync (path) { removed.push(path) },
    projectEnvironment () { return {} }, contextUsage () { return null },
    createChatControl () { return { receive () {}, close () {} } },
    listenForChatControl () { return { close () {} } },
    process: { env: { AGENT_JOB_FILE: '/tmp/nautionette-job.json' }, stdout: { write (line) { output.push(JSON.parse(line)) } } },
    spawn (_command, args, options) {
      spawnArgs = args; spawnOptions = options
      const child = new EventEmitter()
      child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.stdin = new EventEmitter()
      child.stdout.setEncoding = child.stderr.setEncoding = () => {}
      child.stdin.write = (line) => {
        const command = JSON.parse(line)
        commands.push(command)
        if (command.type === 'prompt') setImmediate(() => {
          child.stdout.emit('data', JSON.stringify({ type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text: 'Seen' }] } }) + '\n')
          child.stdout.emit('data', '{"type":"agent_end"}\n')
        })
      }
      child.kill = () => child.emit('close', 0)
      return child
    }
  })
  return { commands, output, writes, removed, spawnArgs, spawnOptions }
}

const image = (data) => ({ type: 'image', mimeType: 'image/png', data })
test('historical and current images reach Pi RPC in labelled order, never argv or JOB.json', async () => {
  const result = await run({ chat_id: 'chat', prompt: 'Compare these', model: 'vision',
    history: [{ role: 'user', content: 'Before', images: [image('older')] }, { role: 'assistant', content: 'Reply' }],
    images: [image('newer'), image('last')], model_api: 'openai-completions' })
  const command = result.commands.find((c) => c.type === 'prompt')
  assert.deepEqual(command.images, [image('older'), image('newer'), image('last')])
  assert.match(command.message, /User: Before\n\[Attached image 1\]/)
  assert.match(command.message, /Compare these\n\[Attached image 2\]\n\[Attached image 3\]/)
  assert.equal(result.spawnOptions.env.NAUTIONETTE_MODEL_IMAGES, 'true')
  assert.equal(result.spawnOptions.env.NAUTIONETTE_MODEL_API, 'openai-completions')
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
  assert.equal(textOnly.commands.find((c) => c.type === 'prompt').images, undefined)
})
