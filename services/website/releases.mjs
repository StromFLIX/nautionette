export const RELEASES_URL = 'https://github.com/StromFLIX/nautionette/releases'
export const RELEASE_API = 'https://api.github.com/repos/StromFLIX/nautionette/releases/latest'

// Only link assets GitHub actually published. A failed/pending platform build
// must never produce a fabricated /latest/download URL or a dead button.
export function releaseDownloads (release) {
  if (!release || release.draft || release.prerelease || typeof release.tag_name !== 'string') return []
  const prefix = `nautionette-${release.tag_name}-`
  const downloads = []
  for (const asset of release.assets || []) {
    if (asset.state !== 'uploaded' || !asset.size || !asset.name?.startsWith(prefix)) continue
    let url
    try { url = new URL(asset.browser_download_url) } catch { continue }
    if (url.origin !== 'https://github.com' || !url.pathname.startsWith('/StromFLIX/nautionette/releases/download/')) continue
    const suffix = asset.name.slice(prefix.length)
    const labels = {
      'mac-arm64.dmg': 'macOS · Apple silicon',
      'mac-x64.dmg': 'macOS · Intel',
      'linux-x86_64.AppImage': 'Linux · x64',
      'win-x64.exe': 'Windows · x64',
      'release.apk': 'Android · APK'
    }
    if (labels[suffix]) downloads.push({ label: labels[suffix], url: url.href, name: asset.name })
  }
  return downloads.sort((a, b) => a.label.localeCompare(b.label))
}

export function appleDistributionURL (value) {
  try {
    const url = new URL(value.trim())
    if (url.protocol !== 'https:' || url.username || url.password) return null
    if (url.hostname === 'testflight.apple.com' && url.pathname.startsWith('/join/')) return url.href
    if (url.hostname === 'apps.apple.com' && /\/id\d+/.test(url.pathname)) return url.href
  } catch {}
  return null
}

export async function loadDownloads (document, fetcher = fetch) {
  const status = document.getElementById('release-status')
  const list = document.getElementById('release-downloads')
  try {
    const response = await fetcher(RELEASE_API, { signal: AbortSignal.timeout(8000), headers: { Accept: 'application/vnd.github+json' } })
    if (!response.ok) throw new Error('Release unavailable')
    const release = await response.json()
    const downloads = releaseDownloads(release)
    if (!downloads.length) throw new Error('No published installers')
    status.textContent = `Latest stable release: ${release.tag_name}`
    for (const download of downloads) {
      const link = document.createElement('a')
      link.className = 'btn btn-primary'
      link.href = download.url
      link.textContent = download.label
      list.appendChild(link)
    }
  } catch {
    status.textContent = 'Check GitHub Releases for available versions and downloads.'
  }
  try {
    const response = await fetcher('/ios-url.txt', { signal: AbortSignal.timeout(8000), cache: 'no-store' })
    const url = response.ok ? appleDistributionURL(await response.text()) : null
    if (url) {
      const link = document.createElement('a')
      link.className = 'btn btn-primary'
      link.href = url
      link.textContent = url.includes('testflight.apple.com') ? 'iOS · TestFlight' : 'iOS · App Store'
      list.appendChild(link)
      document.getElementById('ios-status').textContent = 'iPhone and iPad: install through Apple using the link above.'
    }
  } catch { /* Apple distribution is optional; retain the truthful fallback. */ }
}
