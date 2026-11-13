// One attribution model for calls and delivered results. Old saved messages
// remain readable; a sender on an assistant message is its author, not caller.
export function messageOrigin (meta = {}, role = 'assistant') {
  const origin = meta?.origin
  if (origin?.kind === 'workflow' && origin.workflow) return origin
  if (origin?.kind === 'agent' && origin.agent_id) return origin
  if (meta?.run?.workflow) return { kind: 'workflow', relation: 'result', ...meta.run }
  if (role === 'user' && meta?.sender_agent?.id) {
    const sender = meta.sender_agent
    return { kind: 'agent', relation: 'call', agent_id: sender.id, agent_name: sender.name || 'Agent', chat_id: sender.chat_id }
  }
  return null
}

export const triggerLabel = trigger => ({
  schedule: 'Scheduled', manual: 'Manually started', agent: 'Agent-started', webhook: 'Webhook-triggered'
})[trigger] || ''

export function originLinks (origin) {
  if (!origin) return {}
  return origin.kind === 'workflow'
    ? { workflow: `/workflows/${encodeURIComponent(origin.workflow)}`, run: origin.workflow_id ? `/runs/${encodeURIComponent(origin.workflow_id)}` : null }
    : { chat: origin.chat_id ? `/chats/${encodeURIComponent(origin.chat_id)}` : null }
}
