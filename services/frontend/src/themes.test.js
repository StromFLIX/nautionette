import { test } from 'node:test'
import assert from 'node:assert/strict'
import { THEMES, THEME_TOKENS, contrastRatio, cssTokens, exportTheme, importTheme, resolveTheme, sanitizeOverrides, validToken } from './themes.js'

for (const theme of THEMES) {
  test(`${theme.name} resolves every token, with readable text, controls and syntax`, () => {
    const resolved = resolveTheme(theme.id)
    const css = cssTokens(theme.id)
    assert.equal(Object.keys(resolved).length, THEME_TOKENS.length)
    for (const token of THEME_TOKENS) {
      assert.ok(validToken(token.key, resolved[token.key]), `${theme.id}: ${token.key}`)
      assert.equal(css[`--${token.key}`], `${resolved[token.key]}${token.unit || ''}`)
    }
    for (const foreground of ['text', 'text-muted', 'text-dim']) {
      for (const background of ['surface-app', 'surface-panel', 'surface-rail', 'surface-input']) {
        assert.ok(contrastRatio(resolved[foreground], resolved[background]) >= 4.5, `${foreground} on ${background}`)
      }
    }
    for (const token of THEME_TOKENS.filter(token => token.key.startsWith('syntax-'))) {
      assert.ok(contrastRatio(resolved[token.key], resolved['surface-code']) >= 4.5, token.key)
    }
    assert.ok(contrastRatio(resolved['accent-text'], resolved.accent) >= 4.5)
    assert.ok(contrastRatio(resolved['bubble-text'], resolved['bubble-out']) >= 4.5)
  })
}

test('custom accents and surfaces derive related tokens without overwriting explicit choices', () => {
  const base = resolveTheme('orbit')
  const input = { accent: '#123456', text: '#F0F0F0', 'surface-app': '#212121', 'accent-hover': '#789ABC', 'chat-font-size': 18 }
  const custom = resolveTheme('orbit', input)
  assert.equal(custom.accent, input.accent)
  assert.equal(custom['accent-hover'], '#789ABC')
  assert.equal(custom['bubble-text'], '#F0F0F0')
  assert.equal(custom['accent-text'], '#ffffff')
  assert.notEqual(custom['accent-soft'], base['accent-soft'])
  assert.notEqual(custom['bubble-out'], base['bubble-out'])
  assert.notEqual(custom.border, base.border)
  assert.equal(cssTokens('orbit', input)['--chat-font-size'], '18px')
  assert.deepEqual(resolveTheme('not-a-theme'), base)
  assert.equal(THEMES[0].colors.accent, '#79dfc5')
})

test('token validation permits bounded values and local fonts, never CSS or remote content', () => {
  for (const color of ['#AaBbCc', '#12345678']) assert.ok(validToken('accent', color))
  for (const color of ['#abc', 'red', '#000000;', 'url(https://example.test/x)', '#12345g', null]) assert.equal(validToken('accent', color), false)
  assert.ok(validToken('font', "'Helvetica Neue', ui-sans-serif, sans-serif"))
  for (const font of ['', ' ', 'url(https://example.test/font)', 'serif; display:none', 'var(--secret)', 'a'.repeat(201)]) assert.equal(validToken('font', font), false)
  for (const size of [NaN, Infinity, 500, '16', -1]) assert.equal(validToken('chat-font-size', size), false)
  assert.equal(validToken('__proto__', '#abcdef'), false)
  assert.deepEqual(sanitizeOverrides({ accent: '#abcdef', 'font-size': 100, font: 'url(x)', 'chat-font-size': 900 }), { accent: '#abcdef' })
  assert.deepEqual(sanitizeOverrides([]), {})
  assert.deepEqual(sanitizeOverrides(null), {})
})

test('contrast accounts for alpha instead of reporting transparent text as readable', () => {
  assert.equal(contrastRatio('#000000', '#ffffff'), 21)
  assert.equal(contrastRatio('#ffffff', '#ffffff'), 1)
  assert.equal(contrastRatio('#00000000', '#ffffff'), 1)
  assert.equal(contrastRatio('#ffffff', '#00000000', '#ffffff'), 1)
  assert.ok(contrastRatio('#00000080', '#ffffff') < 4.5)
  assert.ok(contrastRatio('#ffffffff', '#000000ff') >= 21)
})

test('theme export is versioned, portable and contains no unrelated workspace or account data', () => {
  const text = exportTheme('sand', { accent: '#bc4512', 'code-font-size': 14.5, token: 'secret', sideWidth: 400 })
  assert.deepEqual(JSON.parse(text), { version: 1, theme: 'sand', overrides: { accent: '#bc4512', 'code-font-size': 14.5 } })
  assert.deepEqual(importTheme(text), { theme: 'sand', overrides: { accent: '#bc4512', 'code-font-size': 14.5 } })
  assert.equal(text.includes('secret'), false)
})

test('invalid imports fail as a whole, including unknown tokens, injection and oversized files', () => {
  for (const value of ['bad JSON', 'null', '[]', '{}', '{"version":2,"theme":"orbit","overrides":{}}', '{"version":1,"theme":"unknown","overrides":{}}']) {
    assert.throws(() => importTheme(value))
  }
  for (const overrides of [{ accent: '#abcdef', unknown: '#000000' }, { font: 'serif; opacity:0' }, { 'radius-md': 10000 }, []]) {
    assert.throws(() => importTheme(JSON.stringify({ version: 1, theme: 'orbit', overrides })))
  }
  assert.throws(() => importTheme('{"version":1,"theme":"orbit","overrides":{"__proto__":{"bad":true}}}'))
  assert.throws(() => importTheme(' '.repeat(64001)), /64 KB/)
  assert.throws(() => importTheme(null))
  assert.equal({}.bad, undefined)
})
