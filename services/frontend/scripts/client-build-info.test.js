import test from 'node:test'
import assert from 'node:assert/strict'
import { clientBuildInfo } from './client-build-info.js'
import { formatBuildInfo } from '../src/build-info.js'

const commit = '68c4590f45388cc3d9270c459a5cd30985aba4e4'
test('web Docker build embeds explicit revision and optional release', () => {
  const info = clientBuildInfo('build', { APP_VERSION: commit, APP_RELEASE_TAG: 'v1.2.3', GITHUB_SHA: 'ignored' })
  assert.deepEqual(info, { version: commit, release_tag: 'v1.2.3' })
  assert.equal(formatBuildInfo(info).label, 'v1.2.3')
})
test('native builds use CI SHA and only explicit tag metadata', () => {
  assert.deepEqual(clientBuildInfo('build', { GITHUB_SHA: commit, GITHUB_REF_TYPE: 'tag', GITHUB_REF_NAME: 'v2.3.4' }), { version: commit, release_tag: 'v2.3.4' })
  assert.deepEqual(clientBuildInfo('build', { GITHUB_SHA: commit, GITHUB_REF_TYPE: 'branch', GITHUB_REF_NAME: 'main' }), { version: commit, release_tag: '' })
})
test('local builds use Git, development is dev, archives are unknown', () => {
  assert.equal(clientBuildInfo('build', {}, () => commit).version, commit)
  assert.equal(clientBuildInfo('serve', {}, () => { throw Error('Must not call Git') }).version, 'dev')
  assert.equal(formatBuildInfo(clientBuildInfo('build', {}, () => { throw Error('No Git') })).label, 'Unknown')
})
