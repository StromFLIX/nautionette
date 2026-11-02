/** Stable, release-specific shortcuts; metadata refreshes never reorder pins. */
export function pinnedApps (apps, agentId) {
  return apps.filter(app => app.pinned && !app.archived && app.active_revision &&
    (!app.pinned_revision || app.pinned_revision === app.active_revision) && (!agentId || app.agent_id === agentId))
    .sort((a, b) => (b.pinned_at || 0) - (a.pinned_at || 0) || a.id.localeCompare(b.id))
    .slice(0, 5)
}

export function appLocation (app, agentId) {
  return { path: `/apps/${app.id}`, query: agentId ? { agent: agentId } : {} }
}

export function appOwner (app, agents = []) {
  return agents.find(agent => agent.id === app.agent_id)?.name || 'Workspace'
}

export function appState (runtime, archived = false) {
  if (archived) return 'Archived'
  return ({ 'frontend-only': 'Frontend only', running: 'Running', suspended: 'Sleeping', absent: 'Not ready', building: 'Building', unavailable: 'Unavailable' })[runtime?.status] || 'Not sampled'
}

export function appBytes (value) {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / 1024 / 1024).toFixed(1)} MiB`
}
