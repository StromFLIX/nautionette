import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { prepareFiles, fileReferences } from '../../images/pi-base/chat-files.mjs'

const original = Buffer.from([0, 255, 1, 2, 3])
const file = (overrides = {}) => ({ id: 'a'.repeat(32), name: 'original.pdf', mime_type: 'application/pdf', data: original.toString('base64'), ...overrides })

function workspace (fn) {
  const directory = mkdtempSync(join(tmpdir(), 'chat-files-'))
  try { fn(directory) } finally { rmSync(directory, { recursive: true, force: true }) }
}

test('raw bytes and safe extension survive; names never choose a workspace path', () => workspace(directory => {
  const job = { files: [file({ name: '../../AGENTS.md' })], history: [
    { role: 'user', files: [file({ id: 'b'.repeat(32), name: 'original.csv', mime_type: 'text/csv' })] }
  ] }
  prepareFiles(job, directory)
  assert.deepEqual(readdirSync(directory), ['attachments'])
  for (const attachment of [job.files[0], job.history[0].files[0]]) {
    assert.ok(readFileSync(attachment.path).equals(original))
    assert.equal(attachment.data, undefined)
    assert.equal(statSync(attachment.path).mode & 0o777, 0o600)
    assert.ok(attachment.path.startsWith(`${directory}/attachments/`))
  }
  assert.match(fileReferences(job.files), /available unchanged/)
  assert.equal(fileReferences(), '')
  // Never overwrite an existing file or follow its symlink.
  assert.throws(() => prepareFiles({ files: [file({ name: 'AGENTS.md' })] }, directory), /EEXIST/)
}))

for (const overrides of [
  { id: '../AGENTS' }, { id: '/tmp/file' }, { id: 'x'.repeat(32) },
  { name: 'x'.repeat(201) }, { data: '%%%' }, { data: '' },
  { data: '====' }, { data: 'YQ==AAAA' }, { data: 'YR==' },
  { data: Buffer.alloc(5 * 1024 * 1024 + 1).toString('base64') }
]) {
  test(`reject invalid file: ${JSON.stringify(overrides).slice(0, 70)}`, () => workspace(directory => {
    assert.throws(() => prepareFiles({ files: [file(overrides)] }, directory), /Invalid attached file/)
  }))
}

test('enforce combined history/current limit and unique IDs', () => workspace(directory => {
  assert.throws(() => prepareFiles({ files: [file(), file()] }, directory), /Invalid attached file/)
  assert.throws(() => prepareFiles({ history: [{ files: Array(9).fill(file()) }] }, directory), /Too many/)
}))

test('Unicode filenames use the same character limit as the backend', () => workspace(directory => {
  const name = '📄'.repeat(196) + '.pdf'
  const job = { files: [file({ name })] }
  prepareFiles(job, directory)
  assert.equal(job.files[0].name, name)
  assert.ok(job.files[0].path.endsWith('.pdf'))
}))

test('maximum-size file does not overflow validation or change its bytes', () => workspace(directory => {
  const data = Buffer.alloc(5 * 1024 * 1024, 255)
  const job = { files: [file({ data: data.toString('base64') })] }
  prepareFiles(job, directory)
  assert.ok(readFileSync(job.files[0].path).equals(data))
}))
