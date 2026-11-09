const COMMIT = /^[0-9a-f]{40}$/i
const CORE = '(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)'
const RELEASE = new RegExp(`^v?${CORE}$`)
const SEMVER = new RegExp(`^v?${CORE}(?:-[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?(?:\\+[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$`)
const text = value => typeof value === 'string' && value.length <= 64 ? value.trim() : ''

// APP_VERSION remains the raw source revision. Only explicit release metadata
// (or a legacy API's semver version) identifies a release; tags are never inferred.
export function formatBuildInfo (system = {}) {
  const version = text(system?.version)
  const release = text(system?.release_tag)
  if (RELEASE.test(release)) return { label: `v${release.replace(/^v/, '')}`, commit: null }
  if (SEMVER.test(version)) return { label: `v${version.replace(/^v/, '')}`, commit: null }
  const buildCommit = text(system?.build_commit)
  const revision = COMMIT.test(buildCommit) ? buildCommit : version
  if (COMMIT.test(revision)) {
    const commit = revision.toLowerCase()
    return { label: commit.slice(0, 7), commit }
  }
  // Unrecognized API values must not turn into URLs or overflow compact headers.
  return { label: /^(dev|development|local|test)$/i.test(version) ? 'dev' : 'Unknown', commit: null }
}
