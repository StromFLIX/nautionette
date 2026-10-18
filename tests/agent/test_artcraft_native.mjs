// Runs offline against the shipped CLIs, not stubs. CI runs this in the actual image.
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { loadCraftConfig, registerCraftTools } from '../../images/agent-sets/default/extensions/nautionette/artcraft.ts'
import { registerOutputTools } from '../../images/agent-sets/default/extensions/nautionette/outputs.ts'

const catalogFile = fileURLToPath(new URL('../../images/pi-base/artcraft/catalog.json', import.meta.url))
const config = loadCraftConfig({ catalogFile, selection: undefined })
const root = process.env.ARTCRAFT_TEST_ROOT || '/opt/artcraft'
const available = config.every(item => existsSync(join(root, item.app, 'manifest.json')))
if (process.env.ARTCRAFT_REQUIRE_NATIVE) assert.ok(available, 'The agent image must contain all 13 native applications')
const parse = result => JSON.parse(result.content.filter(part => part.type === 'text').map(part => part.text).join('\n'))

function agent(cwd, rpc) {
  const tools = new Map(), hooks = new Map()
  const pi = { registerTool: tool => tools.set(tool.name, tool), on: (event, callback) => hooks.set(event, callback) }
  registerCraftTools(pi, config, { root })
  if (rpc) registerOutputTools(pi, rpc, { grant: 'test-private-output-capability-with-32-chars' })
  return {
    call: (name, args) => tools.get(name).execute('test-call', args, undefined, null, { cwd }),
    native: (app, tool, args = {}) => tools.get('craft_call').execute('test-call', { app, tool, arguments: args }, undefined, null, { cwd }),
    close: () => hooks.get('session_shutdown')()
  }
}

async function workspace(t) {
  const cwd = await mkdtemp(join(tmpdir(), 'craft-native-'))
  t.after(() => rm(cwd, { force: true, recursive: true }))
  return cwd
}

test('every bundled ArtCraft app exposes real headless MCP tool schemas without a display/network', { skip: !available, timeout: 180000 }, async t => {
  const cwd = await workspace(t), session = agent(cwd)
  t.after(session.close)
  for (const item of config) {
    const tools = parse(await session.call('craft_tools', { app: item.app }))
    assert.ok(tools.total > 0, item.app)
    const schema = parse(await session.call('craft_tools', { app: item.app, name: tools.tools[0].name }))
    assert.equal(schema.tool.inputSchema.type, 'object', item.app)
    await session.call('craft_apps', { action: 'close', app: item.app })
  }
})

test('real CAD -> published editable source/PNG -> fresh turn restoration -> revised 6mm holes', { skip: !available, timeout: 120000 }, async t => {
  const cwd = await workspace(t), revisions = []
  const outputId = 'a'.repeat(32)
  const rpc = async (_method, params) => {
    const args = params.arguments.body
    assert.ok(args.grant)
    let value
    if (params.name === 'backend_publish_chat_output') {
      if (revisions.length) assert.equal(args.expected_revision, revisions.length)
      const revision = revisions.length + 1
      value = { id: outputId, revision, title: args.title, files: args.files.map((file, index) => ({
        id: String(index + 1).repeat(32), name: file.name, mime_type: file.mime_type, role: file.role,
        size: Buffer.from(file.data, 'base64').length
      })) }
      revisions.push({ output: value, data: args.files.map(file => file.data) })
    } else {
      assert.equal(params.name, 'backend_read_chat_output')
      const record = revisions[(args.revision || revisions.length) - 1]
      value = { output: record.output }
      if (args.file_id) {
        const index = record.output.files.findIndex(file => file.id === args.file_id)
        value = { output_id: outputId, revision: record.output.revision, file: { ...record.output.files[index], data: record.data[index] } }
      }
    }
    return { content: [{ type: 'text', text: JSON.stringify(value) }] }
  }
  const firstDir = join(cwd, 'turn1'); await mkdir(firstDir)
  const first = agent(firstDir, rpc); t.after(first.close)
  await first.native('cadcraft', 'new_drawing', { metric: true })
  await first.native('cadcraft', 'command_line', { text: 'rectang 0,0 100,60' })
  const centers = [[10, 10], [90, 10], [90, 50], [10, 50]]
  for (const center of centers) await first.native('cadcraft', 'execute', { command: 'circle', params: { center, radius: 2.5 } })
  await first.native('cadcraft', 'command_line', { text: 'dimlinear 0,0 100,0 50,-8' })
  await first.native('cadcraft', 'command_line', { text: 'dimlinear 100,0 100,60 108,30' })
  const source = join(firstDir, 'plate.dxf'), preview = join(firstDir, 'plate.png')
  await first.native('cadcraft', 'save', { path: source })
  const rendered = await first.native('cadcraft', 'render', { width: 900, height: 600, path: preview })
  assert.ok(rendered.content.some(part => part.type === 'image' && part.mimeType === 'image/png'))
  const geometry = parse(await first.native('cadcraft', 'inspect_drawing', { entities: true }))
  assert.equal(geometry.counts.Circle, 4)
  assert.equal(geometry.counts.Polyline, 1)
  assert.ok(geometry.entities.some(entity => entity.type.includes('Dimension')))
  const publication = parse(await first.call('publish_output', { title: 'Mounting plate', source_app: 'CADCraft', files: [
    { path: source, role: 'source' }, { path: preview, role: 'preview' }
  ] }))
  assert.equal(publication.output.revision, 1)
  const originalSource = revisions[0].data[0]
  await first.close()
  await rm(firstDir, { recursive: true }) // Prove that publication is not a link to sandbox files.

  const nextDir = join(cwd, 'turn2'); await mkdir(nextDir)
  const next = agent(nextDir, rpc); t.after(next.close)
  const restored = parse(await next.call('read_output', { output_id: outputId, file_id: publication.output.files[0].id }))
  assert.equal((await readFile(restored.file.path)).toString('base64'), originalSource)
  await next.native('cadcraft', 'open', { path: restored.file.path })
  const circles = parse(await next.native('cadcraft', 'query_entities', { type: 'Circle' }))
  assert.equal(circles.count, 4)
  assert.ok(circles.entities.every(entity => entity.geometry.radius === 2.5))
  await next.native('cadcraft', 'execute', { command: 'erase', params: { handles: circles.entities.map(entity => entity.handle) } })
  for (const center of centers) await next.native('cadcraft', 'execute', { command: 'circle', params: { center, radius: 3 } })
  const revisedSource = join(nextDir, 'plate.dxf'), revisedPreview = join(nextDir, 'plate.png')
  await next.native('cadcraft', 'save', { path: revisedSource })
  await next.native('cadcraft', 'render', { width: 900, height: 600, path: revisedPreview })
  const changed = parse(await next.native('cadcraft', 'query_entities', { type: 'Circle' }))
  assert.equal(changed.count, 4)
  assert.ok(changed.entities.every(entity => entity.geometry.radius === 3))
  const revised = parse(await next.call('publish_output', { title: 'Mounting plate', source_app: 'CADCraft', output_id: outputId, expected_revision: 1,
    files: [{ path: revisedSource, role: 'source' }, { path: revisedPreview, role: 'preview' }] }))
  assert.equal(revised.output.revision, 2)
  assert.equal(revisions[0].data[0], originalSource)
  assert.notEqual(revisions[1].data[0], originalSource)
  assert.deepEqual(Buffer.from(revisions[1].data[1], 'base64').subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
})

test('WordCraft creates editable DOCX and PDF/PNG exports; GridCraft preserves spreadsheet formulas', { skip: !available, timeout: 120000 }, async t => {
  const cwd = await workspace(t), session = agent(cwd)
  t.after(session.close)
  await session.native('wordcraft', 'new_document', { template: 'blank' })
  await session.native('wordcraft', 'type_text', { text: 'A server-side document\nCreated without a browser.', paragraphs: true })
  for (const extension of ['docx', 'pdf', 'png']) {
    const path = join(cwd, `report.${extension}`)
    await session.native('wordcraft', 'save_document', { path })
    assert.ok((await readFile(path)).length > 0)
  }
  assert.ok((await session.native('wordcraft', 'render_page', { page: 1 })).content.some(part => part.type === 'image'))
  await session.native('gridcraft', 'new_workbook', {})
  await session.native('gridcraft', 'write_range', { range: 'A1', values: [['Item', 'Cost'], ['One', 12], ['Two', 8], ['Total', '=SUM(B2:B3)']] })
  const cell = parse(await session.native('gridcraft', 'get_cell', { cell: 'B4' }))
  assert.ok(JSON.stringify(cell).includes('20'))
  const path = join(cwd, 'costs.xlsx')
  await session.native('gridcraft', 'save_workbook', { path })
  assert.equal((await readFile(path)).subarray(0, 2).toString(), 'PK')
  await session.call('craft_apps', { action: 'close', app: 'gridcraft' })
  await session.native('gridcraft', 'open_workbook', { path })
  const restored = parse(await session.native('gridcraft', 'get_cell', { cell: 'B4' }))
  assert.ok(JSON.stringify(restored).includes('20'))
})
