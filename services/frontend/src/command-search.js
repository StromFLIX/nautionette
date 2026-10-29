import { SETTINGS_SECTIONS, searchSettings } from './settings-registry.js'

const normalize = value => String(value ?? '').toLocaleLowerCase().trim()

// Search the full chat list, independently of sidebar grouping/activity filters.
export function searchChats (chats, query) {
  const words = normalize(query).split(/\s+/).filter(Boolean)
  const matches = text => words.every(word => normalize(text).includes(word))
  return chats.filter(chat => matches(`${chat.title || ''} ${chat.last_message?.preview || ''}`))
    .sort((a, b) => Number(matches(b.title)) - Number(matches(a.title)))
}

export function commandSearchGroups (chats, query, scope = 'all') {
  const text = query.trim()
  const chatResults = searchChats(chats, text)
  const sections = !text || /^(settings|preferences|einstellungen)$/i.test(text)
  const settingResults = sections
    ? SETTINGS_SECTIONS.map(section => ({
        id: '', label: section.label, description: `${section.group} · ${section.scope}`, section
      }))
    : searchSettings(text)
  return [
    {
      key: 'chats', label: text ? 'Chats' : 'Recent chats', count: chatResults.length,
      results: chatResults.slice(0, text ? 30 : 8).map(chat => ({
        key: `chat-${chat.id}`, label: chat.title || 'Untitled chat',
        description: chat.last_message?.preview || 'Open conversation', icon: 'chat_bubble_outline',
        to: { path: `/chats/${encodeURIComponent(chat.id)}` }
      }))
    },
    {
      key: 'settings', label: 'Settings', count: settingResults.length,
      results: settingResults.slice(0, 30).map((item, index) => ({
        key: `setting-${item.section.key}-${item.id}-${index}`, label: item.label,
        description: sections ? item.description : `${item.section.label} · ${item.description}`,
        icon: item.section.icon,
        to: { path: `/settings/${item.section.key}`, ...(item.id ? { hash: `#${item.id}` } : {}) }
      }))
    }
  ].filter(group => (scope === 'all' || scope === group.key) && group.results.length)
}
