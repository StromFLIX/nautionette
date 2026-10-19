import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { createServer } from 'node:http'

export const digest = data => createHash('sha256').update(data).digest('hex')
const wire = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] })

/** Real HTTP binary fixture, deliberately separate from metadata-only MCP. */
export async function outputTransport ({ onPublish, transformDownload, beforePrepare, uploadStatus = 200 } = {}) {
  const records = [], calls = [], prepared = new Map(), uploads = new Map()
  const server = createServer(async (req, res) => {
    const route = new URL(req.url, 'http://fixture').pathname
    try {
      if (req.method === 'PUT' && route.startsWith('/uploads/')) {
        const [_root, _upload, uploadId, index] = route.split('/')
        const file = prepared.get(uploadId)?.files[Number(index)]
        assert.ok(file, 'upload metadata must be prepared before bytes arrive')
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const bytes = Buffer.concat(chunks)
        assert.equal(bytes.length, file.size)
        assert.equal(digest(bytes), file.sha256)
        if (uploadStatus !== 200) { res.writeHead(uploadStatus); res.end('Private provider body: do-not-expose'); return }
        uploads.set(route, bytes)
        res.writeHead(200); res.end(); return
      }
      if (req.method === 'GET' && route.startsWith('/files/')) {
        const [_root, _files, revision, index] = route.split('/')
        const bytes = Buffer.from(records[Number(revision) - 1].files[Number(index)].data, 'base64')
        res.writeHead(200); res.end(transformDownload ? transformDownload(bytes) : bytes); return
      }
      res.writeHead(404); res.end()
    } catch (error) { res.writeHead(400); res.end(error.message) }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  const rpc = async (method, params, signal) => {
    assert.equal(method, 'tools/call')
    assert.ok(signal instanceof AbortSignal)
    calls.push(params)
    const body = params.arguments.body
    if (params.name === 'backend_prepare_chat_output') {
      await beforePrepare?.(body)
      assert.ok(!JSON.stringify(body).includes('"data":'), 'MCP must not carry binary bytes')
      const uploadId = (prepared.size + 1).toString(16).padStart(32, '0')
      prepared.set(uploadId, body)
      return wire({ upload_id: uploadId, settings_url: '/settings/storage', files: body.files.map((file, index) => ({
        url: `${origin}/uploads/${uploadId}/${index}?private-upload-capability=do-not-expose`,
        headers: { 'content-type': 'application/octet-stream', 'content-length': String(file.size) }
      })) })
    }
    if (params.name === 'backend_publish_chat_output') {
      assert.deepEqual(Object.keys(body).sort(), ['grant', 'request_id', 'upload_id'])
      const metadata = prepared.get(body.upload_id)
      assert.ok(metadata)
      assert.equal(body.request_id, metadata.request_id)
      const files = metadata.files.map((file, index) => {
        const bytes = uploads.get(`/uploads/${body.upload_id}/${index}`)
        assert.ok(bytes, 'all binary uploads must finish before commit')
        return { ...file, data: bytes.toString('base64') }
      })
      const output = {
        id: metadata.output_id || 'a'.repeat(32), revision: (metadata.expected_revision || 0) + 1,
        title: metadata.title, source_app: metadata.source_app || '',
        files: files.map(({ data: _data, ...file }, index) => ({ id: String(index + 1).repeat(32), ...file }))
      }
      records.push({ ...metadata, files, output })
      await onPublish?.(records.at(-1))
      return wire(output)
    }
    assert.equal(params.name, 'backend_read_chat_output')
    assert.equal(body.binary, true)
    if (!body.output_id) return wire({ outputs: records.map(record => record.output) })
    const record = body.revision ? records[body.revision - 1] : records.at(-1)
    assert.ok(record)
    if (!body.file_id) return wire({ output: record.output })
    const index = record.output.files.findIndex(file => file.id === body.file_id)
    assert.ok(index >= 0)
    return wire({ output_id: record.output.id, revision: record.output.revision, file: {
      ...record.output.files[index], download_url: `${origin}/files/${record.output.revision}/${index}?private-download-capability=do-not-expose`
    } })
  }
  return { rpc, calls, records, uploads, close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections() }) }
}
