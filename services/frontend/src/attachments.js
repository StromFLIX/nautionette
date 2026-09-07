export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
export const MAX_IMAGES = 4
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024

export function addImages (existing, files, uuid = () => crypto.randomUUID()) {
  const selected = Array.from(files)
  if (existing.length + selected.length > MAX_IMAGES) throw new Error('Attach at most 4 images per message.')
  for (const file of selected) {
    if (!IMAGE_TYPES.includes(file.type)) throw new Error('Choose PNG, JPEG, GIF or WebP images.')
    if (!file.size || file.size > MAX_IMAGE_BYTES) throw new Error('Images must be nonempty and at most 5 MiB each.')
  }
  return [...existing, ...selected.map((file) => ({ id: uuid(), file, name: file.name, size: file.size, mime_type: file.type }))]
}

// Keep successful uploads on the draft so a partial failure/retry never uploads them twice.
// Only small server metadata, never File objects or image bytes, reaches the durable outbox.
export async function uploadImages (chatId, images, upload) {
  const result = []
  for (const image of images) {
    if (image.uploadedChat !== chatId || !image.uploaded) {
      image.uploaded = await upload(chatId, image.file)
      image.uploadedChat = chatId
    }
    result.push(image.uploaded)
  }
  return result
}
