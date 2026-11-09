import { byChatActivity, chatActivityAt } from './chat-order.js'

/** Delegated chats form a forest; branches remain independent conversations. */
export function chatForest (chats) {
  const nodes = new Map(chats.map(chat => [chat.id, { chat, children: [] }]))
  const roots = []
  for (const node of nodes.values()) {
    const parent = nodes.get(node.chat.delegated_parent_chat_id)
    const seen = new Set([node.chat.id])
    let ancestor = parent
    while (ancestor && !seen.has(ancestor.chat.id)) {
      seen.add(ancestor.chat.id)
      ancestor = nodes.get(ancestor.chat.delegated_parent_chat_id)
    }
    // Missing/deleted parents and malformed cycles must never hide a chat.
    if (parent && !ancestor) parent.children.push(node)
    else roots.push(node)
  }
  function summarise (node) {
    node.children.forEach(summarise)
    node.children.sort((a, b) => byChatActivity(a.summary, b.summary))
    node.count = node.children.reduce((sum, child) => sum + 1 + child.count, 0)
    node.summary = {
      ...node.chat,
      answering: Boolean(node.chat.answering || node.children.some(child => child.summary.answering)),
      unread: Boolean(node.chat.unread || node.children.some(child => child.summary.unread)),
      last_activity_at: Math.max(chatActivityAt(node.chat), ...node.children.map(child => chatActivityAt(child.summary)))
    }
  }
  roots.forEach(summarise)
  return roots.sort((a, b) => byChatActivity(a.summary, b.summary))
}

/** Search/agent filters retain context paths, rather than orphaning matching children. */
export function filterChatForest (forest, matches) {
  function visit (node, parentMatches = false) {
    const matched = parentMatches || matches(node.chat)
    const children = node.children.map(child => visit(child, matched)).filter(Boolean)
    const count = children.reduce((sum, child) => sum + 1 + child.count, 0)
    return matched || children.length ? { ...node, children, count } : null
  }
  return forest.map(node => visit(node)).filter(Boolean)
}

export function chatTreeRows (root, expanded, reveal = false) {
  const rows = []
  function visit (node, depth) {
    const open = node.children.length > 0 && (reveal || expanded.has(node.chat.id))
    rows.push({ ...node, depth, open })
    if (open) node.children.forEach(child => visit(child, depth + 1))
  }
  visit(root, 0)
  return rows
}

export function chatAncestors (chats, id) {
  const byId = new Map(chats.map(chat => [chat.id, chat]))
  const ancestors = []
  const seen = new Set([id])
  let parent = byId.get(id)?.delegated_parent_chat_id
  while (parent && byId.has(parent) && !seen.has(parent)) {
    seen.add(parent)
    ancestors.push(parent)
    parent = byId.get(parent).delegated_parent_chat_id
  }
  return ancestors
}
