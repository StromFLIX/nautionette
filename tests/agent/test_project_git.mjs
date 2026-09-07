import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { credential, prepareProjects, projectEnvironment } from '../../images/pi-base/project-git.mjs'

const selected = 'a'.repeat(32)
const job = { chat_id: 'c'.repeat(12), project_ids: [selected], project_credentials: [
  { full_name: 'owner/repository', token: 'turn-secret', expires_at: '2099-01-01T00:00:00Z' },
] }

test('Git credentials only match selected HTTPS GitHub repositories and expire', () => {
  const environment = projectEnvironment(job)
  const request = 'protocol=https\nhost=github.com\npath=owner/repository.git\n\n'
  assert.equal(credential(request, environment), 'username=x-access-token\npassword=turn-secret\n\n')
  assert.equal(credential(request.replace('owner/repository', 'owner/other'), environment), '')
  assert.equal(credential(request.replace('github.com', 'other.example.com'), environment), '')
  assert.equal(credential(request.replace('protocol=https', 'protocol=http'), environment), '')
  assert.equal(credential(request.replace('owner/repository', 'owner/../repository'), environment), '')
  assert.equal(credential(request, projectEnvironment({ ...job, project_credentials: [
    { ...job.project_credentials[0], expires_at: '2000-01-01T00:00:00Z' },
  ] })), '')
})

test('Git settings authorize only mounted safe directories and never persist credentials', () => {
  assert.deepEqual(projectEnvironment({}), {})
  const environment = projectEnvironment(job)
  const entries = Array.from({ length: Number(environment.GIT_CONFIG_COUNT) }, (_, index) =>
    [environment[`GIT_CONFIG_KEY_${index}`], environment[`GIT_CONFIG_VALUE_${index}`]])
  assert.deepEqual(entries.filter(([key]) => key === 'safe.directory'), [
    ['safe.directory', `/projects/${selected}`],
    ['safe.directory', `/projects/.sessions/${selected}/${job.chat_id}`],
    ['safe.directory', `/project-repositories/${selected}`],
  ])
  assert.ok(entries.some(([key, value]) => key === 'credential.useHttpPath' && value === 'true'))
  assert.ok(entries.some(([key, value]) => key === 'http.followRedirects' && value === 'false'))
})

test('Git invokes the credential helper for GitHub without network access', () => {
  const environment = { ...process.env, ...projectEnvironment(job),
    GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_NOSYSTEM: '1' }
  const helper = fileURLToPath(new URL('../../images/pi-base/project-git.mjs', import.meta.url))
  environment.GIT_CONFIG_VALUE_1 = `!node "${helper}"`
  const result = execFileSync('git', ['credential', 'fill'], {
    env: environment, encoding: 'utf8', input: 'url=https://github.com/owner/repository.git\n\n',
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  assert.match(result, /password=turn-secret/)
  assert.throws(() => execFileSync('git', ['credential', 'fill'], {
    env: environment, input: 'url=https://github.com/owner/unselected.git\n\n', stdio: 'pipe',
  }))
})

for (const empty of [false, true]) {
  test(`chat worktrees keep independent detached HEADs and preserve work across turns (empty=${empty})`, () => {
    const root = mkdtempSync(join(tmpdir(), 'nautionette-worktrees-'))
    const environment = { ...process.env, GIT_AUTHOR_NAME: 'Test', GIT_AUTHOR_EMAIL: 'test@example.test',
      GIT_COMMITTER_NAME: 'Test', GIT_COMMITTER_EMAIL: 'test@example.test' }
    const git = (...args) => execFileSync('git', args, { env: environment, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim()
    try {
      const remote = join(root, 'remote.git')
      const metadataRoot = join(root, 'repositories')
      const metadata = join(metadataRoot, selected)
      mkdirSync(metadataRoot)
      git('init', '--bare', '-b', 'main', remote)
      if (!empty) {
        const seed = join(root, 'seed')
        git('clone', remote, seed)
        writeFileSync(join(seed, 'base.txt'), 'baseline\n')
        git('-C', seed, 'add', '.')
        git('-C', seed, 'commit', '-m', 'Initial')
        git('-C', seed, 'push', 'origin', 'main')
      }
      git('clone', '--bare', remote, metadata)
      git('--git-dir', metadata, 'remote', 'set-url', 'origin', 'https://offline.invalid/unreachable.git')
      const firstRoot = join(root, 'first-container')
      const secondRoot = join(root, 'second-container')
      const first = { ...job, project_baselines: { [selected]: 'main' } }
      const second = { ...first, chat_id: 'd'.repeat(12) }
      prepareProjects(first, { projectsRoot: firstRoot, repositoriesRoot: metadataRoot })
      prepareProjects(second, { projectsRoot: secondRoot, repositoriesRoot: metadataRoot })
      const firstPath = join(firstRoot, selected)
      const secondPath = join(secondRoot, selected)
      assert.equal(git('-C', firstPath, 'rev-parse', '--abbrev-ref', 'HEAD'), 'HEAD')
      assert.equal(git('-C', secondPath, 'rev-parse', '--abbrev-ref', 'HEAD'), 'HEAD')
      git('--git-dir', metadata, 'remote', 'set-url', 'origin', remote)
      writeFileSync(join(firstPath, 'first.txt'), 'first chat\n')
      git('-C', firstPath, 'add', '.')
      git('-C', firstPath, 'commit', '-m', 'First chat')
      const firstHead = git('-C', firstPath, 'rev-parse', 'HEAD')
      assert.notEqual(firstHead, git('-C', secondPath, 'rev-parse', 'HEAD'))
      assert.equal(existsSync(join(secondPath, 'first.txt')), false)
      writeFileSync(join(firstPath, 'unfinished.txt'), 'keep this\n')
      prepareProjects(first, { projectsRoot: firstRoot, repositoriesRoot: metadataRoot })
      assert.equal(git('-C', firstPath, 'rev-parse', 'HEAD'), firstHead)
      assert.equal(readFileSync(join(firstPath, 'unfinished.txt'), 'utf8'), 'keep this\n')
      git('-C', firstPath, 'push', 'origin', 'HEAD:refs/heads/main')
      assert.equal(git('--git-dir', remote, 'show', 'main:first.txt'), 'first chat')
      assert.equal(git('--git-dir', metadata, 'for-each-ref', '--format=%(refname)', 'refs/heads'), empty ? '' : 'refs/heads/main')
      writeFileSync(join(secondPath, 'second.txt'), 'second chat\n')
      git('-C', secondPath, 'add', '.')
      git('-C', secondPath, 'commit', '-m', 'Second chat')
      assert.throws(() => git('-C', secondPath, 'push', 'origin', 'HEAD:refs/heads/main'))
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
}