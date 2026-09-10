<template>
  <h2 class="settings__title">MCP servers</h2>

  <div id="mcp-servers" class="setting">
    <div class="row integration-head">
      <div class="setting__label grow">Servers</div>
      <button class="btn btn--sm btn--outline" :disabled="loading" @click="refresh">
        {{ loading ? 'Checking…' : 'Refresh' }}
      </button>
      <button
        class="btn btn--sm btn--primary" :disabled="!state.writable || adding" @click="startAdd"
      >
        Add server
      </button>
    </div>
    <p class="caption dim">
      Connected tools are available in every chat.
    </p>

    <div v-if="adding" class="integration-add">
      <strong>{{ editing ? `Configure ${editing}` : 'Add a server' }}</strong>
      <label class="integration-field">
        <span class="caption">Transport</span>
        <select v-model="transport" class="field" aria-label="Transport" :disabled="Boolean(busy)">
          <option value="http">HTTP — connect to a server</option>
          <option value="stdio">stdio — run inside agentgateway</option>
        </select>
      </label>
      <DeclaredField
        v-for="field in activeFields" :key="field.key" v-model="draft[field.key]"
        :field="field" :credential="editingCredential"
        :disabled="Boolean(editing) && field.key === 'name'"
      />
      <template v-if="transport === 'stdio'">
        <label class="integration-field">
          <span class="caption">Arguments (JSON array)</span>
          <textarea v-model="argsText" class="field mono" rows="3" spellcheck="false"
            placeholder='["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"]' />
          <span v-if="parsedArgs === null" class="caption field-hint--bad" role="alert">Enter a JSON array of strings (up to 256 arguments, no NUL characters).</span>
          <span class="caption dim">Passed as separate arguments, without a shell. Use environment variables for secrets.</span>
        </label>
        <div class="integration-field">
          <span class="caption">Environment variables</span>
          <div v-for="(variable, index) in environment" :key="variable.id" class="mcp-env-row">
            <input v-model="variable.key" class="field mono" :aria-label="`Variable ${index + 1} name`"
              placeholder="BRAVE_API_KEY" :disabled="variable.stored" autocomplete="off" />
            <input v-model="variable.value" class="field mono" type="password" :aria-label="`Variable ${index + 1} value`"
              :placeholder="variable.stored ? 'stored — leave blank to keep' : 'Value (literal)'" autocomplete="new-password" />
            <button class="btn btn--sm" :aria-label="`Remove variable ${index + 1}`" @click="environment.splice(index, 1)">Remove</button>
          </div>
          <button class="btn btn--sm btn--outline" @click="addVariable">Add variable</button>
          <span v-if="environmentError" class="caption field-hint--bad" role="alert">{{ environmentError }}</span>
          <span class="caption dim">Values are stored in agentgateway and never returned. Remove a row to delete it. Values are literal; $VARIABLE is not expanded.</span>
        </div>
        <p class="caption">
          Only run trusted packages: these processes share agentgateway's container, network and data volume.
          Node/npm/npx and Python/uvx are available. Other runtimes must be installed in the image.
          Use stdio mode, not an HTTP listener, and pin package versions.
        </p>
      </template>
      <p class="caption dim">
        {{ transport === 'stdio'
          ? 'The process is tested on an isolated gateway route before saving. First-time package downloads can take up to 90 seconds.'
          : 'Connection is checked before saving to protect the shared tool endpoint.' }}
      </p>
      <div class="row integration-actions">
        <button class="btn btn--sm" @click="cancelAdd">Cancel</button>
        <button
          class="btn btn--sm btn--primary" :disabled="Boolean(busy) || !draftValid" @click="save"
        >
          {{ busy ? 'Checking…' : editing ? 'Save server' : 'Add server' }}
        </button>
      </div>
    </div>

    <p v-if="!state.servers.length && !loading" class="caption dim integration-empty">
      No MCP server is configured.
    </p>
    <div v-for="server in state.servers" :key="server.name" class="integration-card">
      <div class="row">
        <span class="material-icons integration-card__icon">handyman</span>
        <strong class="grow">{{ server.name }}</strong>
        <span class="chip" :class="server.tool_count ? 'chip--success' : 'chip--warning'">
          {{ server.tool_count }} tools
        </span>
      </div>
      <details class="integration-details">
        <summary>Connection details</summary>
        <div class="integration-meta caption dim">
          <span class="caption">{{ server.transport === 'stdio' ? 'stdio · runs in agentgateway' : 'HTTP' }}</span>
          <template v-if="server.transport === 'stdio'">
            <span class="mono mcp-command">{{ server.command }} {{ JSON.stringify(server.args || []) }}</span>
            <span>Stored environment: {{ Object.keys(server.env || {}).join(', ') || 'none' }}</span>
          </template>
          <template v-else>
            <span class="mono truncate">{{ server.url }}</span>
            <span>{{ credentialLabel(server.credential) }}</span>
          </template>
          <span v-if="!server.managed">from the gateway config file</span>
        </div>
      </details>
      <div v-if="tests[server.name]" class="line">
        <span class="dot" :class="tests[server.name].ok ? 'dot--ok' : 'dot--bad'" />
        <span class="caption grow">{{ tests[server.name].message }}</span>
        <span v-if="tests[server.name].status" class="caption dim">
          HTTP {{ tests[server.name].status }}
        </span>
      </div>
      <div v-if="server.managed" class="row integration-actions">
        <button class="btn btn--sm" :disabled="Boolean(busy)" @click="edit(server)">
          Configure
        </button>
        <button class="btn btn--sm btn--outline" :disabled="Boolean(busy)" @click="test(server)">
          {{ busy === server.name ? 'Testing…' : 'Test' }}
        </button>
        <span class="grow" />
        <button class="btn btn--sm btn--danger" :disabled="Boolean(busy)" @click="remove(server)">
          Remove
        </button>
      </div>
    </div>
  </div>

  <div id="tool-catalog" class="setting">
    <div class="setting__label">Tool catalog</div>
    <input v-model="toolQuery" class="field" type="search" placeholder="Search tools…" aria-label="Search tool catalog" />
    <p v-if="!toolGroups.length" class="caption dim integration-empty">{{ toolQuery ? 'No matching tools.' : 'No MCP server answered.' }}</p>
    <details v-for="group in toolGroups" :key="group.name" class="settings-disclosure model-catalog" :open="Boolean(toolQuery)">
      <summary>
        <span class="grow truncate">{{ group.name }}</span>
        <span v-if="group.host" class="caption dim mono truncate">{{ group.host }}</span>
        <span class="chip chip--success">{{ group.tools.length }} tools</span>
      </summary>
      <div v-for="tool in group.tools" :key="tool.name" class="line line--stacked line--nested">
        <span class="mono">{{ tool.name }}</span>
        <span v-if="tool.description" class="caption dim">{{ tool.description }}</span>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import DeclaredField from './DeclaredField.vue'
import { credentialLabel, draftIsValid, resetDraft } from './fields'
import { actions, store } from '../../store'
import { api } from '../../api'

const $q = useQuasar()

const state = reactive({ servers: [], fields: [], stdio_fields: [], storage_mode: 'unknown', writable: false })
const tests = reactive({})
const draft = reactive({})
const loading = ref(false)
const adding = ref(false)
const editing = ref('')
const busy = ref('')
const toolQuery = ref('')

const transport = ref('http')
const argsText = ref('[]')
const environment = ref([])
let nextVariableId = 0
const activeFields = computed(() => transport.value === 'stdio' ? state.stdio_fields : state.fields)
const parsedArgs = computed(() => {
  try {
    const args = JSON.parse(argsText.value)
    return Array.isArray(args) && args.length <= 256 && args.every(arg =>
      typeof arg === 'string' && !arg.includes('\u0000') && arg.length <= 8192) ? args : null
  } catch { return null }
})
const environmentError = computed(() => {
  const keys = environment.value.map(variable => variable.key)
  if (new Set([...keys, 'HOME', 'PATH']).size > 128) return 'Use at most 128 variables, including HOME and PATH.'
  if (keys.some(key => !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(key))) return 'Variable names must start with a letter or underscore and contain only letters, digits and underscores.'
  if (new Set(keys).size !== keys.length) return 'Each variable name must be unique.'
  if (environment.value.some(variable => variable.value.includes('\u0000') || variable.value.length > 32768)) return 'Variable values must be at most 32768 characters and contain no NUL characters.'
  return ''
})
const draftValid = computed(() => draftIsValid(activeFields.value, draft) && (
  transport.value !== 'stdio' || (parsedArgs.value !== null && !environmentError.value)
))

function addVariable () {
  environment.value.push({ id: nextVariableId++, key: '', value: '', stored: false })
}

const editingCredential = computed(() =>
  state.servers.find((item) => item.name === editing.value)?.credential || null)

const toolGroups = computed(() => {
  const query = toolQuery.value.trim().toLowerCase()
  return (store.catalog.tool_servers || []).map(server => ({
    ...server,
    tools: (store.catalog.tools || []).filter(tool => tool.server === server.name &&
      `${server.name} ${tool.name} ${tool.description || ''}`.toLowerCase().includes(query))
  })).filter(server => !query || server.tools.length)
})

async function load () {
  loading.value = true
  try {
    Object.assign(state, await api.mcpServers())
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    loading.value = false
  }
}

async function refresh () {
  loading.value = true
  await Promise.all([load(), actions.loadCatalog(true)])
  loading.value = false
}

function openForm (server) {
  adding.value = true
  editing.value = server?.name || ''
  transport.value = server?.transport || 'http'
  resetDraft(draft, [...state.fields, ...state.stdio_fields], server || {})
  argsText.value = JSON.stringify(server?.args || [], null, 2)
  environment.value = Object.keys(server?.env || {}).map(key => ({
    id: nextVariableId++, key, value: '', stored: true
  }))
}

const startAdd = () => openForm(null)
const edit = (server) => openForm(server)

function cancelAdd () {
  adding.value = false
  editing.value = ''
  for (const key of Object.keys(draft)) delete draft[key]
  environment.value = []
  argsText.value = '[]'
}

async function save () {
  const name = draft.name
  busy.value = name
  try {
    const payload = transport.value === 'stdio'
      ? { transport: 'stdio', command: draft.command, args: parsedArgs.value,
          env: Object.fromEntries(environment.value.map(variable => [variable.key,
            variable.stored && variable.value === '' ? null : variable.value])) }
      : { transport: 'http', url: draft.url, token: draft.token }
    Object.assign(state, await api.saveMcpServer(name, payload))
    const verb = editing.value ? 'updated' : 'added'
    cancelAdd()
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: `${name} ${verb}` })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    busy.value = ''
  }
}

function remove (server) {
  $q.dialog({
    title: `Remove ${server.name}`,
    message: 'Its tools disappear from the pickers. Chats pinned to them keep the rest.',
    cancel: true
  }).onOk(async () => {
    busy.value = server.name
    try {
      Object.assign(state, await api.removeMcpServer(server.name))
      delete tests[server.name]
      await actions.loadCatalog(true)
      $q.notify({ type: 'positive', message: `${server.name} removed` })
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
    } finally {
      busy.value = ''
    }
  })
}

async function test (server) {
  busy.value = server.name
  delete tests[server.name]
  try {
    tests[server.name] = await api.testMcpServer(server.name)
  } catch (error) {
    tests[server.name] = { ok: false, message: error.message }
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.mcp-env-row { display: flex; flex-wrap: wrap; gap: 8px; }
.mcp-env-row .field { flex: 1 1 150px; min-width: 0; }
.mcp-command { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
