import { test, expect } from '@playwright/test'
import { openChatConfiguration } from './helpers'

const firstId = 'a'.repeat(32)
const secondId = 'b'.repeat(32)
const repository = { id: 123, full_name: 'team/nautionette', default_branch: 'main', private: true }
const privateRepository = { id: 456, full_name: 'personal/private-repository', default_branch: 'main', private: true }
const teamConnection = { id: '1-10', app_id: '1', installation_id: '10', configured: true, automatic: true, account: 'team', slug: 'team-app', public_url: 'https://nautionette.example.com' }
const personalConnection = { ...teamConnection, id: '2-20', app_id: '2', installation_id: '20', account: 'personal', slug: 'personal-app' }

async function mockProjects (context) {
  const state = {
    app: { configured: false, registered: false, connections: [] },
    repositories: { [teamConnection.id]: [repository], [personalConnection.id]: [privateRepository] },
    repositoryRequests: [], added: [],
    projects: [], sent: [], saved: null, downloaded: false,
    chat: { id: 'alpha', title: 'Project work', agent_set: 'default', model: 'test/model', project_ids: [] },
    messages: [],
  }
  await context.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const data = () => route.request().postDataJSON()
    const reply = (json, status = 200) => route.fulfill({ status, json })
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') return reply({ components: [], agent_sets: [], model_key_present: true })
    if (path === '/api/catalog') return reply({ models: [], tools: [], agent_sets: [], default_model: 'test/model' })
    if (path === '/api/workflows') return reply({ workflows: [] })
    if (path === '/api/drafts') return reply({ drafts: [] })
    if (path === '/api/runs') return reply({ runs: [] })
    if (path === '/api/projects/github-app') {
      return reply(state.app)
    }
    if (path === '/api/projects/github-app/connect') {
      state.saved = data()
      return reply({ start_url: '/api/projects/github-app/start?state=test-state' })
    }
    if (path === '/api/projects/github-app/start') return route.fulfill({ contentType: 'text/html', body: '<form method="post" action="https://github.com/settings/apps/new?state=test-state"><input name="manifest" value="{}"></form><script>document.forms[0].submit()</script>' })
    if (path === '/api/projects/repositories') {
      const connectionId = new URL(route.request().url()).searchParams.get('connection_id')
      state.repositoryRequests.push(connectionId)
      const repositories = state.repositories[connectionId] || []
      return reply({ connection_id: connectionId, total_count: repositories.length, repositories })
    }
    if (path === '/api/projects') {
      if (method === 'POST') {
        const payload = data()
        state.added.push(payload)
        const selected = state.repositories[payload.connection_id]?.find((item) => item.full_name === payload.full_name)
        expect(selected).toBeDefined()
        const id = selected.id === repository.id ? firstId : secondId
        let project = state.projects.find((item) => item.id === id)
        if (!project) {
          project = { id, repository_id: selected.id, full_name: selected.full_name, connection_id: payload.connection_id, path: `/projects/${id}` }
          state.projects.push(project)
        }
        project.status = 'cloning'
        return reply(project, 202)
      }
      if (state.downloaded && state.projects[0]) state.projects[0].status = 'ready'
      return reply({ projects: state.projects })
    }
    if (path.startsWith('/api/projects/') && method === 'DELETE') {
      state.projects = state.projects.filter((project) => !path.endsWith(project.id))
      return reply({ ok: true })
    }
    if (path === '/api/chats') {
      if (method === 'POST') { Object.assign(state.chat, data()); return reply(state.chat) }
      return reply({ chats: [state.chat] })
    }
    if (path === '/api/chats/alpha/messages') {
      const payload = data()
      state.sent.push(payload)
      if (payload.project_ids !== undefined) state.chat.project_ids = payload.project_ids
      const message = { id: payload.message_id, chat_id: 'alpha', role: 'user', content: payload.text, meta: { project_ids: [...state.chat.project_ids] } }
      state.messages.push(message)
      return reply({ message, turn_id: message.id }, 202)
    }
    if (path === '/api/chats/alpha' && method === 'PATCH') {
      Object.assign(state.chat, data())
      return reply(state.chat)
    }
    const snapshot = { chat: state.chat, messages: state.messages, active_turn: null }
    if (path === '/api/chats/alpha/stream') return route.fulfill({ contentType: 'text/event-stream', body: `retry: 100\ndata: ${JSON.stringify({ type: 'snapshot', ...snapshot })}\n\n` })
    if (path === '/api/chats/alpha') return reply(snapshot)
    return reply({})
  })
  await context.route('https://github.com/settings/apps/new?state=test-state', async (route) => {
    expect(route.request().method()).toBe('POST')
    expect(route.request().postData()).toContain('manifest=')
    expect(route.request().headers().authorization).toBeUndefined()
    state.app = { configured: true, registered: false, connections: [teamConnection], public_url: state.saved.public_url }
    await route.fulfill({ status: 303, headers: { location: 'http://127.0.0.1:9012/settings/projects?github=connected' } })
  })
  return state
}

for (const instanceUrl of ['', 'https://instance.example.com']) {
  test(`public instance URL defaults to ${instanceUrl || 'the current browser origin'}`, async ({ page, context }) => {
    await mockProjects(context)
    await context.addInitScript((url) => {
      if (url) localStorage.setItem('nautionette.server', url)
    }, instanceUrl)
    await page.goto('/settings/projects')
    await expect(page.getByRole('button', { name: 'Connect GitHub' })).toBeEnabled()
    await expect(page.getByLabel('Public instance URL')).toHaveValue(instanceUrl || new URL(page.url()).origin)
  })
}

for (const width of [1440, 320]) {
  test(`configure, download and select shared projects at ${width}px`, async ({ page, context }) => {
    const state = await mockProjects(context)
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.setViewportSize({ width, height: width === 320 ? 568 : 1000 })
    await page.goto('/settings/projects')
    await expect(page.locator('input[type="file"]')).toHaveCount(0)
    await page.getByLabel('Public instance URL').fill('https://nautionette.example.com')
    await page.getByLabel('App owner').selectOption('organization')
    await page.getByLabel('Organization', { exact: true }).fill('team')
    await page.screenshot({ path: `/tmp/nautionette-github-connect-${width}.png`, fullPage: true })
    await page.getByRole('button', { name: 'Connect GitHub' }).click()
    await expect(page.getByText('Connected', { exact: true })).toBeVisible()
    expect(state.saved).toEqual({ public_url: 'https://nautionette.example.com', organization: 'team', new_app: false })
    await page.getByRole('button', { name: 'download Add', exact: true }).click()
    await expect(page.getByText('Downloading', { exact: true })).toBeVisible()
    state.downloaded = true
    await expect(page.getByText('ready', { exact: true })).toBeVisible()
    await page.screenshot({ path: `/tmp/nautionette-project-settings-${width}.png`, fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)

    state.projects.push({ id: secondId, repository_id: 456, full_name: 'team/another-repository-with-a-long-name', status: 'ready', path: `/projects/${secondId}` })
    await page.goto('/chats')
    if (width === 320) await page.getByTitle('New chat', { exact: true }).click()
    await openChatConfiguration(page)
    await page.getByRole('button', { name: 'Select projects', exact: true }).click()
    await page.getByRole('checkbox', { name: 'team/nautionette', exact: true }).check()
    await page.getByRole('checkbox', { name: 'team/another-repository-with-a-long-name' }).check()
    await expect(page.getByRole('button', { name: 'Manage projects' })).toBeInViewport()
    const menu = await page.locator('.project-picker').boundingBox()
    const count = await page.locator('.project-picker__foot > .caption').boundingBox()
    const manage = await page.getByRole('button', { name: 'Manage projects' }).boundingBox()
    expect(count.x + count.width).toBeLessThanOrEqual(manage.x)
    expect(menu.x).toBeGreaterThanOrEqual(0)
    expect(menu.x + menu.width).toBeLessThanOrEqual(width)
    expect(menu.y).toBeGreaterThanOrEqual(0)
    expect(menu.y + menu.height).toBeLessThanOrEqual(width === 320 ? 568 : 1000)
    await page.screenshot({ path: `/tmp/nautionette-project-picker-${width}.png` })
    await page.keyboard.press('Escape')
    await page.locator('.composer__input').fill('Update both projects')
    await page.locator('.composer__send').click()
    await expect.poll(() => state.sent.length).toBe(1)
    expect(state.sent[0].project_ids).toEqual([firstId, secondId])
    expect(state.messages[0].meta.project_ids).toEqual([firstId, secondId])

    // Sending from the welcome view mounts a new, collapsed composer.
    await expect(page).toHaveURL(/\/chats\/alpha$/)
    await openChatConfiguration(page)
    await page.getByRole('button', { name: 'Select projects', exact: true }).click()
    await page.getByRole('checkbox', { name: 'team/nautionette', exact: true }).uncheck()
    await page.keyboard.press('Escape')
    await page.locator('.composer__input').fill('Work only on the second project')
    await page.locator('.composer__send').click()
    await expect.poll(() => state.sent.length).toBe(2)
    expect(state.sent[1].project_ids).toEqual([secondId])
    expect(state.messages[1].meta.project_ids).toEqual([secondId])
    await page.reload()
    await openChatConfiguration(page)
    await expect(page.getByRole('button', { name: 'Select projects', exact: true })).toContainText('1 project')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  })
}

test('project selection is saved before sending and follows server snapshots', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.projects = [{ id: firstId, full_name: repository.full_name, status: 'ready' }]
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  await page.getByRole('button', { name: 'Select projects', exact: true }).click()
  await page.getByRole('checkbox', { name: repository.full_name, exact: true }).check()
  await expect.poll(() => state.chat.project_ids).toEqual([firstId])
  expect(state.sent).toEqual([])
  await page.reload()
  await openChatConfiguration(page)
  await expect(page.getByRole('button', { name: 'Select projects', exact: true })).toContainText('1 project')
  state.chat.project_ids = []
  await expect(page.getByRole('button', { name: 'Select projects', exact: true })).not.toContainText('1 project')
  state.chat.project_ids = [firstId]
  await expect(page.getByRole('button', { name: 'Select projects', exact: true })).toContainText('1 project')
  await page.locator('.composer__input').fill('Keep working')
  await page.locator('.composer__send').click()
  await expect.poll(() => state.sent.length).toBe(1)
  expect(state.sent[0].project_ids).toEqual([firstId])
  expect(state.messages[0].meta.project_ids).toEqual([firstId])
})

for (const fail of [false, true]) {
  test(`sending waits for project settings and ${fail ? 'keeps the draft on failure' : 'uses the saved selection'}`, async ({ page, context }) => {
    const state = await mockProjects(context)
    state.projects = [{ id: firstId, full_name: repository.full_name, status: 'ready' }]
    let release
    const gate = new Promise((resolve) => { release = resolve })
    let saving = false
    await context.route('**/api/chats/alpha', async (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback()
      saving = true
      await gate
      if (fail) return route.fulfill({ status: 422, json: { detail: 'Project is not ready' } })
      return route.fallback()
    })
    await page.goto('/chats/alpha')
    await openChatConfiguration(page)
    await page.getByRole('button', { name: 'Select projects', exact: true }).click()
    await page.getByRole('checkbox', { name: repository.full_name, exact: true }).check()
    await expect.poll(() => saving).toBe(true)
    await page.keyboard.press('Escape')
    await page.locator('.composer__input').fill('Use the selected project')
    await page.locator('.composer__send').click()
    await expect(page.locator('.composer__send')).toBeDisabled()
    await page.locator('.composer__send').dispatchEvent('click') // Guard duplicate sends even if dispatched.
    expect(state.sent).toEqual([])
    release()
    if (fail) {
      await expect(page.getByText('Project is not ready', { exact: true })).toBeVisible()
      await expect(page.locator('.composer__input')).toHaveValue('Use the selected project')
      expect(state.sent).toEqual([])
      expect(state.chat.project_ids).toEqual([])
    } else {
      await expect.poll(() => state.sent.length).toBe(1)
      expect(state.messages[0].meta.project_ids).toEqual([firstId])
      await expect(page.locator('.composer__input')).toHaveValue('')
    }
  })
}

test('unavailable selected projects can be removed without being silently replaced', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.chat.project_ids = [firstId]
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  await page.getByRole('button', { name: 'Select projects', exact: true }).click()
  await expect(page.getByRole('checkbox')).toBeChecked()
  await expect(page.getByText('unavailable', { exact: true })).toBeVisible()
  const clear = page.getByRole('button', { name: 'Clear project selection' })
  await clear.hover()
  await expect(page.getByRole('tooltip').filter({ hasText: /^Clear selection$/ })).toBeVisible()
  await clear.click()
  await page.keyboard.press('Escape')
  await expect(page.locator('.project-picker')).toBeHidden()
  await page.locator('.composer__input').fill('No project needed')
  await page.locator('.composer__send').click()
  await expect.poll(() => state.sent.length).toBe(1)
  expect(state.sent[0].project_ids).toEqual([])
  expect(state.messages[0].meta.project_ids).toEqual([])
  await page.reload()
  await openChatConfiguration(page)
  await expect(page.getByRole('button', { name: 'Select projects', exact: true })).not.toContainText('1 project')
})

test('registered Apps can resume installation without uploading credentials', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.app = { configured: false, registered: true, public_url: 'https://nautionette.example.com' }
  await page.goto('/settings/projects?github=pending')
  await expect(page.getByText('Waiting for organization approval.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Complete installation' })).toBeEnabled()
  await expect(page.getByLabel('Public instance URL')).toHaveValue(state.app.public_url)
  await expect(page.getByLabel('Public instance URL')).toHaveAttribute('readonly', '')
  await expect(page.getByLabel('App owner')).toHaveCount(0)
  await expect(page.locator('input[type="file"]')).toHaveCount(0)
})

for (const width of [1440, 320]) {
  test(`personal and organization connections stay separate at ${width}px`, async ({ page, context }) => {
    const state = await mockProjects(context)
    state.app = { configured: true, registered: false, connections: [teamConnection, personalConnection] }
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/settings/projects')
    await expect(page.getByText('Connected', { exact: true })).toHaveCount(2)
    await expect(page.getByRole('button', { name: 'Add GitHub connection' })).toBeEnabled()
    const selector = page.getByLabel('GitHub connection', { exact: true })
    await expect(selector).toHaveValue(teamConnection.id)
    await expect(page.locator('#available-repositories')).toContainText(repository.full_name)
    await selector.selectOption(personalConnection.id)
    await expect(page.locator('#available-repositories')).toContainText(privateRepository.full_name)
    await expect(page.locator('#available-repositories')).not.toContainText(repository.full_name)
    await page.getByRole('button', { name: 'download Add', exact: true }).click()
    await expect.poll(() => state.added).toEqual([{ full_name: privateRepository.full_name, connection_id: personalConnection.id }])
    await expect(page.locator('#repositories')).toContainText('personal · personal-app (20)')
    await selector.selectOption(teamConnection.id)
    await page.getByRole('button', { name: 'download Add', exact: true }).click()
    await expect.poll(() => state.added.length).toBe(2)
    expect(state.added[1]).toEqual({ full_name: repository.full_name, connection_id: teamConnection.id })
    await expect(page.locator('#repositories')).toContainText('team · team-app (10)')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/nautionette-multiple-github-${width}.png`, fullPage: true })
    await page.reload()
    await expect(page.getByText('Connected', { exact: true })).toHaveCount(2)
    await expect(page.locator('#repositories .project-row')).toHaveCount(2)
    await selector.selectOption(personalConnection.id)
    await expect(page.getByRole('button', { name: 'check Added', exact: true })).toBeDisabled()
  })
}

test('connection switching ignores stale repository responses', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.app = { configured: true, connections: [teamConnection, personalConnection] }
  let release
  const gate = new Promise((resolve) => { release = resolve })
  let waiting = false
  await context.route('**/api/projects/repositories?**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('connection_id') === personalConnection.id) {
      waiting = true
      await gate
    }
    return route.fallback()
  })
  await page.goto('/settings/projects')
  const selector = page.getByLabel('GitHub connection', { exact: true })
  await expect(selector).toBeEnabled()
  await selector.selectOption(personalConnection.id)
  await expect.poll(() => waiting).toBe(true)
  await expect(page.locator('#available-repositories .project-row')).toHaveCount(0)
  await selector.selectOption(teamConnection.id)
  await expect(page.locator('#available-repositories')).toContainText(repository.full_name)
  const stale = page.waitForResponse((response) => response.url().includes('/api/projects/repositories?') && response.url().includes('connection_id=2-20'))
  release()
  await stale
  await expect(page.locator('#available-repositories')).not.toContainText(privateRepository.full_name)
  await page.getByRole('button', { name: 'download Add', exact: true }).click()
  await expect.poll(() => state.added).toEqual([{ full_name: repository.full_name, connection_id: teamConnection.id }])
})

test('failed downloads retry with the project connection, not the browser selection', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.app = { configured: true, connections: [teamConnection, personalConnection] }
  state.projects = [{ id: secondId, repository_id: privateRepository.id, full_name: privateRepository.full_name, connection_id: personalConnection.id, status: 'failed' }]
  await page.goto('/settings/projects')
  await expect(page.getByLabel('GitHub connection', { exact: true })).toHaveValue(teamConnection.id)
  await page.getByRole('button', { name: `Retry ${privateRepository.full_name}` }).click()
  await expect.poll(() => state.added).toEqual([{ full_name: privateRepository.full_name, connection_id: personalConnection.id }])
})

test('repository access reopens only the chosen connection', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.app = { configured: true, connections: [teamConnection, personalConnection] }
  await context.route('**/api/projects/github-app/connect', async (route) => {
    state.saved = route.request().postDataJSON()
    await route.fulfill({ status: 409, json: { detail: 'Test stops before navigating to GitHub' } })
  })
  await page.goto('/settings/projects')
  await page.getByRole('button', { name: 'Repository access for personal', exact: true }).click()
  await expect.poll(() => state.saved).toEqual({ public_url: personalConnection.public_url, connection_id: personalConnection.id })
  await expect(page.getByText('Connected', { exact: true })).toHaveCount(2)
})

test('pending registration can be resumed or bypassed to add another account', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.app = { configured: true, registered: true, public_url: teamConnection.public_url, connections: [teamConnection] }
  await context.route('**/api/projects/github-app/connect', async (route) => {
    state.saved = route.request().postDataJSON()
    await route.fulfill({ status: 409, json: { detail: 'Test stops before navigating to GitHub' } })
  })
  await page.goto('/settings/projects')
  await expect(page.getByRole('button', { name: 'Complete installation' })).toBeEnabled()
  await page.getByRole('button', { name: 'Connect another account' }).click()
  await expect(page.getByLabel('App owner')).toBeVisible()
  await page.getByRole('button', { name: 'Resume pending installation' }).click()
  await expect(page.getByLabel('App owner')).toBeHidden()
  await page.getByRole('button', { name: 'Connect another account' }).click()
  await page.getByLabel('App owner').selectOption('organization')
  await page.getByLabel('Organization', { exact: true }).fill('another-org')
  await page.getByRole('button', { name: 'Add GitHub connection' }).click()
  await expect.poll(() => state.saved).toEqual({ public_url: teamConnection.public_url, organization: 'another-org', new_app: true })
  await expect(page.getByText('Connected', { exact: true })).toHaveCount(1)
})

test('suspended connections do not prevent browsing healthy accounts', async ({ page, context }) => {
  const state = await mockProjects(context)
  state.app = { configured: true, connections: [{ ...teamConnection, configured: false, installation_status: 'suspended' }, personalConnection] }
  await page.goto('/settings/projects')
  await expect(page.getByText('Suspended', { exact: true })).toBeVisible()
  await expect(page.getByLabel('GitHub connection', { exact: true })).toHaveValue(personalConnection.id)
  await expect(page.locator('#available-repositories')).toContainText(privateRepository.full_name)
  expect(state.repositoryRequests).toEqual([personalConnection.id])
})

test('connection errors leave a retryable form', async ({ page, context }) => {
  await mockProjects(context)
  await context.route('**/api/projects/github-app/connect', (route) => route.fulfill({ status: 422, json: { detail: 'GitHub needs a public HTTPS instance URL' } }))
  await page.goto('/settings/projects')
  await page.getByLabel('Public instance URL').fill('http://localhost:8080')
  await page.getByRole('button', { name: 'Connect GitHub' }).click()
  await expect(page.getByRole('alert')).toHaveText('GitHub needs a public HTTPS instance URL')
  await expect(page.getByRole('button', { name: 'Connect GitHub' })).toBeEnabled()
})