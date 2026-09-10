import { THEMES, sanitizeOverrides } from './themes.js'

export const PREFERENCES_KEY = 'nautionette.preferences.v1'
export const WORKSPACE_SETTINGS = [
  { key: 'interfaceSize', group: 'Layout', label: 'Interface size', description: 'Scale text and controls on this device, including phones, without browser zoom.', type: 'select', value: 125, options: [[100, '100%'], [110, '110%'], [125, '125% (default)'], [150, '150%']] },
  { key: 'density', group: 'Layout', label: 'Density', description: 'Spacing in navigation and lists.', type: 'select', value: 'comfortable', options: [['comfortable', 'Comfortable'], ['compact', 'Compact']] },
  { key: 'sideWidth', group: 'Layout', label: 'Sidebar width', description: 'You can also drag the sidebar edge.', type: 'number', value: 320, min: 260, max: 560, step: 10, unit: 'px' },
  { key: 'sideCollapsed', group: 'Layout', label: 'Collapse sidebar', description: 'Give the conversation more space on desktop. Click Chats, Workflows or Runs in the navigation to reopen its list.', type: 'boolean', value: false },
  { key: 'motion', group: 'Animations', label: 'Motion', description: 'Reduce all motion, or choose individual animations below. System accessibility preferences are always respected.', type: 'select', value: 'system', options: [['system', 'Follow system'], ['reduced', 'Reduce motion']] },
  { key: 'chatListAnimation', group: 'Animations', label: 'Chat list animation', description: 'Animate the progress ring around running chats. Off keeps a static indicator.', type: 'boolean', value: true },
  { key: 'messageWindowAnimation', group: 'Animations', label: 'Message window animation', description: 'Animate the running light around the message composer. Off keeps the highlighted border.', type: 'boolean', value: true },
  { key: 'toolIndicatorAnimation', group: 'Animations', label: 'Tool-call indicator animation', description: 'Animate the summary octagon and running-tool dots. Off keeps static progress indicators.', type: 'boolean', value: true },
  { key: 'sendShortcut', group: 'Chat', label: 'Send shortcut', description: 'Shift + Enter always adds a new line.', type: 'select', value: 'enter', options: [['enter', 'Enter'], ['modifier-enter', '⌘ / Ctrl + Enter']] },
  { key: 'newChatSettings', group: 'Chat', label: 'New chat settings', description: 'Reuse the last model, agent, environment, reasoning, tools, projects and extensions used on this device, or start from your configured defaults. Defaults apply until you have used a chat. Configuration controls always start closed.', type: 'select', value: 'last', options: [['last', 'Always use last settings'], ['defaults', 'Always use defaults']] },
  { key: 'showTimestamps', group: 'Chat', label: 'Message timestamps', description: 'Delivery errors and queued messages stay visible.', type: 'boolean', value: true },
  { key: 'starterPrompts', group: 'Chat', label: 'Starter prompts', description: 'Show suggestions on the new-chat screen.', type: 'boolean', value: true },
  { key: 'chatGroupBy', group: 'Chat list', label: 'Group chats by', type: 'select', value: 'none', options: [['none', 'No grouping'], ['project', 'Project'], ['model', 'Model'], ['internet', 'Internet access']] },
  { key: 'chatOrderBy', group: 'Chat list', label: 'Chat order', description: 'Sort newest first by all activity (including tools, thinking and progress), user and agent messages, or only user messages.', type: 'select', value: 'activity', options: [['activity', 'All activity'], ['messages', 'Messages only'], ['user', 'User messages only']] },
  { key: 'chatActiveMinutes', group: 'Chat list', label: 'Activity window', description: 'Unread replies, running chats and requests for approval are never hidden by this window.', type: 'select', value: 0, options: [[60, 'Last hour'], [1440, 'Last 24 hours'], [10080, 'Last 7 days'], [43200, 'Last 30 days'], [0, 'All chats']] },
  { key: 'chatKeepSelectedVisible', group: 'Chat list', label: 'Keep selected chat visible', description: 'Keep the open chat in the list even when it is read and outside the activity window. Search still applies.', type: 'boolean', value: true },
  { key: 'chatSelectionGraceSeconds', group: 'Chat list', label: 'Chat visibility grace period', description: 'With Keep selected chat visible enabled, retain each chat after leaving it so you can return easily. Use 0 for no grace period.', type: 'number', value: 60, min: 0, max: 3600, step: 1, unit: 'seconds' },
  { key: 'flowDirection', group: 'Code & workflows', label: 'Flow direction', description: 'Default orientation for workflow graphs.', type: 'select', value: 'TB', options: [['TB', 'Top to bottom'], ['LR', 'Left to right']] },
  { key: 'codeWrap', group: 'Code & workflows', label: 'Wrap source code', description: 'Wrap long lines in the code viewer.', type: 'boolean', value: false }
]
export const WORKSPACE_GROUPS = [...new Set(WORKSPACE_SETTINGS.map(setting => setting.group))]
export const workspaceDefaults = () => Object.fromEntries(WORKSPACE_SETTINGS.map(setting => [setting.key, setting.value]))
export const preferenceDefaults = () => ({ theme: 'orbit', overrides: {}, ...workspaceDefaults() })

export function validPreference (key, value) {
  const field = WORKSPACE_SETTINGS.find(setting => setting.key === key)
  if (!field) return false
  if (field.type === 'boolean') return typeof value === 'boolean'
  if (field.type === 'number') return typeof value === 'number' && Number.isFinite(value) && value >= field.min && value <= field.max
  return field.options.some(([option]) => option === value)
}

export function sanitizePreferences (data) {
  const result = preferenceDefaults()
  if (!data || typeof data !== 'object' || Array.isArray(data)) return result
  if (THEMES.some(theme => theme.id === data.theme)) result.theme = data.theme
  for (const theme of THEMES) {
    const overrides = sanitizeOverrides(data.overrides?.[theme.id])
    if (Object.keys(overrides).length) result.overrides[theme.id] = overrides
  }
  for (const field of WORKSPACE_SETTINGS) {
    if (validPreference(field.key, data[field.key])) result[field.key] = data[field.key]
  }
  return result
}
