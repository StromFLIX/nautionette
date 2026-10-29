/** Missing live data is unknown, never a cached or invented zero. */
export function agentActivity (activity, agentId) {
  if (!activity) return null
  return agentId ? (activity.agents?.[agentId] || { running: 0, queued: 0 }) : activity
}

export function activityLabel (counts) {
  if (!counts) return 'Activity unavailable'
  return `${counts.running} ${counts.running === 1 ? 'agent' : 'agents'} running · ${counts.queued} ${counts.queued === 1 ? 'message' : 'messages'} waiting`
}
