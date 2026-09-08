import { THEME_TOKENS } from './themes.js'
import { WORKSPACE_SETTINGS } from './preferences-schema.js'

const entry = (id, label, description, keywords = '') => ({ id, label, description, keywords })

/** A settings contribution supplies one pane and its search entries. No shell edits needed. */
export const SETTINGS_SECTIONS = [
  {
    key: 'general', label: 'General', icon: 'tune', group: 'Preferences', scope: 'Instance',
    load: () => import('./components/settings/GeneralPane.vue'),
    entries: [
      entry('default-chat-agent', 'Default agent', 'Start new chats with global defaults or a saved agent.', 'profile configuration preset'),
      entry('default-model', 'Default model', 'Model inherited by agents and new chats.', 'provider AI LLM'),
      entry('default-agent', 'Default agent set', 'Container environment inherited by agents and new chats.', 'container image'),
      entry('default-reasoning', 'Default reasoning', 'Reasoning effort for the default model.', 'thinking high medium low provider'),
      entry('default-tools', 'Default tools', 'All, no, or selected MCP tools for new chats.', 'permissions capabilities access'),
      entry('default-projects', 'Default projects', 'Writable projects selected for new chats.', 'repositories git workspace'),
      entry('history', 'History budget', 'Automatic or fixed transcript character limit.', 'context tokens trimming'),
      { ...entry('server', 'Server', 'Connect this device to an instance.', 'URL address connection endpoint'), scope: 'This device' },
      { ...entry('access-token', 'Access token', 'Authentication for this device.', 'password security credentials login'), scope: 'This device' }
    ]
  },
  {
    key: 'appearance', label: 'Appearance', icon: 'palette', group: 'Preferences', scope: 'This device',
    load: () => import('./components/settings/AppearancePane.vue'),
    entries: [
      entry('themes', 'Color theme', 'Orbit, Nebula, Daylight or Sand. Make any of them yours.', 'dark light mode palette preset'),
      entry('theme-transfer', 'Import & export theme', 'Share a theme as JSON, without account data.', 'backup restore download upload'),
      ...THEME_TOKENS.map(token => entry(`token-${token.key}`, token.label, `Customize ${token.group.toLowerCase()}.`, `theme color style ${token.key} ${token.group}`))
    ]
  },
  {
    key: 'workspace', label: 'Workspace', icon: 'space_dashboard', group: 'Preferences', scope: 'This device',
    load: () => import('./components/settings/WorkspacePane.vue'),
    entries: WORKSPACE_SETTINGS.map(field => entry(field.key, field.label, field.description || field.group, field.group))
  },
  {
    key: 'agents', label: 'Agents & models', icon: 'memory', group: 'Connections', scope: 'Instance',
    load: () => import('./components/settings/AgentsPane.vue'),
    entries: [
      entry('agent-profiles', 'Saved agents', 'Create reusable configurations with model, reasoning, tools and projects.', 'profile preset defaults inherit override duplicate'),
      entry('agent-sets', 'Agent sets', 'Available container environments and image readiness.', 'container docker runtime'),
      entry('model-integrations', 'Model integrations', 'Add, configure or test a model provider.', 'API key credentials OpenAI Anthropic OpenRouter gateway'),
      entry('available-models', 'Available models', 'Models discovered through your integrations.', 'catalog vendor provider refresh')
    ]
  },
  {
    key: 'mcp', label: 'MCP servers', icon: 'extension', group: 'Connections', scope: 'Instance',
    load: () => import('./components/settings/McpPane.vue'),
    entries: [
      entry('mcp-servers', 'MCP servers', 'Add, configure and test tool connections.', 'URL endpoint credentials authentication tools'),
      entry('tool-catalog', 'Tool catalog', 'Browse or search tools by server.', 'capabilities integration actions')
    ]
  },
  {
    key: 'projects', label: 'Projects', icon: 'folder_open', group: 'Development', scope: 'Instance',
    load: () => import('./components/settings/ProjectsPane.vue'),
    entries: [
      entry('github-app', 'GitHub connection', 'Connect an account and manage repository access.', 'app installation permissions organization'),
      entry('repositories', 'Repositories', 'Manage local projects and retry downloads.', 'git clone source code files'),
      entry('available-repositories', 'Available repositories', 'Add a repository from your GitHub installation.', 'search GitHub download')
    ]
  },
  {
    key: 'git', label: 'Git authorship', icon: 'attribution', group: 'Development', scope: 'Instance',
    load: () => import('./components/settings/GitPane.vue'),
    entries: [
      entry('git-mode', 'Commit attribution', 'Choose authors, committers and co-authors.', 'mode GitHub credits'),
      entry('git-human-name', 'Your name', 'Human author identity for new commits.', 'git author'),
      entry('git-human-email', 'Your Git email', 'Use your GitHub email or noreply address.', 'privacy author'),
      entry('git-bot-name', 'Automation name', 'Name used by the automation identity.', 'bot committer'),
      entry('git-bot-email', 'Automation Git email', 'Email used by the automation identity.', 'bot committer'),
      entry('commit-preview', 'Commit preview', 'Preview the resulting attribution.', 'author trailer coauthored')
    ]
  },
  {
    key: 'automation', label: 'Automation', icon: 'account_tree', group: 'Operations', scope: 'Per workflow',
    load: () => import('./components/settings/AutomationPane.vue'),
    entries: [
      entry('workflow-settings', 'Schedules & triggers', 'Configure recurrence, timezone and inputs for each workflow.', 'hourly daily weekly monthly webhook API'),
      entry('workflow-settings', 'Workflow delivery', 'Choose one chat or a new chat for every run.', 'output results notifications disable pause enable')
    ]
  },
  {
    key: 'system', label: 'System', icon: 'monitor_heart', group: 'Operations', scope: 'Instance',
    load: () => import('./components/settings/SystemPane.vue'),
    entries: [
      entry('system-title', 'System health', 'Service status, diagnostics and runtime configuration.', 'worker temporal docker gateway version authentication'),
      entry('system-agents', 'Agent images', 'Image readiness for each agent set.', 'build containers docker'),
      entry('system-raw', 'Raw system response', 'Inspect the complete health response.', 'JSON debug diagnostics')
    ]
  },
  {
    key: 'activity', label: 'Activity', icon: 'bolt', group: 'Operations', scope: 'Instance',
    load: () => import('./components/settings/ActivityPane.vue'),
    entries: [entry('activity-log', 'Activity log', 'Recent system events.', 'history notifications audit events runs errors')]
  }
]
export const SETTINGS_GROUPS = [...new Set(SETTINGS_SECTIONS.map(section => section.group))]

const normalize = value => String(value).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
export function searchSettings (query, sections = SETTINGS_SECTIONS) {
  const words = normalize(query).split(/\s+/).filter(Boolean)
  if (!words.length) return []
  return sections.flatMap(section => section.entries.map(item => ({ ...item, section, scope: item.scope || section.scope })))
    .filter(item => {
      const text = normalize(`${item.section.label} ${item.section.group} ${item.label} ${item.description} ${item.keywords} ${item.id}`)
      return words.every(word => text.includes(word))
    })
    .sort((a, b) => Number(normalize(b.label).includes(normalize(query))) - Number(normalize(a.label).includes(normalize(query))))
}
