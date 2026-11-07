import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'
import oauth from '../desktop/oauth.cjs'

const { createReceivers, redirectURL } = oauth
const state = 's'.repeat(43)
function authorization (redirect) {
  return 'https://login.example.test/authorize?' + new URLSearchParams({ state, redirect_uri: redirect,
    response_type: 'code', code_challenge: 'x'.repeat(43), code_challenge_method: 'S256' })
}
function callback (receiver, query, options = {}) {
  return new Promise((resolve, reject) => {
    const request = http.get({ hostname: '127.0.0.1', port: receiver.port,
      path: `/callback?${new URLSearchParams(query)}`, ...options }, response => {
      let body = ''
      response.on('data', data => { body += data })
      response.on('end', () => resolve({ status: response.statusCode, body }))
    })
    request.on('error', reject)
  })
}

test('only unprivileged loopback redirects are accepted', () => {
  for (const value of ['https://localhost:12798', 'http://evil.test:12798', 'http://0.0.0.0:12798',
    'http://localhost:80', 'http://localhost:0', 'http://user@localhost:12798', 'http://localhost:12798/?x=1']) {
    assert.throws(() => redirectURL(value))
  }
  assert.equal(redirectURL('http://127.0.0.1:0/callback', true).port, '0')
})

test('real receiver binds before login, rejects wrong state/host/path/duplicates and returns code once', async t => {
  let opened = ''
  const bridge = createReceivers({ openExternal: url => { opened = url } })
  t.after(() => bridge.close())
  const receiver = await bridge.prepare(1, 'http://127.0.0.1:0/callback')
  assert.ok(receiver.port > 1023)
  assert.equal((await callback(receiver, { state, code: 'before-open' })).status, 400)
  const pending = bridge.authorize(1, { id: receiver.id, authorization_url: authorization(receiver.redirect_uri), state })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(opened, authorization(receiver.redirect_uri))
  assert.equal((await callback(receiver, { state: 'wrong', code: 'secret' })).status, 400)
  assert.equal((await callback(receiver, { state, code: 'secret' }, { headers: { Host: 'evil.test' } })).status, 400)
  assert.equal((await callback(receiver, { state, code: 'secret' }, { path: '/wrong' })).status, 400)
  assert.equal((await callback(receiver, { state, code: 'secret' }, { path: '/callback?state=' + state + '&state=' + state + '&code=secret' })).status, 400)
  const response = await callback(receiver, { state, code: 'secret-code' })
  assert.equal(response.status, 200)
  assert.ok(!response.body.includes('secret-code'))
  assert.deepEqual(await pending, { method: 'GET', fields: { state, code: 'secret-code' } })
  await assert.rejects(callback(receiver, { state, code: 'replay' }))
})

test('a fixed port conflict fails before opening the browser', async t => {
  const occupied = http.createServer()
  occupied.listen(0, '127.0.0.1')
  await once(occupied, 'listening')
  t.after(() => occupied.close())
  let opened = false
  const bridge = createReceivers({ openExternal: () => { opened = true } })
  t.after(() => bridge.close())
  await assert.rejects(bridge.prepare(1, `http://localhost:${occupied.address().port}`), /Cannot open OAuth callback port/)
  assert.equal(opened, false)
})

test('provider errors, cancellation, timeout and owner isolation release listeners', async t => {
  const bridge = createReceivers({ openExternal: () => {}, timeout: 100 })
  t.after(() => bridge.close())
  let receiver = await bridge.prepare(1, 'http://127.0.0.1:0/callback')
  await assert.rejects(bridge.authorize(2, { id: receiver.id }), /No matching/)
  let pending = bridge.authorize(1, { id: receiver.id, authorization_url: authorization(receiver.redirect_uri), state })
  assert.equal((await callback(receiver, { state, error: 'access_denied' })).status, 200)
  assert.deepEqual(await pending, { method: 'GET', fields: { state, error: 'access_denied' } })
  receiver = await bridge.prepare(1, 'http://127.0.0.1:0/callback')
  pending = bridge.authorize(1, { id: receiver.id, authorization_url: authorization(receiver.redirect_uri), state })
  const rejected = assert.rejects(pending, /cancelled or timed out/)
  bridge.cancel(1)
  await rejected
  receiver = await bridge.prepare(1, 'http://127.0.0.1:0/callback')
  pending = bridge.authorize(1, { id: receiver.id, authorization_url: authorization(receiver.redirect_uri), state })
  await assert.rejects(pending, /cancelled or timed out/)
})

test('authorization URL cannot redirect to another receiver or weaken PKCE', async t => {
  const bridge = createReceivers({ openExternal: () => {} })
  t.after(() => bridge.close())
  const receiver = await bridge.prepare(1, 'http://127.0.0.1:0/callback')
  for (const url of [authorization('http://localhost:9999'), authorization(receiver.redirect_uri).replace('S256', 'plain'),
    authorization(receiver.redirect_uri).replace('https:', 'http:')]) {
    await assert.rejects(bridge.authorize(1, { id: receiver.id, authorization_url: url, state }), /Invalid OAuth/)
  }
})
