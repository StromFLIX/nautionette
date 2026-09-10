import { CONFIG_KEYS, copyConfig } from './agent-config.js'

export const LAST_CHAT_SETTINGS_KEY = 'nautionette.last-chat-settings.v1'
const object = value => value && typeof value === 'object' && !Array.isArray(value)
const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string')

/** Only reusable configuration, never messages, chat IDs or internet approval. */
export function chatSettingsSnapshot (value) {
  if (!object(value) || typeof value.model !== 'string' || typeof value.agent_set !== 'string' ||
      !(value.tools === null || strings(value.tools)) || !strings(value.project_ids) ||
      !(value.reasoning_effort == null || typeof value.reasoning_effort === 'string') ||
      !(value.agent_id == null || typeof value.agent_id === 'string') ||
      !(value.packages == null || strings(value.packages))) return null
  const config = { agent_id: value.agent_id ?? null,
    ...Object.fromEntries(CONFIG_KEYS.map(key => [key, value[key]])),
    reasoning_effort: value.reasoning_effort ?? null, packages: value.packages ?? [] }
  // Extension revision IDs pin their configuration. Never share mutable selections with a draft.
  return copyConfig(config)
}

export function reusableChatSettings (catalog, saved) {
  const config = chatSettingsSnapshot(saved)
  if (!config) return null
  // A removed profile must not block creating a chat. Keep its explicit settings,
  // including restrictions, rather than silently replacing them with global defaults.
  if (config.agent_id && !(catalog.agents || []).some(agent => agent.id === config.agent_id)) config.agent_id = null
  return config
}

/** Device-local history, isolated by backend just like the offline chat cache. */
export function createChatSettings ({ scope, storage = () => localStorage, onError = () => {} }) {
  const memory = new Map()
  const unsaved = new Set()
  const key = scope => `${LAST_CHAT_SETTINGS_KEY}:${encodeURIComponent(scope)}`
  function load (target = scope()) {
    if (unsaved.has(target)) return chatSettingsSnapshot(memory.get(target))
    try {
      const raw = storage().getItem(key(target))
      const config = raw ? chatSettingsSnapshot(JSON.parse(raw)) : null
      memory.set(target, config)
      return config
    } catch {
      onError('Last chat settings could not be loaded on this device.')
      return chatSettingsSnapshot(memory.get(target))
    }
  }
  function remember (value, target = scope()) {
    const config = chatSettingsSnapshot(value)
    if (!config) return
    memory.set(target, config)
    try {
      storage().setItem(key(target), JSON.stringify(config))
      unsaved.delete(target)
    } catch {
      unsaved.add(target)
      onError('Last chat settings could not be saved. They apply for this session only.')
    }
  }
  return {
    load, remember,
    forNewChat: (catalog, mode, target = scope()) => mode === 'last' ? reusableChatSettings(catalog, load(target)) || {} : {}
  }
}
