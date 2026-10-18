import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { CRAFT_TOOL_NAMES, loadCraftConfig, registerCraftTools } from '../../images/agent-sets/default/extensions/nautionette/artcraft.ts'

const catalogFile = fileURLToPath(new URL('../../images/pi-base/artcraft/catalog.json', import.meta.url))
const config = loadCraftConfig({ catalogFile, selection: undefined })
const parse = result => JSON.parse(result.content[0].text)
const cli = `#!/usr/bin/env node
const { createInterface } = require('node:readline');
const { appendFileSync } = require('node:fs');
appendFileSync('started.log', JSON.stringify({ pid: process.pid, args: process.argv.slice(2), env: Object.keys(process.env) }) + '\\n');
let counter = 0;
const text = value => ({ content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }] });
createInterface({ input: process.stdin }).on('line', async line => {
  const m = JSON.parse(line); if (m.id == null) return;
  if (process.argv.includes('--hang-init') && m.method === 'initialize') return;
  let result;
  if (m.method === 'initialize') result = { protocolVersion: '2025-06-18', instructions: 'Save the document.' };
  else if (m.method === 'tools/list') result = { tools: ['echo', 'increment', 'delay', 'image', 'large', 'reject', 'crash', 'invalid', ...Array.from({length: 35}, (_, i) => 'query_' + i)].map(name => ({ name, description: name + ' tool', inputSchema: { type: 'object', additionalProperties: true } })) };
  else {
    const { name, arguments: args } = m.params;
    appendFileSync('calls.log', name + '\\n');
    if (args.wait) await new Promise(resolve => setTimeout(resolve, args.wait));
    if (name === 'crash') process.exit(9);
    if (name === 'invalid') { process.stdout.write('not-json\\n'); return; }
    if (name === 'increment') result = text(++counter);
    else if (name === 'image') result = { content: [{ type: 'image', mimeType: 'image/png', data: 'aW1hZ2U=' }] };
    else if (name === 'large') result = text('x'.repeat(50000));
    else if (name === 'reject') result = { ...text('Rejected mutation'), isError: true };
    else result = text(args);
  }
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: m.id, result }) + '\\n');
});
`

async function fixture(t, options = {}) {
  const cwd = await mkdtemp(join(tmpdir(), 'craft-adapter-'))
  const root = join(cwd, 'apps'), tools = new Map(), hooks = new Map()
  const selected = config.slice(0, 4).map(item => ({ ...item, args: options.hangStartup ? ['--hang-init'] : item.args }))
  for (const item of selected) {
    await mkdir(join(root, item.app, 'bin'), { recursive: true })
    await writeFile(join(root, item.app, 'bin', `${item.app}-cli`), cli, { mode: 0o755 })
    await writeFile(join(root, item.app, 'manifest.json'), JSON.stringify(item))
  }
  registerCraftTools({ registerTool: tool => tools.set(tool.name, tool), on: (event, callback) => hooks.set(event, callback) }, selected, { root, ...options })
  const call = (tool, args = {}, signal) => tools.get(tool).execute('test', args, signal, null, { cwd })
  t.after(async () => { await hooks.get('session_shutdown')(); await rm(cwd, { recursive: true, force: true }) })
  return { cwd, root, call, hooks, selected }
}

test('catalog has all 13 apps, strict turn pins, and an explicit empty selection disables tools', () => {
  assert.equal(config.length, 13)
  assert.deepEqual([...CRAFT_TOOL_NAMES], ['craft_apps', 'craft_tools', 'craft_call'])
  assert.deepEqual(loadCraftConfig({ catalogFile, selection: '[]' }), [])
  for (const selection of ['null', '{}', '[{"app":"../evil"}]', JSON.stringify([{ app: config[0].app, release: { ...config[0], args: ['bad'] } }])]) {
    assert.throws(() => loadCraftConfig({ catalogFile, selection }), /Invalid ArtCraft/)
  }
  assert.deepEqual(loadCraftConfig({ catalogFile: '/missing/catalog.json' }), [])
  const tools = []
  registerCraftTools({ registerTool: tool => tools.push(tool) }, [])
  assert.equal(tools.length, 0)
})

test('listing is lazy, bounded discovery returns full schemas on demand, and children receive no grants', async t => {
  const { call, cwd, selected } = await fixture(t)
  process.env.NAUTIONETTE_TEST_SECRET = 'must-not-be-in-child-env'
  t.after(() => { delete process.env.NAUTIONETTE_TEST_SECRET })
  const apps = parse(await call('craft_apps'))
  assert.equal(apps.apps.length, 4)
  assert.ok(apps.apps.every(item => !item.open))
  await assert.rejects(readFile(join(cwd, 'started.log')), { code: 'ENOENT' })
  const app = selected[0].app
  const listed = parse(await call('craft_tools', { app }))
  assert.equal(listed.tools.length, 30)
  assert.equal(listed.total, 43)
  assert.equal(listed.next_offset, 30)
  assert.ok(!listed.tools[0].inputSchema)
  const query = parse(await call('craft_tools', { app, query: 'query_', offset: 30 }))
  assert.equal(query.tools.length, 5)
  const detail = parse(await call('craft_tools', { app, name: 'echo' }))
  assert.equal(detail.tool.inputSchema.type, 'object')
  assert.equal(detail.instructions, 'Save the document.')
  const started = JSON.parse((await readFile(join(cwd, 'started.log'), 'utf8')).trim())
  assert.ok(!started.env.includes('NAUTIONETTE_TEST_SECRET'))
  assert.ok(!started.env.some(key => /TOKEN|GRANT|GATEWAY|NAUTIONETTE/.test(key)))
  const result = parse(await call('craft_call', { app, tool: 'echo', arguments: { text: 'hello' } }))
  assert.equal(result.text, 'hello')
  await assert.rejects(call('craft_call', { app, tool: 'invented' }), /Unknown application tool/)
  assert.equal((await readFile(join(cwd, 'started.log'), 'utf8')).trim().split('\n').length, 1)
})

test('mutations to the same engine are serialized even when tool calls overlap', async t => {
  const { call, selected } = await fixture(t)
  const app = selected[0].app
  await call('craft_apps', { action: 'open', app })
  const results = await Promise.all([
    call('craft_call', { app, tool: 'increment', arguments: { wait: 60 } }),
    call('craft_call', { app, tool: 'increment' })
  ])
  assert.deepEqual(results.map(parse), [1, 2])
})

test('image results remain model-visible and large text is written to bounded local context', async t => {
  const { call, selected } = await fixture(t)
  const app = selected[0].app
  const image = await call('craft_call', { app, tool: 'image' })
  assert.deepEqual(image.content, [{ type: 'image', mimeType: 'image/png', data: 'aW1hZ2U=' }])
  const large = await call('craft_call', { app, tool: 'large' })
  assert.ok(large.content[0].text.length < 25000)
  const path = large.content[0].text.split('Full result saved to ')[1]
  assert.equal((await readFile(path, 'utf8')).length, 50000)
  await assert.rejects(call('craft_call', { app, tool: 'reject' }), /Rejected mutation/)
})

for (const tool of ['crash', 'invalid']) {
  test(`a ${tool} never replays a mutation; explicit reopen starts empty state`, async t => {
    const { call, cwd, selected } = await fixture(t)
    const app = selected[0].app
    assert.equal(parse(await call('craft_call', { app, tool: 'increment' })), 1)
    await assert.rejects(call('craft_call', { app, tool }), /not replay|not.*replay|do not replay/i)
    await assert.rejects(call('craft_call', { app, tool: 'increment' }), /state|replay|reopen/i)
    await call('craft_apps', { action: 'open', app })
    assert.equal(parse(await call('craft_call', { app, tool: 'increment' })), 1)
    assert.equal((await readFile(join(cwd, 'calls.log'), 'utf8')).split('\n').filter(name => name === tool).length, 1)
  })
}

test('aborting an active operation kills the app and leaves no silently reusable state', async t => {
  const { call, selected } = await fixture(t)
  const app = selected[0].app
  await call('craft_apps', { action: 'open', app })
  const abort = new AbortController()
  const pending = call('craft_call', { app, tool: 'delay', arguments: { wait: 500 } }, abort.signal)
  setTimeout(() => abort.abort(), 30)
  await assert.rejects(pending, /cancelled/)
  await assert.rejects(call('craft_call', { app, tool: 'increment' }), /reopen|restore/i)
  await call('craft_apps', { action: 'open', app })
  assert.equal(parse(await call('craft_call', { app, tool: 'increment' })), 1)
})

test('abort while queued does not dispatch the call or kill an unrelated active call', async t => {
  const { call, selected, cwd } = await fixture(t)
  const app = selected[0].app
  await call('craft_apps', { action: 'open', app })
  const first = call('craft_call', { app, tool: 'increment', arguments: { wait: 100 } })
  const abort = new AbortController()
  const second = call('craft_call', { app, tool: 'echo' }, abort.signal)
  abort.abort()
  const rejected = assert.rejects(second, /abort/i)
  assert.equal(parse(await first), 1)
  await rejected
  assert.equal((await readFile(join(cwd, 'calls.log'), 'utf8')).trim(), 'increment')
})

test('timeouts stop an app, explicit closes free slots, and concurrent app count is bounded', async t => {
  const { call, selected } = await fixture(t, { maxSessions: 2, timeout: 1500 })
  const [first, second, third] = selected.map(item => item.app)
  await call('craft_apps', { action: 'open', app: first })
  await call('craft_apps', { action: 'open', app: second })
  await assert.rejects(call('craft_apps', { action: 'open', app: third }), /At most 2/)
  await call('craft_apps', { action: 'close', app: second })
  await call('craft_apps', { action: 'open', app: third })
  await assert.rejects(call('craft_call', { app: first, tool: 'delay', arguments: { wait: 5000 } }), /timed out/)
})

test('shutdown stops a still-initializing engine without waiting for its request timeout', { timeout: 4000 }, async t => {
  const { call, selected, hooks, cwd } = await fixture(t, { hangStartup: true, timeout: 10000 })
  const opening = call('craft_apps', { action: 'open', app: selected[0].app })
  const rejected = assert.rejects(opening, /closed|exited/)
  for (let attempt = 0; attempt < 50; attempt++) {
    if (await readFile(join(cwd, 'started.log')).catch(() => null)) break
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  const start = Date.now()
  await hooks.get('session_shutdown')()
  await rejected
  assert.ok(Date.now() - start < 1000)
})

test('pinned manifests are checked before launch, and explicit reopen can recover from failed startup', async t => {
  const { call, selected, root } = await fixture(t)
  const item = selected[0], path = join(root, item.app, 'manifest.json')
  await writeFile(path, JSON.stringify({ ...item, version: '9.9.9' }))
  await assert.rejects(call('craft_apps', { action: 'open', app: item.app }), /pinned version/)
  await writeFile(path, JSON.stringify(item))
  await call('craft_apps', { action: 'open', app: item.app })
  assert.equal(parse(await call('craft_call', { app: item.app, tool: 'increment' })), 1)
  await assert.rejects(call('craft_tools', { app: 'not-installed' }), /unavailable/)
})
