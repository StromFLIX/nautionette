import { execFileSync } from 'node:child_process'

// Embedded in the bundle, never obtained from the connected server. Docker
// builds receive APP_VERSION; native CI builds have GitHub's immutable SHA/tag.
export function clientBuildInfo(command, env = process.env, revision = () => execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()) {
  let version = env.APP_VERSION || env.GITHUB_SHA
  if (!version && command === 'build') {
    try { version = revision() } catch { /* Source archives may not include Git. */ }
  }
  return {
    version: version || (command === 'serve' ? 'dev' : ''),
    release_tag: env.APP_RELEASE_TAG || (env.GITHUB_REF_TYPE === 'tag' ? env.GITHUB_REF_NAME : '') || ''
  }
}
