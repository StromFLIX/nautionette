import test from 'node:test'
import assert from 'node:assert/strict'
import { pinnedApps, appBytes, appState, appOwner } from './miniapps.js'

test('shortcuts show the five newest explicit pins, not recently edited apps or stale releases', () => {
  const apps = Array.from({ length: 8 }, (_, i) => ({ id: `app-${i}`, agent_id: i % 2 ? 'ada' : 'grace', pinned: true, active_revision: 2, pinned_revision: 2, pinned_at: i }))
  apps.push({ id: 'archived', pinned: true, active_revision: 2, pinned_revision: 2, pinned_at: 20, archived: true })
  apps.push({ id: 'stale', pinned: true, active_revision: 3, pinned_revision: 2, pinned_at: 30 })
  apps.push({ id: 'pending', pinned: true, pinned_at: 40 })
  apps.push({ id: 'unpinned', pinned: false, active_revision: 1, pinned_at: 50 })
  assert.deepEqual(pinnedApps(apps).map(app => app.id), ['app-7', 'app-6', 'app-5', 'app-4', 'app-3'])
  assert.deepEqual(pinnedApps(apps, 'ada').map(app => app.id), ['app-7', 'app-5', 'app-3', 'app-1'])
  assert.equal(apps[0].id, 'app-0')
})

test('runtime presentation never invents usage or an owning agent', () => {
  assert.equal(appBytes(undefined), '—')
  assert.equal(appBytes(0), '0 B')
  assert.equal(appBytes(1024), '1.0 KiB')
  assert.equal(appBytes(1048576), '1.0 MiB')
  assert.equal(appState({ status: 'suspended' }), 'Sleeping')
  assert.equal(appState({ status: 'running' }, true), 'Archived')
  assert.equal(appOwner({ agent_id: 'gone' }, []), 'Workspace')
  assert.equal(appOwner({ agent_id: 'ada' }, [{ id: 'ada', name: 'Ada' }]), 'Ada')
})
