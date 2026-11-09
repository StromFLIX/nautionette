import { test } from 'node:test'
import assert from 'node:assert/strict'
import { avatarStyle, initials } from './format.js'

test('agent and session initials preserve graphemes and use the first and last name', () => {
  assert.equal(initials('Jo'), 'JO')
  assert.equal(initials('Jane Mary Doe'), 'JD')
  assert.equal(initials('👩‍💻 Ada'), '👩‍💻A')
  assert.equal(initials('张三'), '张三')
  assert.equal(initials(null), '?')
  assert.equal(initials('   '), '?')
})

test('generated identity colors stay in the shared brand roles', () => {
  for (const id of ['research', 'coding', 'writing', 'personal', 'a', 'b', 'c']) {
    assert.match(avatarStyle(id).color, /^var\(--(agent|session|workflow)-color\)$/)
  }
})
