/**
 * The ordered story of one answer: the prose the model wrote and the tool calls
 * that happened between it. The backend stores the same shape in a message's
 * meta, so a reloaded chat reads exactly like the live stream did.
 */

export function addText (steps, text) {
  if (!text) return
  const last = steps[steps.length - 1]
  if (last?.kind === 'text') last.text += text
  else steps.push({ kind: 'text', text })
}

export function startTool (steps, event) {
  const step = {
    kind: 'tool',
    id: event.id || `call-${steps.length}`,
    name: event.name || '?',
    args: event.args ?? null,
    ok: null,
    result: ''
  }
  steps.push(step)
  return step
}

export function finishTool (steps, event) {
  const step = [...steps].reverse().find(
    (candidate) => candidate.kind === 'tool' && candidate.ok === null &&
      (!event.id || candidate.id === event.id)
  ) || startTool(steps, event)
  step.ok = !event.error
  step.result = event.result || ''
}

/** Folds one streamed agent event into the timeline. Anything else is ignored. */
export function foldEvent (steps, event) {
  if (event.type === 'delta') addText(steps, event.text)
  else if (event.type === 'tool') startTool(steps, event)
  else if (event.type === 'tool_done') finishTool(steps, event)
}

/** Group consecutive calls only; narration stays visible between disclosures. */
export function groupToolCalls (parts) {
  if (!parts.some((part) => part.kind === 'tool')) return parts
  const grouped = []
  let group = null
  parts.forEach((part, index) => {
    // Invisible streaming deltas should not create separate disclosures.
    if (part.kind === 'text' && !part.text.trim()) return
    if (part.kind === 'tool') {
      if (!group) {
        // The first call's position stays stable as the timeline grows, even
        // for legacy calls without IDs. Keep expanded groups/calls mounted.
        group = { kind: 'tool-group', id: `tool-group-${index}`, steps: [] }
        grouped.push(group)
      }
      group.steps.push(part)
    } else {
      grouped.push(part)
      group = null
    }
  })
  return grouped
}

/** An interrupted call may have no result, but is no longer executing. */
export function isToolPending (step) {
  return step.kind === 'tool' && step.ok === null && step.finished_at == null && !step.interrupted
}

/** Count invocations, not unique names, including calls still in progress. */
export function summarizeToolCalls (steps) {
  const tools = steps.filter((step) => step.kind === 'tool')
  const shell = tools.filter((step) => ['bash', 'powershell'].includes(step.name)).length
  const other = tools.length - shell
  const count = (number, noun) => `${number} ${noun}${number === 1 ? '' : 's'}`
  const labels = []
  if (shell) labels.push(count(shell, 'shell command'))
  if (other) labels.push(count(other, shell ? 'other tool call' : 'tool call'))
  return {
    label: `Ran ${labels.join(' and ') || '0 tool calls'}`,
    failed: tools.filter((step) => step.ok === false).length,
    pending: tools.some(isToolPending)
  }
}

function baseName (path = '') {
  const clean = String(path).replace(/[/\\]+$/, '')
  return clean.slice(Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\')) + 1) || clean
}

function sentence (text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

function firstScalar (args) {
  for (const value of Object.values(args)) {
    if (typeof value === 'string' && value.trim()) return value
    if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  }
  return ''
}

// Pi's built-in tools have known arguments, so they get a sentence a human reads.
const BUILT_IN = {
  read: (a) => ({
    icon: 'description',
    title: `Read ${baseName(a.path) || 'a file'}`,
    detail: a.line_start ? `lines ${a.line_start}–${a.line_end || 'end'}` : a.path
  }),
  write: (a) => ({ icon: 'note_add', title: `Wrote ${baseName(a.path) || 'a file'}`, detail: a.path }),
  edit: (a) => ({ icon: 'edit', title: `Edited ${baseName(a.path) || 'a file'}`, detail: a.path }),
  bash: (a) => ({ icon: 'terminal', title: 'Ran a command', detail: a.command }),
  powershell: (a) => ({ icon: 'terminal', title: 'Ran a command', detail: a.command }),
  grep: (a) => ({
    icon: 'search',
    title: `Searched for “${a.pattern}”`,
    detail: [a.include, a.path].filter(Boolean).join(' in ')
  }),
  find: (a) => ({ icon: 'plagiarism', title: `Looked for “${a.pattern}”`, detail: a.path }),
  ls: (a) => ({ icon: 'folder_open', title: `Listed ${a.path || 'the workspace'}`, detail: '' })
}

/** A one-line heading for a tool call. `server` strips the federation prefix. */
export function describeTool (step, server = '') {
  const args = step.args && typeof step.args === 'object' ? step.args : {}
  const known = BUILT_IN[step.name]
  if (known) return { detail: '', ...known(args) }
  const prefix = `${server}_`
  const bare = server && step.name.startsWith(prefix) ? step.name.slice(prefix.length) : step.name
  return {
    icon: 'handyman',
    title: sentence(bare.replace(/[_-]+/g, ' ')),
    detail: firstScalar(args)
  }
}

/** Tool output is text, but the MCP bridge mostly returns JSON, so make it readable. */
export function prettyJson (value) {
  if (value == null || value === '') return ''
  if (typeof value !== 'string') return JSON.stringify(value, null, 2)
  const trimmed = value.trim()
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return value
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch {
    return value
  }
}
