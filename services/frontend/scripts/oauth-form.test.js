import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'
import oauth from '../desktop/oauth.cjs'
const { createReceivers } = oauth
const state = 'a+/state=='.repeat(4)
function url (receiver) {
  return 'https://login.example.test/authorize?' + new URLSearchParams({ redirect_uri: receiver.redirect_uri,
    state, response_type: 'code', response_mode: 'form_post', code_challenge: 'x'.repeat(43), code_challenge_method: 'S256' })
}
function post (receiver, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const request = http.request({ hostname: '127.0.0.1', port: receiver.port, path: '/callback', method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': Buffer.byteLength(body),
        Origin: 'https://login.example.test', ...headers } }, response => {
      let text = ''
      response.on('data', data => { text += data })
      response.on('end', () => resolve({ status: response.statusCode, text, headers: response.headers }))
    })
    request.on('error', reject)
    request.end(body)
  })
}

test('form_post preserves method and all fields, decodes state, rejects forgery/duplicates/tokens and closes', async t => {
  const bridge = createReceivers({ openExternal: () => {} })
  t.after(() => bridge.close())
  const receiver = await bridge.prepare(1, 'http://localhost:0/callback')
  const waiting = bridge.authorize(1, { id: receiver.id, authorization_url: url(receiver), state, response_mode: 'form_post' })
  assert.equal((await post(receiver, new URLSearchParams({ state: 'wrong', code: 'wrong' }).toString())).status, 400)
  assert.equal((await post(receiver, new URLSearchParams({ state, code: 'wrong', access_token: 'must-not-pass' }).toString())).status, 400)
  const duplicate = new URLSearchParams({ state, code: 'code' }); duplicate.append('state', state)
  assert.equal((await post(receiver, duplicate.toString())).status, 400)
  assert.equal((await post(receiver, new URLSearchParams({ state, code: 'wrong' }).toString(), { 'Content-Type': 'application/json' })).status, 400)
  const fields = { state, code: 'private-code', session_state: 'session', client_info: 'info' }
  const response = await post(receiver, new URLSearchParams(fields).toString())
  assert.equal(response.status, 200)
  assert.equal(response.headers['cache-control'], 'no-store')
  assert.ok(!response.text.includes('private-code'))
  assert.deepEqual(await waiting, { method: 'POST', fields })
  await assert.rejects(post(receiver, new URLSearchParams(fields).toString()))
})

test('a conflicting IPv6 localhost port prevents browser launch even when IPv4 is free', async t => {
  const occupied = http.createServer()
  occupied.listen({ host: '::1', port: 0, ipv6Only: true })
  try { await once(occupied, 'listening') } catch { t.skip('IPv6 unavailable'); return }
  t.after(() => occupied.close())
  let opened = false
  const bridge = createReceivers({ openExternal: () => { opened = true } })
  t.after(() => bridge.close())
  await assert.rejects(bridge.prepare(1, `http://localhost:${occupied.address().port}/callback`), /Cannot open OAuth callback port/)
  assert.equal(opened, false)
})
