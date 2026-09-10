// Minimal offline MCP fixture. A leaked gateway environment or split argument
// makes initialize fail, so the runtime test proves more than process spawning.
import { appendFileSync } from 'node:fs'
import { createInterface } from 'node:readline'

if (process.env.NAUTIONETTE_GATEWAY_ONLY || process.env.TEST_VALUE !== '$literal' ||
    process.argv[2] !== 'two words' || process.argv[3] !== '${LITERAL_ARG}') {
  process.exit(1)
}
appendFileSync(process.env.PID_FILE, `${process.pid}\n`)
createInterface({ input: process.stdin }).on('line', line => {
  const message = JSON.parse(line)
  if (message.id === undefined) return
  let result = {}
  if (message.method === 'initialize') {
    result = { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'fixture', version: '1' } }
  } else if (message.method === 'tools/list') {
    result = { tools: [{ name: 'echo', description: 'Offline test tool', inputSchema: { type: 'object', properties: {} } }] }
  }
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, result }) + '\n')
})
