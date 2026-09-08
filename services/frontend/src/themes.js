/** Theme schema and pure helpers. No CSS, storage, Vue or browser dependencies. */
export const THEMES = [
  {
    id: 'orbit', name: 'Orbit', mode: 'dark', description: 'Graphite & mint',
    colors: {
      'surface-app': '#101517', 'surface-rail': '#0c1113', 'surface-panel': '#151c1f',
      'surface-raised': '#1b2529', 'surface-overlay': '#202b30', 'surface-input': '#192226', 'surface-code': '#0d1214',
      text: '#e5edee', 'text-muted': '#a3b3b8', 'text-dim': '#84989f', accent: '#79dfc5',
      success: '#81d6a3', warning: '#edc17c', danger: '#ff939c',
      'syntax-comment': '#899da4', 'syntax-keyword': '#c4a7e7', 'syntax-string': '#a3d9ac',
      'syntax-number': '#edbd8d', 'syntax-function': '#8acde8', 'syntax-variable': '#dae5e9',
      'syntax-builtin': '#edd39a', 'syntax-meta': '#eaa5b5', 'flow-activity': '#7ed6cd', 'flow-signal': '#d9ace4'
    }
  },
  {
    id: 'nebula', name: 'Nebula', mode: 'dark', description: 'Midnight & iris',
    colors: {
      'surface-app': '#14121d', 'surface-rail': '#100e18', 'surface-panel': '#1b1827',
      'surface-raised': '#252034', 'surface-overlay': '#2d263e', 'surface-input': '#231f31', 'surface-code': '#110f19',
      text: '#efebf7', 'text-muted': '#b9afcc', 'text-dim': '#9e93b4', accent: '#bea1fa',
      success: '#8cd8ba', warning: '#edc588', danger: '#f79dad',
      'syntax-comment': '#9c91b2', 'syntax-keyword': '#d4a4f8', 'syntax-string': '#9adbc0',
      'syntax-number': '#f1bb93', 'syntax-function': '#9cbff5', 'syntax-variable': '#e0d8ed',
      'syntax-builtin': '#e9d28f', 'syntax-meta': '#ef9fcc', 'flow-activity': '#9cdbd1', 'flow-signal': '#e4a4d9'
    }
  },
  {
    id: 'daylight', name: 'Daylight', mode: 'light', description: 'Porcelain & cobalt',
    colors: {
      'surface-app': '#f8fafc', 'surface-rail': '#eef2f7', 'surface-panel': '#ffffff',
      'surface-raised': '#f1f5f9', 'surface-overlay': '#ffffff', 'surface-input': '#f3f6fa', 'surface-code': '#eef3f8',
      text: '#1e2b3d', 'text-muted': '#52647a', 'text-dim': '#5e6f84', accent: '#255acb',
      success: '#23794c', warning: '#8a5b0b', danger: '#bc344c',
      'syntax-comment': '#5e6f84', 'syntax-keyword': '#7750a5', 'syntax-string': '#256d47',
      'syntax-number': '#955523', 'syntax-function': '#255da5', 'syntax-variable': '#2d3e53',
      'syntax-builtin': '#7c6108', 'syntax-meta': '#a13660', 'flow-activity': '#21786d', 'flow-signal': '#8b4594'
    }
  },
  {
    id: 'sand', name: 'Sand', mode: 'light', description: 'Limestone & copper',
    colors: {
      'surface-app': '#f7f4ee', 'surface-rail': '#eee9df', 'surface-panel': '#fffcf7',
      'surface-raised': '#eee8dc', 'surface-overlay': '#fffcf7', 'surface-input': '#f1ece3', 'surface-code': '#efe9df',
      text: '#34312b', 'text-muted': '#6d6558', 'text-dim': '#706759', accent: '#9d492e',
      success: '#4e733e', warning: '#855e10', danger: '#b23241',
      'syntax-comment': '#706759', 'syntax-keyword': '#775181', 'syntax-string': '#4d6f3d',
      'syntax-number': '#925322', 'syntax-function': '#396981', 'syntax-variable': '#494135',
      'syntax-builtin': '#79600e', 'syntax-meta': '#a03f52', 'flow-activity': '#357667', 'flow-signal': '#885775'
    }
  }
]

const colors = (group, entries) => entries.map(([key, label]) => ({ key, label, group, type: 'color' }))
const size = (key, label, group, value, min, max, step = 1) => ({ key, label, group, type: 'number', value, min, max, step, unit: 'px' })

/** Adding a token here makes it editable, searchable, exportable and validated. */
export const THEME_TOKENS = [
  ...colors('Surfaces', [
    ['surface-app', 'Canvas'], ['surface-rail', 'Navigation'], ['surface-panel', 'Panels'],
    ['surface-raised', 'Raised surfaces'], ['surface-overlay', 'Menus & dialogs'], ['surface-input', 'Inputs'],
    ['surface-code', 'Code background'], ['surface-hover', 'Hover background'], ['surface-active', 'Selected background'],
    ['border', 'Subtle borders'], ['border-strong', 'Strong borders']
  ]),
  ...colors('Text & accent', [
    ['text', 'Primary text'], ['text-muted', 'Secondary text'], ['text-dim', 'Muted text'],
    ['accent', 'Accent'], ['accent-hover', 'Accent hover & links'], ['accent-press', 'Accent pressed'],
    ['accent-soft', 'Accent tint'], ['accent-text', 'Text on accent']
  ]),
  ...colors('Messages & status', [
    ['bubble-out', 'Your message background'], ['bubble-text', 'Your message text'],
    ['success', 'Success'], ['success-soft', 'Success tint'], ['warning', 'Warning'], ['warning-soft', 'Warning tint'],
    ['warning-text', 'Text on warning'], ['danger', 'Error'], ['danger-soft', 'Error tint']
  ]),
  ...colors('Code & workflows', [
    ['syntax-comment', 'Comments'], ['syntax-keyword', 'Keywords'], ['syntax-string', 'Strings'],
    ['syntax-number', 'Numbers'], ['syntax-function', 'Functions'], ['syntax-variable', 'Variables'],
    ['syntax-builtin', 'Built-ins'], ['syntax-meta', 'Metadata'],
    ['flow-activity', 'Activity nodes'], ['flow-signal', 'Signal nodes']
  ]),
  { key: 'font', label: 'Interface font', group: 'Typography', type: 'font', value: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" },
  { key: 'font-mono', label: 'Code font', group: 'Typography', type: 'font', value: "ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, monospace" },
  size('chat-font-size', 'Message text size', 'Typography', 15, 12, 22),
  size('code-font-size', 'Code text size', 'Typography', 12.5, 10, 20, 0.5),
  size('radius-xs', 'Small corners', 'Shape & layout', 4, 0, 16),
  size('radius-sm', 'Control corners', 'Shape & layout', 8, 0, 24),
  size('radius-md', 'Card corners', 'Shape & layout', 12, 0, 32),
  size('radius-lg', 'Composer & dialog corners', 'Shape & layout', 18, 0, 36),
  size('radius-xl', 'Large corners', 'Shape & layout', 24, 0, 40),
  size('content-width', 'Conversation width', 'Shape & layout', 880, 560, 1200, 20),
  size('rail-width', 'Navigation width', 'Shape & layout', 72, 64, 96),
  size('header-height', 'Header height', 'Shape & layout', 64, 52, 80),
  size('space-1', 'Spacing · XS', 'Spacing', 4, 2, 8),
  size('space-2', 'Spacing · S', 'Spacing', 8, 4, 12),
  size('space-3', 'Spacing · M', 'Spacing', 12, 8, 20),
  size('space-4', 'Spacing · L', 'Spacing', 16, 12, 28),
  size('space-5', 'Spacing · XL', 'Spacing', 24, 16, 36),
  size('space-6', 'Spacing · XXL', 'Spacing', 32, 24, 48)
]
export const TOKEN_GROUPS = [...new Set(THEME_TOKENS.map(token => token.group))]
export const themeById = id => THEMES.find(theme => theme.id === id) || THEMES[0]
const tokenByKey = new Map(THEME_TOKENS.map(token => [token.key, token]))
const HEX = /^#[\da-f]{6}([\da-f]{2})?$/i

export function validToken (key, value) {
  const token = tokenByKey.get(key)
  if (!token) return false
  if (token.type === 'color') return typeof value === 'string' && HEX.test(value)
  if (token.type === 'font') return typeof value === 'string' && value.trim().length > 0 && value.length <= 200 && /^[\w\s,'"-]+$/.test(value)
  return typeof value === 'number' && Number.isFinite(value) && value >= token.min && value <= token.max
}

export function sanitizeOverrides (input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return {}
  return Object.fromEntries(Object.entries(input).filter(([key, value]) => validToken(key, value)))
}

const rgb = color => [1, 3, 5].map(start => parseInt(color.slice(start, start + 2), 16))
const hex = channels => `#${channels.map(value => Math.round(value).toString(16).padStart(2, '0')).join('')}`
const mix = (a, b, share) => hex(rgb(a).map((value, index) => value * (1 - share) + rgb(b)[index] * share))
const alpha = (color, opacity) => `${color.slice(0, 7)}${Math.round(opacity * 255).toString(16).padStart(2, '0')}`
const composite = (color, background) => {
  const opacity = color.length === 9 ? parseInt(color.slice(7), 16) / 255 : 1
  return rgb(color).map((value, index) => value * opacity + background[index] * (1 - opacity))
}
const luminance = channels => channels.map(value => {
  const n = value / 255
  return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4
}).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0)

/** Composite transparent foreground/background colors before measuring contrast. */
export function contrastRatio (foreground, background, canvas = '#ffffff') {
  const back = composite(background, composite(canvas, [255, 255, 255]))
  const front = composite(foreground, back)
  const [low, high] = [luminance(front), luminance(back)].sort((x, y) => x - y)
  return (high + 0.05) / (low + 0.05)
}
const onColor = (color, canvas) => contrastRatio('#101517', color, canvas) > contrastRatio('#ffffff', color, canvas) ? '#101517' : '#ffffff'

/** Dependent colors follow custom accents/surfaces unless explicitly overridden. */
export function resolveTheme (id, input = {}) {
  const theme = themeById(id)
  const overrides = sanitizeOverrides(input)
  const base = { ...theme.colors, ...overrides }
  const dark = theme.mode === 'dark'
  return {
    ...Object.fromEntries(THEME_TOKENS.filter(token => token.value !== undefined).map(token => [token.key, token.value])),
    ...theme.colors,
    'surface-hover': alpha(base.text, 0.045), 'surface-active': alpha(base.text, 0.08),
    border: alpha(base.text, dark ? 0.09 : 0.12), 'border-strong': alpha(base.text, dark ? 0.17 : 0.22),
    'accent-hover': mix(base.accent, base.text, 0.18), 'accent-press': mix(base.accent, '#101517', 0.12),
    'accent-soft': alpha(base.accent, dark ? 0.12 : 0.09), 'accent-text': onColor(base.accent, base['surface-app']),
    'success-soft': alpha(base.success, 0.12), 'warning-soft': alpha(base.warning, 0.12),
    'warning-text': onColor(base.warning, base['surface-app']), 'danger-soft': alpha(base.danger, 0.12),
    'bubble-out': mix(base.accent, base['surface-app'], dark ? 0.85 : 0.92), 'bubble-text': base.text,
    ...overrides
  }
}

export function cssTokens (id, overrides) {
  return Object.fromEntries(Object.entries(resolveTheme(id, overrides)).map(([key, value]) =>
    [`--${key}`, `${value}${tokenByKey.get(key)?.unit || ''}`]))
}

export function exportTheme (theme, overrides) {
  return JSON.stringify({ version: 1, theme: themeById(theme).id, overrides: sanitizeOverrides(overrides) }, null, 2)
}

export function importTheme (text) {
  if (typeof text !== 'string') throw new Error('Import a JSON theme file. Nothing was changed.')
  if (text.length > 64000) throw new Error('Theme files must be smaller than 64 KB.')
  let data
  try { data = JSON.parse(text) } catch { throw new Error('This is not valid JSON. Nothing was changed.') }
  if (!data || data.version !== 1 || !THEMES.some(theme => theme.id === data.theme) ||
      !data.overrides || typeof data.overrides !== 'object' || Array.isArray(data.overrides)) {
    throw new Error('Use a Nautionette theme export (version 1). Nothing was changed.')
  }
  for (const [key, value] of Object.entries(data.overrides)) {
    if (!validToken(key, value)) throw new Error(`Invalid theme token: ${key}. Nothing was changed.`)
  }
  return { theme: data.theme, overrides: sanitizeOverrides(data.overrides) }
}
