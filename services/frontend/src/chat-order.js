/** One clock for chat rows and group ordering. Fall back for older cached lists. */
export function chatRecency (chat, order = 'activity') {
  const field = { activity: 'updated_at', messages: 'last_message_at', user: 'last_user_message_at' }[order] || 'updated_at'
  return chat[field] ?? chat.updated_at ?? chat.created_at ?? 0
}
