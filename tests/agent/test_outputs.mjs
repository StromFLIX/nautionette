import assert from 'node:assert/strict'
import { chmod, mkdtemp, readFile, rm, stat, symlink, truncate, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { loadOutputGrant, registerOutputTools, sandboxFile, isOutputTransportTool } from '../../images/agent-sets/default/extensions/nautionette/outputs.ts'

const grant = { grant: 'test-capability-with-at-least-32-characters' }
const outputId = 'a'.repeat(32)
const fileId = 'b'.repeat(32)
const wire = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] })
async function workspace (fn) {
  const directory = await mkdtemp(join(tmpdir(), 'chat-outputs-'))
  try { await fn(directory) } finally { await rm(directory, { force: true, recursive: true }) }
}
function tools (rpc, capability = grant) {
  const registered = new Map()
  registerOutputTools({ registerTool: tool => registered.set(tool.name, tool) }, rpc, capability)
  return registered
}

const execute = (tool, id, args, cwd, signal) => tool.execute(id, args, signal, undefined, { cwd })

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
  assert.ok(isOutputTransportTool('backend_publish_chat_output'))
  assert.ok(isOutputTransportTool('backend_read_chat_output'))
  assert.ok(!isOutputTransportTool('backend_system_status'))
}))

test('an undeletable capability file fails closed before tools can be registered', { skip: process.getuid?.() === 0 }, () => workspace(async cwd => {
  const path = join(cwd, 'capability.json')
  await writeFile(path, JSON.stringify(grant), { mode: 0o600 })
  await chmod(cwd, 0o500)
  try { assert.throws(() => loadOutputGrant(path), /Cannot consume output capability/) }
  finally { await chmod(cwd, 0o700) }
}))

test('publish reads sandbox files and returns only committed metadata, not bytes or capabilities', () => workspace(async cwd => {
  const bytes = Buffer.from([0, 255, 1, 20])
  await writeFile(join(cwd, 'bracket.dxf'), bytes)
  const calls = []
  const metadata = { id: outputId, revision: 1, files: [{ id: fileId, name: 'bracket.dxf' }] }
  const registered = tools(async (method, params, signal) => {
    assert.equal(method, 'tools/call')
    assert.ok(signal instanceof AbortSignal)
    calls.push(params)
    return wire(metadata)
  })
  const result = await execute(registered.get('publish_output'), 'tool-call-1', {
    title: 'Bracket', files: [{ path: 'bracket.dxf', role: 'source' }]
  }, cwd)
  assert.equal(calls[0].name, 'backend_publish_chat_output')
  const body = calls[0].arguments.body
  assert.equal(body.grant, grant.grant)
  assert.equal(body.request_id, 'tool-call-1')
  assert.deepEqual(body.files, [{ name: 'bracket.dxf', mime_type: 'image/vnd.dxf', role: 'source', data: bytes.toString('base64') }])
  assert.deepEqual(result.details.output, metadata)
  assert.ok(!JSON.stringify(result).includes(grant.grant))
  assert.ok(!JSON.stringify(result).includes(bytes.toString('base64')))
  assert.ok(!JSON.stringify(result).includes(cwd))
}))

test('rejects secret paths, symlinks escaping the sandbox, empty files and non-files', () => workspace(async cwd => {
  await assert.rejects(sandboxFile({ path: '/proc/self/environ' }, cwd), /workspace/)
  await assert.rejects(sandboxFile({ path: '/etc/passwd' }, cwd), /workspace/)
  await symlink('/etc/passwd', join(cwd, 'escaped.txt'))
  await assert.rejects(sandboxFile({ path: 'escaped.txt' }, cwd), /workspace/)
  await assert.rejects(sandboxFile({ path: cwd }, cwd), /workspace/)
  await writeFile(join(cwd, 'empty'), '')
  await assert.rejects(sandboxFile({ path: 'empty' }, cwd), /nonempty/)
  await truncate(join(cwd, 'empty'), 20 * 1024 * 1024 + 1)
  await assert.rejects(sandboxFile({ path: 'empty' }, cwd), /20 MiB/)
}))

test('read_output restores exact bytes into a new private file without exposing base64', () => workspace(async cwd => {
  const bytes = Buffer.from('editable-source-document')
  const registered = tools(async (_method, params) => {
    assert.equal(params.name, 'backend_read_chat_output')
    assert.equal(params.arguments.body.grant, grant.grant)
    return wire({ output_id: outputId, revision: 2, file: {
      id: fileId, name: '../../source.dxf', mime_type: 'image/vnd.dxf', size: bytes.length, data: bytes.toString('base64')
    } })
  })
  const result = await execute(registered.get('read_output'), 'restore', { output_id: outputId, file_id: fileId }, cwd)
  const path = result.details.file.path
  assert.ok(path.startsWith(`${cwd}/output-`))
  assert.ok(path.endsWith('/document.dxf'))
  assert.deepEqual(await readFile(path), bytes)
  assert.equal((await stat(path)).mode & 0o777, 0o600)
  assert.equal(result.details.file.data, undefined)
  assert.ok(!JSON.stringify(result).includes(bytes.toString('base64')))
}))

test('read_output lists metadata, honors abort and propagates publication failures', () => workspace(async cwd => {
  const listing = { outputs: [{ id: outputId, revision: 1 }] }
  assert.deepEqual((await execute(tools(async () => wire(listing)).get('read_output'), 'list', {}, cwd)).details, listing)
  const failed = tools(async () => ({ ...wire({ detail: 'Storage quota reached' }), isError: true }))
  await assert.rejects(execute(failed.get('publish_output'), 'failed', { title: 'x', files: [] }, cwd), /Storage quota reached/)
  await writeFile(join(cwd, 'a.txt'), 'hello')
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(execute(tools(() => assert.fail('must not publish')).get('publish_output'), 'aborted', { title: 'x', files: [{ path: 'a.txt' }] }, cwd, controller.signal), /abort/i)
}))
