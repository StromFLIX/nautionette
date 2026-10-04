/** Sent messages and completed turns advance this clock, never live progress. */
export function chatActivityAt (chat) {
  if (Number.isFinite(chat.last_activity_at) && chat.last_activity_at > 0) return chat.last_activity_at
  // Empty chats and older cached records still have a stable timestamp.
  return Number.isFinite(chat.created_at) && chat.created_at > 0 ? chat.created_at : 0
}

/** The ID breaks ties so unrelated API changes cannot shuffle equal timestamps. */
export function byChatActivity (a, b) {
  return chatActivityAt(b) - chatActivityAt(a) || String(a.id).localeCompare(String(b.id))
}
