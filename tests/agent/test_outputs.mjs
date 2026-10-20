import assert from 'node:assert/strict'
import { chmod, mkdtemp, readFile, readdir, rm, stat, symlink, truncate, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { loadOutputGrant, registerOutputTools, sandboxFile, isOutputTransportTool, publicationRequestId } from '../../images/agent-sets/default/extensions/nautionette/outputs.ts'
import { digest, outputTransport } from './output_fixture.mjs'

const grant = { grant: 'test-capability-with-at-least-32-characters' }
const outputId = 'a'.repeat(32)
const wire = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] })
async function workspace (fn) {
  const directory = await mkdtemp(join(tmpdir(), 'chat-outputs-'))
  const transport = await outputTransport()
  try { await fn(directory, transport) } finally { await transport.close(); await rm(directory, { force: true, recursive: true }) }
}
function tools (rpc, capability = grant) {
  const registered = new Map()
  registerOutputTools({ registerTool: tool => registered.set(tool.name, tool) }, rpc, capability)
  return registered
}
const execute = (tool, id, args, cwd, signal) => tool.execute(id, args, signal, undefined, { cwd })

async function publish (cwd, transport, bytes = Buffer.from('editable-source-document'), toolId = 'tool-call-1') {
  await writeFile(join(cwd, 'bracket.dxf'), bytes)
  return execute(tools(transport.rpc).get('publish_output'), toolId, { title: 'Bracket', files: [{ path: 'bracket.dxf', role: 'source' }] }, cwd)
}

test('a capability is read privately, unlinked, and never exposed in tool schemas', () => workspace(async cwd => {
  const path = join(cwd, 'capability.json')
  await writeFile(path, JSON.stringify(grant), { mode: 0o600 })
  assert.deepEqual(loadOutputGrant(path), grant)
  assert.equal(loadOutputGrant(path), null)
  await writeFile(path, JSON.stringify({ grant: 'invalid-secret' }))
  assert.throws(() => loadOutputGrant(path), error => !error.message.includes('invalid-secret'))
  const registered = tools(() => {})
  assert.deepEqual([...registered.keys()], ['publish_output', 'read_output'])
  assert.ok(!JSON.stringify([...registered.values()]).includes(grant.grant))
  assert.equal(tools(() => {}, null).size, 0)
  for (const name of ['backend_prepare_chat_output', 'backend_publish_chat_output', 'backend_read_chat_output']) assert.ok(isOutputTransportTool(name))
  assert.ok(!isOutputTransportTool('backend_system_status'))
}))

test('publication instructions distinguish server-assigned creates from existing-output updates', () => {
  const registered = tools(() => {})
  const publisher = registered.get('publish_output')
  assert.deepEqual(publisher.parameters.required, ['title', 'files'])
  assert.match(publisher.description, /omit both output_id and expected_revision/)
  assert.match(publisher.description, /Never invent an output ID/)
  for (const name of ['output_id', 'expected_revision']) {
    assert.match(publisher.parameters.properties[name].description, /UPDATE ONLY/)
    assert.match(publisher.parameters.properties[name].description, /For CREATE, omit/)
  }
  assert.match(registered.get('read_output').description, /discover existing output IDs and current revisions/)
})

test('an undeletable capability file fails closed before tools can be registered', { skip: process.getuid?.() === 0 }, () => workspace(async cwd => {
  const path = join(cwd, 'capability.json')
  await writeFile(path, JSON.stringify(grant), { mode: 0o600 })
  await chmod(cwd, 0o500)
  try { assert.throws(() => loadOutputGrant(path), /Cannot consume output capability/) }
  finally { await chmod(cwd, 0o700) }
}))

test('publish streams binary bytes separately and returns only committed metadata', () => workspace(async (cwd, transport) => {
  const bytes = Buffer.from([0, 255, 1, 20])
  const result = await publish(cwd, transport, bytes)
  assert.deepEqual(transport.calls.map(call => call.name), ['backend_prepare_chat_output', 'backend_publish_chat_output'])
  assert.deepEqual(transport.calls[0].arguments.body.files, [{ name: 'bracket.dxf', mime_type: 'image/vnd.dxf', role: 'source', size: bytes.length, sha256: digest(bytes) }])
  assert.equal(transport.calls[0].arguments.body.grant, grant.grant)
  assert.equal(transport.calls[0].arguments.body.request_id, 'tool-call-1')
  assert.ok(!Object.hasOwn(transport.calls[0].arguments.body, 'output_id'))
  assert.ok(!Object.hasOwn(transport.calls[0].arguments.body, 'expected_revision'))
  assert.equal(transport.records[0].files[0].data, bytes.toString('base64'))
  assert.deepEqual(result.details.output, transport.records[0].output)
  for (const secret of [grant.grant, bytes.toString('base64'), cwd, 'private-upload-capability']) assert.ok(!JSON.stringify(result).includes(secret))
}))

test('long and Unicode provider IDs are normalized deterministically without collisions', () => workspace(async (cwd, transport) => {
  const toolId = 'provider-tool-call-'.repeat(100)
  await publish(cwd, transport, undefined, toolId)
  assert.equal(transport.calls[0].arguments.body.request_id, `sha256:${digest(toolId)}`)
  assert.equal(publicationRequestId('a'.repeat(256)), 'a'.repeat(256))
  assert.equal(publicationRequestId('a'.repeat(257)), `sha256:${digest('a'.repeat(257))}`)
  assert.equal(publicationRequestId('📁'.repeat(256)), '📁'.repeat(256))
  assert.equal(publicationRequestId('📁'.repeat(257)), `sha256:${digest('📁'.repeat(257))}`)
  assert.notEqual(publicationRequestId(toolId), publicationRequestId(`${toolId}2`))
}))

test('rejects secret paths, escaping symlinks, empty files, directories and protocol-oversized files', () => workspace(async cwd => {
  await assert.rejects(sandboxFile({ path: '/proc/self/environ' }, cwd), /workspace/)
  await assert.rejects(sandboxFile({ path: '/etc/passwd' }, cwd), /workspace/)
  await symlink('/etc/passwd', join(cwd, 'escaped.txt'))
  await assert.rejects(sandboxFile({ path: 'escaped.txt' }, cwd), /workspace/)
  await assert.rejects(sandboxFile({ path: cwd }, cwd), /workspace/)
  await writeFile(join(cwd, 'empty'), '')
  await assert.rejects(sandboxFile({ path: 'empty' }, cwd), /nonempty/)
  await truncate(join(cwd, 'empty'), 1024 ** 3 + 1)
  await assert.rejects(sandboxFile({ path: 'empty' }, cwd), /1 GiB/)
}))

test('effective quotas no longer hard-code 20 MiB inside the agent', () => workspace(async (cwd, transport) => {
  const bytes = Buffer.alloc(21 * 1024 ** 2, 7)
  const result = await publish(cwd, transport, bytes)
  assert.equal(result.details.output.files[0].size, bytes.length)
  assert.ok(JSON.stringify(transport.calls).length < 4096)
}))

test('read_output streams exact bytes into a new mode-0600 file without exposing private URLs', () => workspace(async (cwd, transport) => {
  const bytes = Buffer.from('editable-source-document')
  const published = await publish(cwd, transport, bytes)
  const fileId = published.details.output.files[0].id
  const result = await execute(tools(transport.rpc).get('read_output'), 'restore', { output_id: outputId, file_id: fileId }, cwd)
  const path = result.details.file.path
  assert.ok(path.startsWith(`${cwd}/output-`))
  assert.ok(path.endsWith('/document.dxf'))
  assert.deepEqual(await readFile(path), bytes)
  assert.equal((await stat(path)).mode & 0o777, 0o600)
  assert.equal(result.details.file.sha256, digest(bytes))
  for (const secret of [bytes.toString('base64'), 'private-download-capability', 'download_url', grant.grant]) assert.ok(!JSON.stringify(result).includes(secret))
}))

test('corrupt binary downloads fail checksum checks and remove partial files', () => workspace(async cwd => {
  const transport = await outputTransport({ transformDownload: bytes => Buffer.alloc(bytes.length, 0) })
  try {
    const result = await publish(cwd, transport)
    const before = await readdir(cwd)
    await assert.rejects(execute(tools(transport.rpc).get('read_output'), 'restore', { output_id: outputId, file_id: result.details.output.files[0].id }, cwd), /checksum/)
    assert.deepEqual(await readdir(cwd), before)
  } finally { await transport.close() }
}))

test('file changes after preparation are detected before any commit', () => workspace(async cwd => {
  const transport = await outputTransport({ beforePrepare: async () => { await writeFile(join(cwd, 'bracket.dxf'), 'changed') } })
  try {
    await assert.rejects(publish(cwd, transport), /changed while publishing|not published/)
    assert.equal(transport.records.length, 0)
  } finally { await transport.close() }
}))

for (const [uploadStatus, code] of [[507, 'output_disk_full'], [503, 'output_storage_unavailable']]) {
  test(`binary upload HTTP ${uploadStatus} returns a distinct storage error and never leaks private provider data`, () => workspace(async cwd => {
    const transport = await outputTransport({ uploadStatus })
    try {
      await assert.rejects(publish(cwd, transport), error => {
        const detail = JSON.parse(error.message)
        assert.equal(detail.code, code)
        assert.equal(detail.settings_url, '/settings/storage')
        assert.ok(!error.message.includes('do-not-expose') && !error.message.includes('127.0.0.1'))
        return true
      })
      assert.equal(transport.records.length, 0)
      assert.equal(transport.calls.length, 1) // An unsuccessful transfer must never commit.
    } finally { await transport.close() }
  }))
}

test('quota errors preserve actionable details and aborts never publish', () => workspace(async (cwd, transport) => {
  const listing = { outputs: [{ id: outputId, revision: 1 }] }
  assert.deepEqual((await execute(tools(async () => wire(listing)).get('read_output'), 'list', {}, cwd)).details, listing)
  const detail = { code: 'output_storage_limit_exceeded', limit_bytes: 10, requested_bytes: 20, settings_url: 'https://app.test/settings/storage' }
  const failed = tools(async () => ({ ...wire({ detail }), isError: true }))
  await assert.rejects(execute(failed.get('publish_output'), 'failed', { title: 'x', files: [] }, cwd), error => JSON.parse(error.message).detail.settings_url === detail.settings_url)
  await writeFile(join(cwd, 'a.txt'), 'hello')
  const controller = new AbortController(); controller.abort()
  await assert.rejects(execute(tools(() => assert.fail('must not publish')).get('publish_output'), 'aborted', { title: 'x', files: [{ path: 'a.txt' }] }, cwd, controller.signal), /abort/i)
}))
