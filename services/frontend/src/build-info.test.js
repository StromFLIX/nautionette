import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatBuildInfo } from './build-info.js'

const commit = '68c4590f45388cc3d9270c459a5cd30985aba4e4'

test('development source revisions display seven characters and preserve the full link target', () => {
  assert.deepEqual(formatBuildInfo({ version: commit, environment: 'production' }), { label: '68c4590', commit })
  assert.deepEqual(formatBuildInfo({ version: 'dev', build_commit: commit.toUpperCase() }), { label: '68c4590', commit })
  assert.deepEqual(formatBuildInfo({ version: commit, build_commit: 'not-a-commit' }), { label: '68c4590', commit })
})

test('only explicit release metadata overrides a source revision', () => {
  assert.deepEqual(formatBuildInfo({ version: commit, release_tag: 'v1.2.3' }), { label: 'v1.2.3', commit: null })
  assert.deepEqual(formatBuildInfo({ version: commit, release_tag: 'v1.2.3-rc.1' }), { label: '68c4590', commit })
})

test('legacy/self-hosted APIs can report semver via version without duplicated prefixes', () => {
  for (const version of ['1.2.3', 'v1.2.3']) assert.deepEqual(formatBuildInfo({ version }), { label: 'v1.2.3', commit: null })
  assert.equal(formatBuildInfo({ version: '1.2.3-rc.1+build.2' }).label, 'v1.2.3-rc.1+build.2')
})

test('unknown values are bounded non-link labels, never arbitrary strings or URLs', () => {
  for (const version of [undefined, null, {}, [], 3, '', 'v01.2.3', 'vv1.2.3', 'a'.repeat(10000), '<script>', 'https://evil.example', 'javascript:alert(1)', `${commit}/bad`, '123abcd']) {
    assert.deepEqual(formatBuildInfo({ version, release_tag: version, build_commit: version }), { label: 'Unknown', commit: null })
  }
  for (const version of ['dev', 'development', 'local', 'test']) assert.deepEqual(formatBuildInfo({ version }), { label: 'dev', commit: null })
  assert.deepEqual(formatBuildInfo(null), { label: 'Unknown', commit: null })
})
