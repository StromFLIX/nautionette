/** Small formatting helpers shared by the list and the detail panes. */

const AVATAR_COLORS = ['accent', 'flow-activity', 'syntax-keyword', 'flow-signal', 'warning', 'success', 'syntax-function']

export function avatarStyle (seed = '') {
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0
  const color = `var(--${AVATAR_COLORS[hash % AVATAR_COLORS.length]})`
  return { background: `color-mix(in srgb, ${color} 13%, var(--surface-panel))`, color }
}

export function initials (text = '?') {
  const words = text.trim().split(/[\s_-]+/).filter(Boolean)
  if (!words.length) return '?'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

export function shortTime (seconds) {
  if (!seconds) return ''
  const date = new Date(seconds * 1000)
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  if (sameDay) return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const days = Math.round((now - date) / 86400000)
  if (days < 7) return date.toLocaleDateString([], { weekday: 'short' })
  return date.toLocaleDateString([], { day: '2-digit', month: 'short' })
}

export function fullTime (seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString() : ''
}

export function scheduleTime (value, timezone) {
  if (!value) return ''
  try {
    return new Intl.DateTimeFormat([], {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: timezone,
      timeZoneName: 'short'
    }).format(new Date(value))
  } catch {
    return new Date(value).toLocaleString()
  }
}

export function compactChars (chars) {
  if (!chars) return '0'
  if (chars >= 1_000_000) return `${+(chars / 1_000_000).toFixed(1)}M`
  return `${Math.round(chars / 1000)}k`
}

export function duration (from, to) {
  if (!from || !to) return ''
  const total = Math.max(0, Math.round(to - from))
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`
  return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`
}

export const RUN_TONE = {
  completed: 'success',
  running: 'accent',
  started: 'accent',
  failed: 'danger',
  terminated: 'danger',
  canceled: 'warning'
}

/** Turns a unified diff into lines the template can colour without a parser. */
export function diffLines (diff) {
  return (diff || '(new file)').split('\n').map((text) => ({
    text,
    cls: text.startsWith('+') ? 'diff-add' : text.startsWith('-') ? 'diff-del'
      : text.startsWith('@@') ? 'diff-meta' : ''
  }))
}
