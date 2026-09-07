import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, symlinkSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

export function projectEnvironment(job) {
  if (!job.project_ids?.length) return {}
  const configuration = [
    ['credential.helper', ''],
    ['credential.https://github.com.helper', '!node /usr/local/lib/nautionette/project-git.mjs'],
    ['credential.useHttpPath', 'true'],
    ['http.followRedirects', 'false'],
    ['user.name', 'Nautionette'],
    ['user.email', 'nautionette@users.noreply.github.com'],
    ['gc.auto', '0'],
    ...job.project_ids.flatMap((id) => [
      ['safe.directory', `/projects/${id}`],
      ['safe.directory', `/projects/.sessions/${id}/${job.chat_id}`],
      ['safe.directory', `/project-repositories/${id}`],
    ]),
  ]
  const environment = {
    NAUTIONETTE_PROJECT_CREDENTIALS: JSON.stringify(job.project_credentials || []),
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

export function prepareProjects(job, { projectsRoot = '/projects', repositoriesRoot = '/project-repositories' } = {}) {
  const environment = { ...process.env, ...projectEnvironment(job) }
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
  }
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

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href && process.argv[2] === 'get') {
  process.stdout.write(credential(readFileSync(0, 'utf8'), process.env))
}