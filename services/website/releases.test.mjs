import { test } from 'node:test'
import assert from 'node:assert/strict'
import { brand } from './brand.mjs'
import { releaseDownloads, appleDistributionURL, loadDownloads, RELEASE_API, RELEASES_URL } from './releases.mjs'

function asset (suffix, extra = {}) {
  return { name: `nautionette-v1.2.3-${suffix}`, state: 'uploaded', size: 100,
    browser_download_url: `${brand.repository}/releases/download/v1.2.3/nautionette-v1.2.3-${suffix}`, ...extra }
}
function documentStub () {
  const nodes = Object.fromEntries(['release-status', 'release-downloads', 'ios-status'].map(id => [id, {
    children: [], textContent: '', appendChild (child) { this.children.push(child) }
  }]))
  return { getElementById: id => nodes[id], createElement: () => ({}) }
}

test('release API and download links share the canonical brand repository', () => {
  assert.equal(RELEASES_URL, `${brand.repository}/releases`)
  assert.equal(RELEASE_API, `https://api.github.com/repos${new URL(brand.repository).pathname}/releases/latest`)
})

test('published release assets map to exact platform downloads, not guessed URLs', () => {
  const release = { tag_name: 'v1.2.3', assets: [
    ...['mac-arm64.dmg', 'mac-x64.dmg', 'linux-x86_64.AppImage', 'win-x64.exe', 'release.apk'].map(suffix => asset(suffix)),
    asset('debug.apk'), asset('ios-unsigned-archive.zip'),
    asset('release.apk', { state: 'new' }), asset('release.apk', { size: 0 }),
    asset('release.apk', { browser_download_url: 'https://evil.example/download.apk' }),
    asset('release.apk', { browser_download_url: 'https://github.com/other/repo/releases/download/v1.2.3/nautionette-v1.2.3-release.apk' }),
    asset('release.apk', { name: 'nautionette-v0.0.1-release.apk' })
  ] }
  const downloads = releaseDownloads(release)
  assert.equal(downloads.length, 5)
  assert.ok(downloads.every(item => release.assets.some(asset => asset.browser_download_url === item.url)))
  assert.deepEqual(releaseDownloads({ ...release, prerelease: true }), [])
  assert.deepEqual(releaseDownloads({ ...release, draft: true }), [])
  assert.deepEqual(releaseDownloads(null), [])
})

test('Apple distribution accepts only public Apple installation links', () => {
  assert.equal(appleDistributionURL('https://testflight.apple.com/join/abcd'), 'https://testflight.apple.com/join/abcd')
  assert.equal(appleDistributionURL('https://apps.apple.com/us/app/example/id123456'), 'https://apps.apple.com/us/app/example/id123456')
  for (const url of ['', 'javascript:alert(1)', 'https://testflight.apple.com.evil/join/abc', 'http://testflight.apple.com/join/abc', 'https://user:pass@testflight.apple.com/join/abc']) {
    assert.equal(appleDistributionURL(url), null)
  }
})

test('website advertises latest tag and only completed platforms', async () => {
  const document = documentStub()
  await loadDownloads(document, async url => url === RELEASE_API
    ? { ok: true, json: async () => ({ tag_name: 'v1.2.3', assets: [asset('release.apk')] }) }
    : { ok: true, text: async () => 'https://testflight.apple.com/join/abcd' })
  assert.match(document.getElementById('release-status').textContent, /v1.2.3/)
  const links = document.getElementById('release-downloads').children
  assert.equal(links.length, 2)
  assert.equal(links[0].textContent, 'Android · APK')
  assert.equal(links[0].href, asset('release.apk').browser_download_url)
  assert.equal(links[1].textContent, 'iOS · TestFlight')
})

test('offline, rate-limited and empty releases retain truthful fallback without dead download links', async () => {
  for (const fetcher of [
    async () => { throw new Error('offline') },
    async () => ({ ok: false }),
    async url => url === RELEASE_API ? { ok: true, json: async () => ({ tag_name: 'v1.2.3', assets: [] }) } : { ok: false }
  ]) {
    const document = documentStub()
    await loadDownloads(document, fetcher)
    assert.match(document.getElementById('release-status').textContent, /Check GitHub Releases/)
    assert.equal(document.getElementById('release-downloads').children.length, 0)
  }
})
