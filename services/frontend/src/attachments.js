export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
export const MAX_ATTACHMENTS = 4
export const MAX_FILE_BYTES = 5 * 1024 * 1024

export function isImage (attachment) {
  return IMAGE_TYPES.includes(attachment.mime_type || attachment.type)
}

export function addAttachments (existing, files, uuid = () => crypto.randomUUID()) {
  const selected = Array.from(files)
  if (existing.length + selected.length > MAX_ATTACHMENTS) throw new Error('Attach at most 4 files per message.')
  for (const file of selected) {
    if (!file.size || file.size > MAX_FILE_BYTES) throw new Error('Files must be nonempty and at most 5 MiB each.')
  }
  return [...existing, ...selected.map((file) => ({ id: uuid(), file, name: file.name, size: file.size, mime_type: file.type || 'application/octet-stream' }))]
}

// Keep successful uploads on the draft so a partial failure/retry never uploads them twice.
// Only small server metadata, never File objects or bytes, reaches the durable outbox.
export async function uploadAttachments (chatId, attachments, upload) {
  const result = []
  for (const attachment of attachments) {
    if (attachment.uploadedChat !== chatId || !attachment.uploaded) {
      attachment.uploaded = await upload(chatId, attachment.file)
      attachment.uploadedChat = chatId
    }
    result.push(attachment.uploaded)
  }
  return result
}
