import { byChatStart, chatStartedAt } from './chat-order.js'

export const CHAT_GROUP_OPTIONS = [
  ['none', 'No grouping'], ['date', 'Date'], ['activity', 'Activity'], ['project', 'Project']
]

function dateGroup (chat) {
  const startedAt = chatStartedAt(chat)
  if (!startedAt) return { key: '__unknown_date__', label: 'Unknown date', rank: 1 }
  const date = new Date(startedAt * 1000)
  // Use local calendar dates, not UTC slices or rolling 24-hour windows. Absolute
  // labels stay accurate across midnight without moving chats between buckets.
  return {
    key: `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`,
    label: date.toLocaleDateString([], { day: 'numeric', month: 'long', year: 'numeric' }),
    rank: 0
  }
}

function activityGroup (chat) {
  if (chat.answering) return { key: 'active', label: 'Active', rank: 0 }
  if (chat.unread) return { key: 'unread', label: 'Unread', rank: 1 }
  return { key: 'inactive', label: 'Inactive', rank: 2 }
}

/** Group and sort before visibility filtering, so activity cannot reorder projects. */
export function groupChats (chats, groupBy, projects = []) {
  const projectNames = new Map(projects.map(project => [project.id, project.full_name]))
  const groups = new Map()
  for (const chat of [...chats].sort(byChatStart)) {
    let memberships
    if (groupBy === 'date') memberships = [dateGroup(chat)]
    else if (groupBy === 'activity') memberships = [activityGroup(chat)]
    else if (groupBy === 'project') {
      const ids = Array.isArray(chat.project_ids) ? [...new Set(chat.project_ids)] : []
      memberships = ids.length
        ? ids.map(id => ({ key: id, label: projectNames.get(id) || id, rank: 0 }))
        : [{ key: '__none__', label: 'No project', rank: 1 }]
    } else memberships = [{ key: '__all__', label: 'Chats', rank: 0 }]
    for (const membership of memberships) {
      if (!groups.has(membership.key)) groups.set(membership.key, { ...membership, chats: [] })
      groups.get(membership.key).chats.push(chat)
    }
  }
  return [...groups.values()].sort((a, b) =>
    a.rank - b.rank || byChatStart(a.chats[0], b.chats[0]) || String(a.label).localeCompare(String(b.label)) || String(a.key).localeCompare(String(b.key)))
}
