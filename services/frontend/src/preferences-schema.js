import { THEMES, sanitizeOverrides } from './themes.js'

export const PREFERENCES_KEY = 'nautionette.preferences.v1'
export const WORKSPACE_SETTINGS = [
  { key: 'interfaceSize', group: 'Layout', label: 'Interface size', description: 'Scale text and controls on this device, including phones, without browser zoom.', type: 'select', value: 125, options: [[100, '100%'], [110, '110%'], [125, '125% (default)'], [150, '150%']] },
  { key: 'density', group: 'Layout', label: 'Density', description: 'Spacing in navigation and lists.', type: 'select', value: 'comfortable', options: [['comfortable', 'Comfortable'], ['compact', 'Compact']] },
  { key: 'sideWidth', group: 'Layout', label: 'Sidebar width', description: 'You can also drag the sidebar edge.', type: 'number', value: 320, min: 260, max: 560, step: 10, unit: 'px' },
  { key: 'motion', group: 'Layout', label: 'Motion', description: 'System accessibility preferences are always respected.', type: 'select', value: 'system', options: [['system', 'Follow system'], ['reduced', 'Reduce motion']] },
  { key: 'sendShortcut', group: 'Chat', label: 'Send shortcut', description: 'Shift + Enter always adds a new line.', type: 'select', value: 'enter', options: [['enter', 'Enter'], ['modifier-enter', '⌘ / Ctrl + Enter']] },
  { key: 'composerExpanded', group: 'Chat', label: 'Show chat configuration', description: 'Keep agents, reasoning, tools and projects expanded.', type: 'boolean', value: false },
  { key: 'showTimestamps', group: 'Chat', label: 'Message timestamps', description: 'Delivery errors and queued messages stay visible.', type: 'boolean', value: true },
  { key: 'starterPrompts', group: 'Chat', label: 'Starter prompts', description: 'Show suggestions on the new-chat screen.', type: 'boolean', value: true },
  { key: 'chatGroupBy', group: 'Chat list', label: 'Group chats by', type: 'select', value: 'none', options: [['none', 'No grouping'], ['project', 'Project'], ['model', 'Model'], ['internet', 'Internet access']] },
  { key: 'chatActiveMinutes', group: 'Chat list', label: 'Activity window', description: 'Unread replies and requests for approval are never hidden.', type: 'select', value: 0, options: [[60, 'Last hour'], [1440, 'Last 24 hours'], [10080, 'Last 7 days'], [43200, 'Last 30 days'], [0, 'All chats']] },
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
