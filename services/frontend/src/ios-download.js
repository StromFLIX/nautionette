export async function saveIOSDownload (blob, name, signal, { Filesystem, Directory, Share }) {
  // The share sheet offers Save to Files. Stage bounded chunks in the private
  // cache, never pass a whole attachment through the Capacitor bridge.
  const safeName = (name || 'download').replace(/[\\/\u0000-\u001f]/g, '_')
  const folder = `downloads/${crypto.randomUUID()}`
  const path = `${folder}/${safeName}`
  try {
    signal?.throwIfAborted()
    await Filesystem.mkdir({ path: folder, directory: Directory.Cache, recursive: true })
    await Filesystem.writeFile({ path, directory: Directory.Cache, data: '' })
    for (let offset = 0; offset < blob.size; offset += 192 * 1024) {
      signal?.throwIfAborted()
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result.split(',')[1])
        reader.onerror = () => reject(reader.error || new Error('Could not read file data'))
        reader.readAsDataURL(blob.slice(offset, offset + 192 * 1024))
      })
      signal?.throwIfAborted()
      await Filesystem.appendFile({ path, directory: Directory.Cache, data })
    }
    signal?.throwIfAborted()
    const { uri } = await Filesystem.getUri({ path, directory: Directory.Cache })
    await Share.share({ files: [uri], title: safeName })
    return {}
  } finally {
    await Filesystem.rmdir({ path: folder, directory: Directory.Cache, recursive: true }).catch(() => {})
  }
}
