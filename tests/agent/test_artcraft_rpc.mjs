// Real Pi model/tool loop against real CADCraft and a local offline gateway.
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { once } from 'node:events'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { loadCraftConfig } from '../../images/agent-sets/default/extensions/nautionette/artcraft.ts'
import { outputTransport } from './output_fixture.mjs'

const source = fileURLToPath(new URL('../../images/agent-sets/default/extensions/nautionette/', import.meta.url))
const config = loadCraftConfig({ catalogFile: fileURLToPath(new URL('../../images/pi-base/artcraft/catalog.json', import.meta.url)), selection: undefined })
const root = process.env.ARTCRAFT_TEST_ROOT || '/opt/artcraft'
const available = existsSync(join(root, 'cadcraft', 'manifest.json')) && spawnSync('pi', ['--version']).status === 0
if (process.env.ARTCRAFT_REQUIRE_NATIVE) assert.ok(available, 'Real Pi and CADCraft must be installed in the agent image')

for (const selection of [null, []]) {
  test(`real Pi discovers native tools, draws and publishes an editable CAD output (MCP selection ${JSON.stringify(selection)})`, { skip: !available, timeout: 45000 }, async () => {
    const cwd = await mkdtemp(join(tmpdir(), 'pi-artcraft-rpc-'))
    const extension = join(cwd, 'extension.ts'), grantFile = join(cwd, 'capability.json')
    const grant = 'private-artcraft-output-grant-with-32-characters'
    await writeFile(grantFile, JSON.stringify({ grant }), { mode: 0o600 })
    // Use production provider/output wiring. Inject only the test installation
    // path; the shipped image uses the same adapter with its fixed /opt root.
    await writeFile(extension, `import main from ${JSON.stringify(pathToFileURL(join(source, 'index.ts')).href)};
      import { registerCraftTools } from ${JSON.stringify(pathToFileURL(join(source, 'artcraft.ts')).href)};
      export default async function(pi) { await main(pi); registerCraftTools(pi, ${JSON.stringify(config)}, {root:${JSON.stringify(root)}}); }`)
    const calls = [
      ['craft_apps', {}],
      ['craft_tools', { app: 'cadcraft' }],
      ['craft_call', { app: 'cadcraft', tool: 'script', arguments: { text: 'rectang 0,0 100,60\ncircle 10,10 2.5\ncircle 90,10 2.5\ncircle 90,50 2.5\ncircle 10,50 2.5' } }],
      ['craft_call', { app: 'cadcraft', tool: 'save', arguments: { path: join(cwd, 'plate.dxf') } }],
      ['craft_call', { app: 'cadcraft', tool: 'render', arguments: { width: 640, height: 400, path: join(cwd, 'plate.png') } }],
      ['publish_output', { title: 'Mounting plate', source_app: 'CADCraft', files: [
        { path: join(cwd, 'plate.dxf'), role: 'source' }, { path: join(cwd, 'plate.png'), role: 'preview' }
      ] }]
    ]
    const requests = [], events = [], errors = []
    const transfer = await outputTransport({ onPublish: args => {
      assert.equal(args.request_id, 'call-5')
      assert.deepEqual(args.files.map(file => file.role), ['source', 'preview'])
      const dxf = Buffer.from(args.files[0].data, 'base64').toString()
      assert.equal((dxf.match(/\nCIRCLE\r?\n/g) || []).length, 4)
      assert.deepEqual(Buffer.from(args.files[1].data, 'base64').subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
    } })
    const publications = transfer.records
    const server = createServer(async (req, res) => {
      try {
        let raw = ''; for await (const chunk of req) raw += chunk
        const body = raw ? JSON.parse(raw) : {}
        if (req.url === '/mcp') {
          let result = {}
          if (body.method === 'tools/list') result = { tools: [
            { name: 'craft_call', description: 'Remote shadow must not register', inputSchema: { type: 'object' } },
            { name: 'backend_prepare_chat_output', inputSchema: { type: 'object' } },
            { name: 'backend_publish_chat_output', inputSchema: { type: 'object' } }
          ] }
          if (body.method === 'tools/call') {
            assert.equal(body.params.arguments.body.grant, grant)
            result = await transfer.rpc(body.method, body.params, AbortSignal.timeout(20000))
          }
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result })); return
        }
        const names = body.tools.map(tool => tool.function.name)
        assert.ok(names.includes('craft_apps') && names.includes('craft_tools') && names.includes('craft_call') && names.includes('publish_output'))
        assert.equal(names.filter(name => name === 'craft_call').length, 1)
        assert.ok(!names.includes('backend_publish_chat_output'))
        assert.ok(!JSON.stringify(body).includes(grant))
        const index = requests.length; requests.push(body)
        if (index >= 5) assert.ok(JSON.stringify(body.messages).includes('data:image/png;base64,'), 'Engine images must reach the model')
        res.setHeader('content-type', 'text/event-stream')
        const chunk = (delta, finish_reason = null) => res.write(`data: ${JSON.stringify({ id: 'craft-response', object: 'chat.completion.chunk', model: 'test/model', choices: [{ index: 0, delta, finish_reason }] })}\n\n`)
        if (index < calls.length) {
          const [name, args] = calls[index]
          chunk({ role: 'assistant', tool_calls: [{ index: 0, id: `call-${index}`, type: 'function', function: { name, arguments: JSON.stringify(args) } }] })
          chunk({}, 'tool_calls')
        } else { chunk({ role: 'assistant', content: 'Published the CAD drawing and preview.' }); chunk({}, 'stop') }
        res.end('data: [DONE]\n\n')
      } catch (error) { errors.push(error); res.statusCode = 500; res.end(error.message) }
    })
    let child, stderr = ''
    try {
      server.listen(0, '127.0.0.1'); await once(server, 'listening')
      const gateway = `http://127.0.0.1:${server.address().port}`
      child = spawn('pi', ['--mode', 'rpc', '--no-session', '--no-extensions', '-e', extension,
        '--no-context-files', '--no-skills', '--no-prompt-templates', '--provider', 'nautionette', '--model', 'test/model'], {
        cwd, env: { ...process.env, PI_CODING_AGENT_DIR: cwd, PI_OFFLINE: '1', PI_TELEMETRY: '0',
          AGENTGATEWAY_URL: gateway, MCP_URL: `${gateway}/mcp`, AGENT_MODEL: 'test/model',
          NAUTIONETTE_MODEL_API: 'openai-completions', NAUTIONETTE_TOOLS_JSON: JSON.stringify(selection),
          NAUTIONETTE_ARTCRAFT_JSON: '[]', NAUTIONETTE_OUTPUT_GRANT_FILE: grantFile }, stdio: ['pipe', 'pipe', 'pipe']
      })
      child.stderr.on('data', chunk => { stderr += chunk })
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`Pi creative-app test timed out: ${stderr}`)), 30000)
        child.on('error', reject)
        child.on('close', code => { clearTimeout(timer); reject(new Error(`Pi exited ${code}: ${stderr}`)) })
        let buffer = ''; child.stdout.setEncoding('utf8')
        child.stdout.on('data', chunk => {
          buffer += chunk; let index
          while ((index = buffer.indexOf('\n')) !== -1) {
            const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
            if (!line.trim()) continue
            const event = JSON.parse(line); events.push(event)
            if (event.type === 'agent_settled') { clearTimeout(timer); resolve() }
          }
        })
        child.stdin.write(JSON.stringify({ type: 'prompt', message: 'Create a 100 x 60 mounting plate with four corner holes and publish its DXF and preview.' }) + '\n')
      })
      assert.deepEqual(errors, [])
      assert.equal(publications.length, 1, JSON.stringify(events.filter(event => event.type === 'tool_execution_end')))
      assert.equal(requests.length, calls.length + 1)
      const results = events.filter(event => event.type === 'tool_execution_end')
      assert.deepEqual(results.map(result => result.toolName), calls.map(call => call[0]))
      assert.ok(results.every(result => !result.isError), JSON.stringify(results))
      assert.ok(!JSON.stringify(events).includes(grant))
      assert.ok((await readFile(join(cwd, 'plate.dxf'))).length > 0)
    } finally {
      if (child && child.exitCode === null && child.signalCode === null) { const closed = once(child, 'close'); child.kill(); await closed }
      server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
      await transfer.close()
      await rm(cwd, { force: true, recursive: true })
    }
  })
}
