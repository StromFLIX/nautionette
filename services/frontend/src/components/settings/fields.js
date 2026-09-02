/**
 * Declared fields.
 *
 * The backend publishes the fields an integration or MCP server needs, with the
 * pattern each one has to match. Both forms render and check them the same way,
 * so the rules live here rather than in either component.
 */

function pattern (field) {
  return new RegExp(`^(?:${field.pattern})$`)
}

/** An empty field explains itself; a filled one that is still refused does not. */
export function fieldError (field, draft) {
  const value = (draft[field.key] || '').trim()
  if (!value || pattern(field).test(value)) return ''
  return field.hint || 'This value is not accepted.'
}

export function draftIsValid (fields, draft) {
  return (fields || []).every((field) => {
    const value = (draft[field.key] || '').trim()
    if (!value) return Boolean(field.optional)
    return pattern(field).test(value)
  })
}

/** A stored secret is never sent back, so an empty field always means "keep it". */
export function secretPlaceholder (field, credential) {
  if (field.kind !== 'secret') return field.placeholder || ''
  if (credential?.mode === 'stored') return 'stored — type a new value to replace'
  if (credential?.mode === 'environment') return `$${credential.variable}`
  if (credential?.mode === 'gateway') return credential.variable ? `$${credential.variable}` : ''
  return field.placeholder || ''
}

export function credentialLabel (credential) {
  if (credential.mode === 'environment') return `key from $${credential.variable}`
  if (credential.mode === 'stored') return 'key stored in agentgateway'
  if (credential.mode === 'gateway') return 'key held by agentgateway'
  return 'no key'
}

/** Refill a reactive draft from a record, never carrying a secret across. */
export function resetDraft (draft, fields, source = {}) {
  for (const key of Object.keys(draft)) delete draft[key]
  for (const field of fields || []) {
    draft[field.key] = field.kind === 'secret' ? '' : source[field.key] || field.default || ''
  }
}
