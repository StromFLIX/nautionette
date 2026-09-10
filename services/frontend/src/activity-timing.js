/** Compact elapsed time, with no made-up value for older, unmeasured calls. */
export function compactDuration (ms) {
  if (!Number.isFinite(ms) || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${Number((ms / 1000).toFixed(1))}s`
  const seconds = Math.round(ms / 1000)
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

export function activityBreakdown (timing, live = false, now = Date.now()) {
  if (!timing) return []
  return [['tools', 'Tools'], ['thinking', 'Thinking'], ['reply', 'Reply'], ['other', 'Other']]
    .flatMap(([key, label]) => {
      let ms = timing[`${key}_ms`]
      if (!Number.isFinite(ms) || ms < 0) return []
      if (live && timing.active === key && Number.isFinite(timing.updated_at)) {
        ms += Math.max(0, now - timing.updated_at * 1000)
      }
      // No thinking events means no measured thinking, not an inferred idle phase.
      return ms > 0 ? [{ key, label, duration: compactDuration(ms) }] : []
    })
}
