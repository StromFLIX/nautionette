import assert from 'node:assert/strict'
import { test } from 'node:test'
import { contextMeter, latestContext, modelContextWindow } from './context.js'

const context = { model: 'test/model', tokens: 4600, source: 'provider' }
const messages = [{ role: 'assistant', content: 'x', meta: { context } }]

test('model windows are token limits, unaffected by character budgets or history overrides', () => {
  const catalog = { default_model: 'test/model', models: [{ id: 'test/model', context_length: 10000 }], context: { override: 5 } }
  assert.equal(modelContextWindow(catalog, ''), 10000)
  assert.equal(modelContextWindow(catalog, 'unknown'), null)
  assert.equal(modelContextWindow({ models: [{ id: 'bad', context_length: 0 }] }, 'bad'), null)
})

test('live usage replaces previous usage, including after compaction', () => {
  assert.equal(latestContext(messages, null, context.model), context)
  const smaller = { ...context, tokens: 1000 }
  assert.equal(latestContext(messages, { context: smaller }, context.model), smaller)
  assert.equal(latestContext(messages, { context: null }, context.model), null)
  assert.equal(latestContext(messages, null, 'another/model'), null)
  assert.equal(latestContext([...messages, { role: 'assistant', meta: {} }], null, context.model), null)
  assert.equal(latestContext([], null, context.model), null)
})

test('meter shows actual token percentage and explains what was measured', () => {
  const meter = contextMeter(context, 10000)
  assert.equal(meter.label, '46% context')
  assert.equal(meter.width, 46)
  assert.match(meter.title, /4,600 of 10,000 tokens/)
  assert.match(meter.title, /last model response/)
  assert.match(meter.title, /excludes unsent text/)
  const overflow = contextMeter({ tokens: 11000 }, 10000)
  assert.equal(overflow.label, '110% context')
  assert.equal(overflow.width, 100)
})

test('unknown usage or window never becomes a fake zero percent', () => {
  assert.equal(contextMeter(null, 10000).label, 'Context unknown')
  assert.equal(contextMeter(null, null).width, 0)
  assert.equal(contextMeter(context, null).label, '4,600 tokens')
  assert.match(contextMeter(context, null).title, /window is unknown/)
})
