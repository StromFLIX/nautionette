import { Capacitor, registerPlugin } from '@capacitor/core'

const Downloads = registerPlugin('NautionetteDownloads')

/** The caller owns/revokes a returned browser URL. Native saves finish only after
 * the user chooses a destination and Android has written and closed the file. */
export async function saveDownload (blob, name, mimeType, signal) {
  signal?.throwIfAborted()
  if (Capacitor.isNativePlatform()) {
    if (!Capacitor.isPluginAvailable('NautionetteDownloads')) {
      throw new Error('Update the Android app to enable saving files')
    }
    // Never put a whole file in a bridge/picker call. Besides multiplying memory
    // use, Capacitor can persist picker arguments into Android's limited activity
    // state. Only a small staging ID may survive while the picker is open.
    let id
    try {
      id = (await Downloads.begin({ size: blob.size })).id
    } catch (error) {
      if (error.code === 'UNIMPLEMENTED') throw new Error('Update the Android app to enable saving files safely')
      throw error
    }
    try {
      const chunkSize = 192 * 1024 // At most 256 KiB of base64 per bridge call.
      for (let offset = 0; offset < blob.size; offset += chunkSize) {
        signal?.throwIfAborted()
        const base64 = await new Promise((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result.slice(reader.result.indexOf(',') + 1))
          reader.onerror = () => reject(reader.error || new Error('Could not read file data'))
          reader.readAsDataURL(blob.slice(offset, offset + chunkSize))
        })
        signal?.throwIfAborted()
        await Downloads.append({ id, offset, base64 })
      }
      signal?.throwIfAborted()
      return await Downloads.save({ id, name, mimeType: mimeType || 'application/octet-stream' })
    } finally {
      // Also releases staging on read/bridge errors and navigation cancellation.
      // Do not mask the original failure if the native process has gone away.
      await Downloads.discard({ id }).catch(() => {})
    }
  }
  // Never navigate to or embed generated HTML/SVG/PDF in the app origin.
  const url = URL.createObjectURL(new Blob([blob], { type: 'application/octet-stream' }))
  const link = document.createElement('a')
  try {
    link.href = url
    link.download = name
    document.body.appendChild(link)
    link.click()
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  } finally {
    link.remove()
  }
  return { url }
}
