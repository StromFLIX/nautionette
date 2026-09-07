import { execFileSync, spawnSync } from 'node:child_process'
import { accessSync, constants, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const DEFAULT_HOOKS_ROOT = '/workspace/git-authorship'

export function gitAuthorship(job) {
  const settings = job.git_authorship || {}
  const mode = settings.authorship_mode || 'automation'
  if (!['automation', 'human_author', 'human_author_bot_coauthor', 'bot_author_human_coauthor'].includes(mode)) {
    throw new Error('Unknown Git authorship mode')
  }
  const automation = {
    name: settings.automation_name ?? 'Nautionette',
    email: settings.automation_email ?? 'nautionette@users.noreply.github.com',
  }
  const human = { name: settings.human_name, email: settings.human_email }
  for (const identity of mode === 'automation' ? [automation] : [automation, human]) {
    if (typeof identity.name !== 'string' || !identity.name.trim() ||
        /[<>\x00-\x1f\x7f]/.test(identity.name) || typeof identity.email !== 'string' ||
        !/^[^\s<>@\x00-\x1f\x7f]+@[^\s<>@\x00-\x1f\x7f]+$/.test(identity.email)) {
      throw new Error('Invalid Git authorship identity; configure name and email in Settings')
    }
  }
  return {
    author: mode.startsWith('human_author') ? human : automation,
    committer: automation,
    coauthor: mode === 'human_author_bot_coauthor' ? automation
      : mode === 'bot_author_human_coauthor' ? human : null,
  }
}

export function projectEnvironment(job, {
  hooksRoot = DEFAULT_HOOKS_ROOT, repositoriesRoot = '/project-repositories', includeHooks = true,
} = {}) {
  if (!job.project_ids?.length) return {}
  const { author, committer, coauthor } = gitAuthorship(job)
  const configuration = [
    ['credential.helper', ''],
    ['credential.https://github.com.helper', '!node /usr/local/lib/nautionette/project-git.mjs'],
    ['credential.useHttpPath', 'true'],
    ['http.followRedirects', 'false'],
    ['user.name', committer.name],
    ['user.email', committer.email],
    ['gc.auto', '0'],
    ...job.project_ids.flatMap((id) => [
      ['safe.directory', `/projects/${id}`],
      ['safe.directory', `/projects/.sessions/${id}/${job.chat_id}`],
      ['safe.directory', `${repositoriesRoot}/${id}`],
      ...(coauthor && includeHooks ? [
        [`includeIf.gitdir:${repositoriesRoot}/${id}/.path`, join(hooksRoot, id, 'config')],
      ] : []),
    ]),
  ]
  const environment = {
    NAUTIONETTE_PROJECT_CREDENTIALS: JSON.stringify(job.project_credentials || []),
    GIT_AUTHOR_NAME: author.name,
    GIT_AUTHOR_EMAIL: author.email,
    GIT_COMMITTER_NAME: committer.name,
    GIT_COMMITTER_EMAIL: committer.email,
    NAUTIONETTE_GIT_COAUTHOR: coauthor ? `${coauthor.name} <${coauthor.email}>` : '',
    GIT_TERMINAL_PROMPT: '0',
    GIT_ASKPASS: '/bin/false',
    GIT_CONFIG_COUNT: String(configuration.length),
  }
  configuration.forEach(([key, value], index) => {
    environment[`GIT_CONFIG_KEY_${index}`] = key
    environment[`GIT_CONFIG_VALUE_${index}`] = value
  })
  return environment
}

export function prepareProjects(job, {
  projectsRoot = '/projects', repositoriesRoot = '/project-repositories', hooksRoot = DEFAULT_HOOKS_ROOT,
} = {}) {
  // Discover repository hooks without our per-turn overlay, even on a repeated preparation.
  const environment = { ...process.env, ...projectEnvironment(job, { repositoriesRoot, includeHooks: false }) }
  for (const id of job.project_ids || []) {
    if (!/^[a-f0-9]{32}$/.test(id) || !/^[a-f0-9]{12}$/.test(job.chat_id || '')) {
      throw new Error('Invalid project worktree identity')
    }
    const metadata = join(repositoriesRoot, id)
    const worktree = join(projectsRoot, '.sessions', id, job.chat_id)
    const git = (...args) => execFileSync('git', ['--git-dir', metadata, ...args], {
      env: environment, encoding: 'utf8', input: '', stdio: ['pipe', 'pipe', 'pipe'],
    }).trim()
    const repository = job.project_remotes?.[id]
    if (repository && git('remote', 'get-url', 'origin') !== `https://github.com/${repository}.git`) {
      git('remote', 'set-url', 'origin', `https://github.com/${repository}.git`)
    }
    if (!existsSync(join(worktree, '.git'))) {
      mkdirSync(worktree, { recursive: true })
      const baseline = job.project_baselines?.[id] || 'main'
      let revision
      for (const reference of [`refs/remotes/origin/${baseline}`, `refs/heads/${baseline}`, 'HEAD']) {
        try {
          revision = git('rev-parse', '--verify', `${reference}^{commit}`)
          break
        } catch {}
      }
      if (!revision) {
        const tree = git('hash-object', '-t', 'tree', '-w', '--stdin')
        revision = git('commit-tree', tree, '-m', 'Initialize project workspace')
      }
      git('worktree', 'add', '--detach', '--lock', '--reason', `Nautionette chat ${job.chat_id}`, '--', worktree, revision)
    }
    const alias = join(projectsRoot, id)
    if (!existsSync(alias)) symlinkSync(worktree, alias, 'dir')
    if (gitAuthorship(job).coauthor) {
      prepareAuthorshipHooks(worktree, join(hooksRoot, id), environment)
    }
  }
}

function prepareAuthorshipHooks(worktree, directory, environment) {
  const original = resolve(worktree, execFileSync('git', ['-C', worktree, 'rev-parse', '--git-path', 'hooks'], {
    env: environment, encoding: 'utf8',
  }).trim())
  const hooks = join(directory, 'hooks')
  mkdirSync(directory, { recursive: true })
  rmSync(hooks, { recursive: true, force: true })
  mkdirSync(hooks)
  // Preserve repository hooks, including configured core.hooksPath, without modifying them.
  if (existsSync(original)) {
    for (const name of readdirSync(original)) {
      if (name !== 'commit-msg') symlinkSync(join(original, name), join(hooks, name))
    }
  }
  const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`
  writeFileSync(join(hooks, 'commit-msg'),
    `#!/bin/sh\nexec node ${quote(fileURLToPath(import.meta.url))} coauthor ${quote(join(original, 'commit-msg'))} "$@"\n`,
    { mode: 0o755 })
  writeFileSync(join(directory, 'config'), `[core]\n\thooksPath = ${JSON.stringify(hooks)}\n`)
}

export function addCoauthor(messagePath, environment) {
  const coauthor = environment.NAUTIONETTE_GIT_COAUTHOR
  if (!coauthor) return
  // Never turn an empty/aborted commit into a commit containing only attribution.
  const message = readFileSync(messagePath, 'utf8')
  if (!message.split('\n').some((line) => line.trim() && !line.startsWith('#'))) return
  execFileSync('git', ['interpret-trailers', '--in-place', '--if-exists', 'addIfDifferent',
    '--if-missing', 'add', '--trailer', `Co-authored-by: ${coauthor}`, messagePath], { env: environment })
}

export function credential(input, environment) {
  const fields = Object.fromEntries(input.split('\n').filter((line) => line.includes('=')).map((line) => {
    const position = line.indexOf('=')
    return [line.slice(0, position), line.slice(position + 1)]
  }))
  if (fields.protocol !== 'https' || fields.host !== 'github.com') return ''
  const allowed = JSON.parse(environment.NAUTIONETTE_PROJECT_CREDENTIALS || '[]')
  const match = allowed.find((entry) =>
    (fields.path === entry.full_name || fields.path === `${entry.full_name}.git`) &&
    Date.parse(entry.expires_at) > Date.now())
  if (!match) return ''
  return `username=x-access-token\npassword=${match.token}\n\n`
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv[2] === 'get') {
    process.stdout.write(credential(readFileSync(0, 'utf8'), process.env))
  } else if (process.argv[2] === 'coauthor') {
    // Run after the editor, but before repository commit-message validation.
    addCoauthor(process.argv[4], process.env)
    // Git ignores missing/non-executable hooks, but an executable hook's failure must stop the commit.
    let executable = true
    try {
      accessSync(process.argv[3], constants.X_OK)
    } catch (error) {
      if (!['ENOENT', 'EACCES'].includes(error.code)) throw error
      executable = false
    }
    if (executable) {
      const original = spawnSync(process.argv[3], process.argv.slice(4), { stdio: 'inherit' })
      if (original.error) throw original.error
      if (original.status !== 0) process.exit(original.status || 1)
    }
  }
}
