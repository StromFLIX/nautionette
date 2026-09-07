import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { addCoauthor, gitAuthorship, prepareProjects, projectEnvironment } from '../../images/pi-base/project-git.mjs'

const human = { name: 'Jane Contributor', email: '123+jane@users.noreply.github.com' }
const automation = { name: 'Project Bot', email: 'bot@example.test' }
const settings = {
  human_name: human.name, human_email: human.email,
  automation_name: automation.name, automation_email: automation.email,
}
const selected = 'a'.repeat(32)
const modes = ['automation', 'human_author', 'human_author_bot_coauthor', 'bot_author_human_coauthor']

for (const mode of modes) {
  test(`${mode}: real commits use configured metadata and trailers while preserving repository hooks/history`, () => {
    const root = mkdtempSync(join(tmpdir(), "nautionette-attribution-'"))
    const paths = {
      projectsRoot: join(root, 'projects'), repositoriesRoot: join(root, 'repositories'),
      hooksRoot: join(root, 'overlay'),
    }
    const job = {
      chat_id: 'c'.repeat(12), project_ids: [selected],
      git_authorship: { ...settings, authorship_mode: mode },
    }
    const environment = {
      ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_NOSYSTEM: '1',
      ...projectEnvironment(job, paths),
    }
    const git = (...args) => execFileSync('git', args, {
      env: environment, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'],
    }).trim()
    try {
      const metadata = join(paths.repositoriesRoot, selected)
      mkdirSync(paths.repositoriesRoot)
      git('init', '--bare', '-b', 'main', metadata)
      const originalHooks = join(root, 'original-hooks')
      mkdirSync(originalHooks)
      const prepareHook = '#!/bin/sh\nprintf "\\nOriginal hook ran\\n" >> "$1"\n'
      writeFileSync(join(originalHooks, 'prepare-commit-msg'), prepareHook, { mode: 0o755 })
      writeFileSync(join(originalHooks, 'pre-commit'), '#!/bin/sh\necho pre-commit > hook-ran\n', { mode: 0o755 })
      const messageHook = '#!/bin/sh\ncp "$1" validated-message\n'
      writeFileSync(join(originalHooks, 'commit-msg'), messageHook, { mode: 0o755 })
      git('--git-dir', metadata, 'config', 'core.hooksPath', originalHooks)
      prepareProjects(job, paths)
      const worktree = join(paths.projectsRoot, selected)
      const initial = git('-C', worktree, 'rev-parse', 'HEAD')
      writeFileSync(join(worktree, 'file.txt'), 'change\n')
      git('-C', worktree, 'add', 'file.txt')
      git('-C', worktree, 'commit', '-m', 'Implement something')
      const expectedAuthor = mode.startsWith('human_author') ? human : automation
      const expectedCoauthor = mode === 'human_author_bot_coauthor' ? automation
        : mode === 'bot_author_human_coauthor' ? human : null
      assert.equal(git('-C', worktree, 'log', '-1', '--format=%an|%ae|%cn|%ce'),
        `${expectedAuthor.name}|${expectedAuthor.email}|${automation.name}|${automation.email}`)
      const message = git('-C', worktree, 'log', '-1', '--format=%B')
      assert.match(message, /Original hook ran/)
      assert.equal(readFileSync(join(worktree, 'hook-ran'), 'utf8'), 'pre-commit\n')
      assert.equal(readFileSync(join(originalHooks, 'prepare-commit-msg'), 'utf8'), prepareHook)
      assert.equal(readFileSync(join(originalHooks, 'commit-msg'), 'utf8'), messageHook)
      assert.equal(readFileSync(join(worktree, 'validated-message'), 'utf8').trim(), message)
      if (expectedCoauthor) assert.ok(message.includes(`Co-authored-by: ${expectedCoauthor.name} <${expectedCoauthor.email}>`))
      else assert.doesNotMatch(message, /Co-authored-by:/)
      assert.equal(git('-C', worktree, 'rev-parse', 'HEAD^'), initial)
      const committed = git('-C', worktree, 'rev-parse', 'HEAD')
      writeFileSync(join(worktree, 'unfinished.txt'), 'keep\n')
      prepareProjects(job, paths)
      assert.equal(git('-C', worktree, 'rev-parse', 'HEAD'), committed)
      assert.equal(readFileSync(join(worktree, 'unfinished.txt'), 'utf8'), 'keep\n')

      // An existing hook still gets to reject a commit.
      writeFileSync(join(originalHooks, 'prepare-commit-msg'), '#!/bin/sh\nexit 42\n', { mode: 0o755 })
      assert.throws(() => git('-C', worktree, 'commit', '--allow-empty', '-m', 'Rejected'))
      assert.equal(git('-C', worktree, 'rev-parse', 'HEAD'), committed)
      writeFileSync(join(originalHooks, 'prepare-commit-msg'), prepareHook, { mode: 0o755 })
      for (const failure of ['#!/bin/sh\nexit 43\n', '#!/missing-hook-interpreter\n']) {
        writeFileSync(join(originalHooks, 'commit-msg'), failure, { mode: 0o755 })
        assert.throws(() => git('-C', worktree, 'commit', '--allow-empty', '-m', 'Rejected by validator'))
        assert.equal(git('-C', worktree, 'rev-parse', 'HEAD'), committed)
      }
      writeFileSync(join(originalHooks, 'commit-msg'), messageHook, { mode: 0o755 })

      // Attribution is also added after editing an initially empty commit message.
      writeFileSync(join(originalHooks, 'prepare-commit-msg'), '#!/bin/sh\nexit 0\n', { mode: 0o755 })
      const editor = join(root, 'editor')
      writeFileSync(editor, '#!/bin/sh\nprintf "Edited message\\n" > "$1"\n', { mode: 0o755 })
      execFileSync('git', ['-C', worktree, 'commit', '--allow-empty'], {
        env: { ...environment, GIT_EDITOR: `sh '${editor.replaceAll("'", "'\\''")}'` }, stdio: 'pipe',
      })
      const edited = git('-C', worktree, 'log', '-1', '--format=%B')
      assert.match(edited, /^Edited message/)
      assert.equal(edited.includes('Co-authored-by:'), Boolean(expectedCoauthor))

      // Switching modes on a later call must not retain the earlier overlay or trailer.
      const later = { ...job, git_authorship: { ...settings, authorship_mode: 'automation' } }
      prepareProjects(later, paths)
      Object.assign(environment, projectEnvironment(later, paths))
      writeFileSync(join(originalHooks, 'prepare-commit-msg'), prepareHook, { mode: 0o755 })
      git('-C', worktree, 'commit', '--allow-empty', '-m', 'Later call')
      assert.doesNotMatch(git('-C', worktree, 'log', '-1', '--format=%B'), /Co-authored-by:/)
      assert.equal(git('-C', worktree, 'log', '-1', '--format=%an'), automation.name)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
}

test('co-author trailers are deduplicated and preserve other contributors', () => {
  const root = mkdtempSync(join(tmpdir(), 'nautionette-trailers-'))
  try {
    const path = join(root, 'message')
    writeFileSync(path, 'Change\n\nCo-authored-by: Someone Else <else@example.test>\n')
    const environment = { ...process.env, NAUTIONETTE_GIT_COAUTHOR: 'Project Bot <bot@example.test>' }
    addCoauthor(path, environment)
    addCoauthor(path, environment)
    const message = readFileSync(path, 'utf8')
    assert.equal((message.match(/Project Bot/g) || []).length, 1)
    assert.match(message, /Co-authored-by: Someone Else <else@example.test>/)
    writeFileSync(path, '\n# Aborted commit\n')
    addCoauthor(path, environment)
    assert.equal(readFileSync(path, 'utf8'), '\n# Aborted commit\n')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('missing human identities and malformed jobs fail instead of silently misattributing', () => {
  for (const mode of modes.slice(1)) assert.throws(() => gitAuthorship({ git_authorship: { authorship_mode: mode } }))
  assert.throws(() => gitAuthorship({ git_authorship: { authorship_mode: 'unknown' } }))
  assert.throws(() => gitAuthorship({ git_authorship: { automation_name: 'Bot\nOther' } }))
  assert.throws(() => gitAuthorship({ git_authorship: { automation_email: 'invalid' } }))
  assert.equal(projectEnvironment({ project_ids: [selected] }).GIT_AUTHOR_NAME, 'Nautionette')
  assert.deepEqual(projectEnvironment({}), {})
})
