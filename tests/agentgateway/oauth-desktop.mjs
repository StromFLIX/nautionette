// Production desktop receiver/controller, with only browser UI and backend
// transport replaced by a synthetic authority and a parent-process RPC channel.
import { createRequire } from 'node:module'
import readline from 'node:readline'
import { oauthConnection } from '../../services/frontend/src/mcp-oauth.js'
const require = createRequire(import.meta.url)
const { createReceivers } = require('../../services/frontend/desktop/oauth.cjs')
const input = readline.createInterface({ input: process.stdin })
const pending = new Map()
let sequence = 0
function rpc (operation, args) {
  const id = ++sequence
  process.stdout.write(JSON.stringify({ kind: 'request', id, operation, args }) + '\n')
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
}
const backend = Object.fromEntries(['startMcpOAuth', 'pollMcpOAuth', 'forwardMcpOAuth', 'cancelMcpOAuth'].map(operation =>
  [operation, (...args) => rpc(operation, args)]))
const stages = []
const receiver = createReceivers({ openExternal: async value => {
  const url = new URL(value)
  const redirect = url.searchParams.get('redirect_uri')
  const fields = { state: url.searchParams.get('state'), code: url.searchParams.get('code_challenge'), session_state: 'synthetic-session' }
  const mode = url.searchParams.get('response_mode') || 'query'
  const response = await fetch(mode === 'query' ? `${redirect}?${new URLSearchParams(fields)}` : redirect, mode === 'query' ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', Origin: url.origin },
    body: new URLSearchParams(fields).toString()
  })
  if (!response.ok) throw new Error('Synthetic browser callback refused')
} })
const bridge = { version: 1, prepare: redirect => receiver.prepare(1, redirect),
  authorize: payload => receiver.authorize(1, payload), cancel: async () => receiver.cancel(1) }
let begun = false
input.on('line', async line => {
  const message = JSON.parse(line)
  if (message.kind === 'begin' && !begun) {
    begun = true
    const controller = oauthConnection({ bridge, backend, interval: 25, context: () => 'synthetic-instance' })
    try {
      await controller.connect('oauth-test', message.config, stage => stages.push(stage))
      process.stdout.write(JSON.stringify({ kind: 'done', ok: true, stages }) + '\n')
    } catch (error) {
      process.stdout.write(JSON.stringify({ kind: 'done', ok: false, error: error.message, stages }) + '\n')
    } finally { receiver.close(); input.close() }
  } else {
    const request = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) request.reject(new Error(message.error))
    else request.resolve(message.result)
  }
})
