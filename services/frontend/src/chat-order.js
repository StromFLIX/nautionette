/** Conversation start is immutable; never fall back to a live activity clock. */
export function chatStartedAt (chat) {
  return Number.isFinite(chat.created_at) && chat.created_at > 0 ? chat.created_at : 0
}

/** The ID breaks ties so server activity ordering cannot shuffle equal timestamps. */
export function byChatStart (a, b) {
  return chatStartedAt(b) - chatStartedAt(a) || String(a.id).localeCompare(String(b.id))
}
