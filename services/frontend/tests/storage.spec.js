import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

const MiB = 1024 ** 2, GiB = 1024 ** 3
async function mockStorage (context) {
  await mockAgents(context)
  const now = Date.now() / 1000
  const row = (id, revision, size, days) => ({
    output_id: id.repeat(32), revision, title: id === 'a' ? 'Drone body' : 'Weekly report',
    chat_id: 'alpha', chat_title: 'Design compact drone', created_at: now - days * 86400,
    is_current: revision === (id === 'a' ? 2 : 1), current_revision: id === 'a' ? 2 : 1,
    total_bytes: size, original_bytes: size - 100, preview_bytes: 100, file_count: 2,
    files: [{ id: 'f'.repeat(32), name: id === 'a' ? 'drone.dxf' : 'report.pdf', size: size - 100 }]
  })
  const state = {
    writes: [], previews: [], deletes: [], outage: false, saveFailure: false, deleteFailure: false,
    rows: [row('a', 1, 4 * MiB, 90), row('a', 2, 8 * MiB, 45), row('b', 1, MiB, 1)],
    policy: { global_limit_bytes: 10 * GiB, chat_limit_bytes: 256 * MiB, max_file_bytes: 20 * MiB, max_publication_bytes: 40 * MiB, minimum_free_bytes: 512 * MiB, retention_days: 0, retention_keep_latest: true },
    maintenance: { errors: [{ code: 'output_storage_unavailable', message: 'A legacy copy failed verification. Original bytes are retained.' }] }
  }
  let plan
  await context.route('**/api/settings/storage**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    const body = request.method() === 'GET' ? {} : request.postDataJSON()
    const reply = (json, status = 200) => route.fulfill({ json, status })
    if (path.endsWith('/outputs')) {
      const query = url.searchParams.get('query')?.toLowerCase() || ''
      const rows = state.rows.filter(row => `${row.title} ${row.chat_title} ${row.files[0].name}`.toLowerCase().includes(query))
      const sort = url.searchParams.get('sort')
      rows.sort(sort === 'oldest' ? (a, b) => a.created_at - b.created_at : sort === 'newest' ? (a, b) => b.created_at - a.created_at : (a, b) => b.total_bytes - a.total_bytes)
      const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 50)
      return reply({ outputs: rows.slice(offset, offset + limit), total: rows.length, offset, limit, has_more: offset + limit < rows.length })
    }
    if (path.endsWith('/cleanup/preview')) {
      state.previews.push(body)
      const revisions = state.rows.filter(row => body.revisions ? body.revisions.some(item => item.output_id === row.output_id && item.revision === row.revision) : body.output_ids ? body.output_ids.includes(row.output_id) : row.created_at < now - body.older_than_days * 86400 && (!body.keep_latest || !row.is_current))
      plan = { plan_id: 'c'.repeat(32), revisions: structuredClone(revisions), revision_count: revisions.length, output_count: new Set(revisions.map(row => row.output_id)).size, bytes: revisions.reduce((sum, row) => sum + row.total_bytes, 0), includes_current: revisions.some(row => row.is_current) }
      return reply(plan)
    }
    if (path.endsWith('/cleanup')) {
      state.deletes.push(body)
      if (state.deleteFailure) return reply({ detail: { message: 'Cleanup preview expired. Review it again.', code: 'expired' } }, 409)
      state.rows = state.rows.filter(row => !plan.revisions.some(item => item.output_id === row.output_id && item.revision === row.revision))
      return reply({ deleted_revisions: plan.revision_count, deleted_bytes: plan.bytes, collection: { reclaimed_bytes: plan.bytes, errors: [] } })
    }
    if (path.endsWith('/maintenance')) {
      state.maintenance.errors = []
      return reply({ migrated_files: 2, deleted_revisions: 0, errors: [] })
    }
    if (path.endsWith('/compact')) return reply({ ok: true })
    if (request.method() === 'PUT') {
      state.writes.push(body)
      if (state.saveFailure) return reply({ detail: { code: 'invalid_policy', message: 'File limit cannot exceed the publication limit.', settings_url: '/settings/storage' } }, 422)
      state.policy = body
      return reply({ policy: state.policy })
    }
    if (state.outage) return reply({ detail: 'Storage status unavailable' }, 503)
    const original = state.rows.reduce((sum, row) => sum + row.original_bytes, 0), previews = state.rows.reduce((sum, row) => sum + row.preview_bytes, 0)
    return reply({
      provider: 'S3-compatible (MaxIO in Compose)', endpoint: 'http://maxio:9000', bucket: 'nautionette-outputs', region: 'us-east-1', health: { status: 'ok' },
      policy: state.policy, usage: { original_bytes: original, preview_bytes: previews, used_bytes: original + previews, charged_bytes: original + previews, reserved_bytes: 0, pending_deletion_bytes: 0, temporary_bytes: 0, output_count: 2, revision_count: state.rows.length, file_count: state.rows.length * 2, legacy_file_count: 2 },
      filesystems: [{ label: 'MaxIO backing filesystem', available: true, total_bytes: 100 * GiB, used_bytes: 60 * GiB, available_bytes: 40 * GiB }, { label: 'Backend data filesystem', available: false, reason: 'Not mounted' }],
      database: { bytes: 20 * MiB, reclaimable_bytes: 10 * MiB }, maintenance: state.maintenance,
      maxio_notice: 'MaxIO is under active upstream development and is not yet recommended for production. Back up its volume and the metadata database together.'
    })
  })
  return state
}

for (const width of [1440, 320]) {
  test(`Storage inventories every revision, distinguishes disk capacity from quotas, and saves limits at ${width}px`, async ({ page, context }) => {
    const state = await mockStorage(context)
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/storage')
    await expect(page.getByRole('heading', { name: 'Storage', exact: true })).toBeVisible()
    await expect(page.getByRole('article')).toHaveCount(3)
    await expect(page.getByRole('article').first()).toContainText('Revision 2')
    await expect(page.getByRole('region', { name: 'MaxIO backing filesystem' })).toContainText('40 GiB')
    await expect(page.getByRole('region', { name: 'Backend data filesystem' })).toContainText('Capacity unavailable')
    await expect(page.getByText('Increasing an output quota does not create physical disk space.', { exact: false })).toBeVisible()
    await expect(page.getByRole('checkbox', { name: 'Enable automatic permanent cleanup' })).not.toBeChecked()
    await page.getByLabel('Instance output quota (GiB)', { exact: true }).fill('20')
    await page.getByLabel('Maximum original file size (MiB)', { exact: true }).fill('32')
    await page.getByRole('button', { name: 'Save storage settings', exact: true }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Storage settings saved' })).toBeVisible()
    expect(state.writes.at(-1).global_limit_bytes).toBe(20 * GiB)
    expect(state.writes.at(-1).max_file_bytes).toBe(32 * MiB)
    expect(state.writes.at(-1).retention_days).toBe(0)
    expect(state.deletes).toHaveLength(0)
    expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    await page.getByRole('heading', { name: 'Storage', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: `test-results/storage-${width}.png` })
    await page.getByRole('combobox', { name: 'Sort outputs', exact: true }).selectOption('oldest')
    await expect(page.getByRole('article').first()).toContainText('Revision 1')
    await page.getByLabel('Search outputs', { exact: true }).fill('report.pdf')
    await page.getByRole('button', { name: 'Search', exact: true }).click()
    await expect(page.getByRole('article')).toHaveCount(1)
    await expect(page.getByRole('article')).toContainText('Weekly report')
  })
}

test('selected and age-based cleanup require a preview, support cancellation, and preserve new revisions', async ({ page, context }) => {
  const state = await mockStorage(context)
  await page.goto('/settings/storage')
  await page.getByRole('checkbox', { name: 'Select Drone body revision 1', exact: true }).check()
  await page.getByRole('button', { name: 'Delete selected revisions (1)', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('1 revisions from 1 outputs')
  expect(state.deletes).toHaveLength(0)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(state.deletes).toHaveLength(0)
  await page.getByRole('button', { name: 'Delete selected revisions (1)', exact: true }).click()
  await dialog.getByRole('button', { name: 'Permanently delete', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(2)
  expect(state.deletes[0]).toEqual({ plan_id: 'c'.repeat(32), confirm: true })
  await page.getByLabel('Older than (days)', { exact: true }).fill('30')
  await page.getByRole('checkbox', { name: 'Keep current revisions', exact: true }).uncheck()
  await page.getByRole('button', { name: 'Preview age cleanup', exact: true }).click()
  await expect(dialog).toContainText('This includes current revisions')
  expect(state.previews.at(-1)).toEqual({ older_than_days: 30, keep_latest: false })
  await dialog.getByRole('button', { name: 'Permanently delete', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article')).toContainText('Weekly report')
})

test('whole-output deletion, rejected confirmations and opt-in retention remain explicit', async ({ page, context }) => {
  const state = await mockStorage(context)
  await page.goto('/settings/storage')
  await page.getByRole('article', { name: 'Drone body, revision 2' }).getByRole('button', { name: 'Delete whole output', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('2 revisions from 1 outputs')
  state.deleteFailure = true
  await dialog.getByRole('button', { name: 'Permanently delete', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('Cleanup preview expired')
  expect(state.rows).toHaveLength(3)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Enable automatic permanent cleanup', exact: true }).check()
  await page.getByLabel('Remove revisions older than (days)', { exact: true }).fill('90')
  await page.getByRole('button', { name: 'Save storage settings', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Storage settings saved' })).toBeVisible()
  expect(state.writes.at(-1).retention_days).toBe(90)
  expect(state.writes.at(-1).retention_keep_latest).toBe(true)
})

test('status and maintenance failures are retryable; structured save errors retain drafts', async ({ page, context }) => {
  const state = await mockStorage(context)
  state.outage = true
  await page.goto('/settings/storage')
  await expect(page.getByRole('alert').first()).toContainText('Storage status unavailable')
  state.outage = false
  await page.getByRole('button', { name: 'Retry loading', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Migration and maintenance' })).toContainText('Original bytes are retained')
  await page.getByRole('button', { name: 'Retry migration & cleanup', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Migration and maintenance' }).getByRole('alert')).toHaveCount(0)
  state.saveFailure = true
  await page.getByLabel('Maximum original file size (MiB)', { exact: true }).fill('48')
  await page.getByRole('button', { name: 'Save storage settings', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('File limit cannot exceed')
  await expect(page.getByLabel('Maximum original file size (MiB)', { exact: true })).toHaveValue('48')
  expect(state.policy.max_file_bytes).toBe(20 * MiB)
})

test('inventory pagination keeps selected revisions across pages and previews their exact total', async ({ page, context }) => {
  const state = await mockStorage(context)
  const sample = state.rows[0]
  state.rows = Array.from({ length: 55 }, (_, index) => ({ ...sample, output_id: index.toString(16).padStart(32, '0'), title: `Output ${index + 1}` }))
  await page.goto('/settings/storage')
  await expect(page.getByRole('article')).toHaveCount(50)
  await page.getByRole('checkbox', { name: 'Select this page', exact: true }).check()
  await page.getByRole('button', { name: 'Next page', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(5)
  await expect(page.getByRole('button', { name: 'Delete selected revisions (50)', exact: true })).toBeEnabled()
  await page.getByRole('checkbox', { name: 'Select this page', exact: true }).check()
  await page.getByRole('button', { name: 'Delete selected revisions (55)', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('55 revisions from 55 outputs')
  expect(state.previews.at(-1).revisions).toHaveLength(55)
  expect(state.deletes).toHaveLength(0)
})

test('settings search discovers Storage and its deep links before opening the page', async ({ page, context }) => {
  await mockStorage(context)
  await page.goto('/settings/general')
  await page.getByRole('searchbox', { name: 'Search settings', exact: true }).fill('storage')
  await page.getByRole('link').filter({ hasText: 'Storage usage' }).click()
  await expect(page).toHaveURL(/\/settings\/storage#storage-overview/)
  await expect(page.getByRole('heading', { name: 'Storage', exact: true })).toBeVisible()
})
