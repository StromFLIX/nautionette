/** Pure dashboard display/form helpers. Manifests never become HTML or code. */
export function safeDashboardHref (value) {
  if (typeof value !== 'string' || /[\s\\\x00-\x1f]/.test(value)) return ''
  if (value.startsWith('/') && !value.startsWith('//')) return value
  try {
    const url = new URL(value)
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return ''
    return url.href
  } catch { return '' }
}

export function dashboardText (value) {
  if (value == null) return '—'
  return (typeof value === 'object' ? JSON.stringify(value) : String(value)).slice(0, 4000)
}

export function dashboardInputs (schema, values) {
  const result = {}
  for (const [key, field] of Object.entries(schema?.properties || {})) {
    const value = values[key]
    if (value === '' || value == null) {
      if (schema.required?.includes(key)) throw new Error(`${field.title || key} is required`)
      continue
    }
    if (['array', 'object'].includes(field.type)) {
      try { result[key] = JSON.parse(value) } catch { throw new Error(`${field.title || key} must be valid JSON`) }
    } else if (['integer', 'number'].includes(field.type)) {
      result[key] = Number(value)
      if (!Number.isFinite(result[key]) || (field.type === 'integer' && !Number.isInteger(result[key]))) throw new Error(`${field.title || key} must be ${field.type === 'integer' ? 'an integer' : 'a number'}`)
    } else result[key] = value
  }
  return result
}

export function dashboardOrder (items, order = []) {
  const ranks = new Map(order.map((id, index) => [id, index]))
  return [...items].sort((a, b) => (ranks.get(a.id) ?? Infinity) - (ranks.get(b.id) ?? Infinity))
}

export function dashboardLocation (item) {
  return { path: `/dashboards/${item.id}`, query: item.agent_id ? { agent: item.agent_id } : {} }
}
