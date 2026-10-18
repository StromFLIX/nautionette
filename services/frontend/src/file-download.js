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
    // FileReader avoids spreading large byte arrays onto the JS stack and keeps
    // arbitrary binary source/export files intact across the native bridge.
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result.slice(reader.result.indexOf(',') + 1))
      reader.onerror = () => reject(reader.error || new Error('Could not read file data'))
      reader.readAsDataURL(blob)
    })
    signal?.throwIfAborted()
    return Downloads.save({ name, mimeType: mimeType || 'application/octet-stream', base64 })
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
