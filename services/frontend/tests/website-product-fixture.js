import { mockAgents } from './agent-fixture.js'

// Fictional demonstration only. No account, customer data, model calls or generated
// files are involved: metadata illustrates the real app's published-output UI.
export const captureTime = new Date('2026-06-15T10:30:00Z')
export const chatPath = '/chats/alpha'
export async function mockWebsiteProduct (context) {
  const state = await mockAgents(context)
  const now = captureTime.getTime() / 1000
  const research = state.addAgent('Research')
  state.addAgent('Builder')
  const writing = state.addAgent('Writing')
  Object.assign(state.data.chat, {
    title: 'Product brief & slides', agent_id: writing.id, agent_name: writing.name,
    agent_customized: false, answering: false, created_at: now - 600, updated_at: now,
    last_read_message_id: 'answer'
  })
  const output = {
    id: 'a'.repeat(32), revision: 1, title: 'Product brief & slides',
    summary: 'A focused brief, an editable deck and a PDF to share.',
    source_app: 'WordCraft · DeckCraft', turn_id: 'sample-turn', created_at: now,
    files: [
      { id: 'brief-source', name: 'product-brief.docx', size: 28672, mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', role: 'source', previewable: false },
      { id: 'deck-source', name: 'product-slides.pptx', size: 94208, mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', role: 'source', previewable: false },
      { id: 'share-pdf', name: 'brief-and-slides.pdf', size: 126976, mime_type: 'application/pdf', role: 'file', previewable: false }
    ]
  }
  const answer = '## Ready to refine\n\nThe brief covers the audience and first release. The six-slide deck is editable; open questions are marked.'
  state.data.messages = [
    { id: 'question', role: 'user', created_at: now - 120, meta: {}, content: 'Create a product brief and an editable slide deck from these notes. Calmer team workspace: conversations, research and files together.' },
    { id: 'answer', role: 'assistant', created_at: now - 30, content: answer, meta: { outputs: [output], steps: [
      { kind: 'tool', id: 'brief', name: 'wordcraft_create_document', ok: true, args: { title: 'Product brief' }, result: 'Sample brief prepared.' },
      { kind: 'tool', id: 'slides', name: 'deckcraft_create_presentation', ok: true, args: { title: 'Product slides' }, result: 'Sample deck prepared.' },
      { kind: 'tool', id: 'publish', name: 'publish_output', ok: true, args: { title: output.title }, result: 'Sample output metadata ready.' },
      { kind: 'text', text: answer }
    ] } }
  ]
  state.data.outputs = [output]
  state.data.active_turn = null
  for (const [index, title] of ['Reading list for the week', 'Plan the first release', 'A clearer onboarding flow', 'Notes for the next workshop', 'Research questions', 'Turn notes into next steps'].entries()) {
    const id = `sample-${index}`
    state.chats[id] = { chat: { ...state.data.chat, id, title, agent_id: index % 2 ? writing.id : research.id, agent_name: index % 2 ? 'Writing' : 'Research', created_at: now - 3600 * (index + 1), updated_at: now - 3600 * (index + 1), last_read_message_id: null }, messages: [], active_turn: null }
  }
  // Register last, before the reusable fixture fallback. An empty but valid draft
  // avoids a misleading 'Draft sync unavailable' notice in the composer.
  const requests = []
  await context.route('**/api/**', async route => {
    const { pathname } = new URL(route.request().url())
    const method = route.request().method()
    requests.push({ path: pathname, method })
    if (pathname.endsWith('/draft')) return route.fulfill({ json: { text: '', attachments: [], revision: 0 } })
    if (pathname === '/api/drafts') return route.fulfill({ json: { drafts: [] } })
    if (pathname === '/api/workflows') return route.fulfill({ json: { workflows: [] } })
    if (pathname === '/api/runs') return route.fulfill({ json: { runs: [] } })
    if (method !== 'GET' && method !== 'HEAD' && !pathname.endsWith('/read-state')) throw new Error(`Unexpected capture mutation: ${method} ${pathname}`)
    return route.fallback()
  })
  await context.addInitScript(() => {
    localStorage.setItem('nautionette.token', 'sample-capture-token-not-a-credential')
    localStorage.setItem('nautionette.preferences.v1', JSON.stringify({ theme: 'denkzeug', skin: '', interfaceSize: 125 }))
  })
  return { state, requests }
}
