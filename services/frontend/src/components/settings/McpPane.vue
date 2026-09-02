<template>
  <h2 class="settings__title">MCP servers</h2>

  <div class="setting">
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
      Every server is federated onto one endpoint in agentgateway. Its tools arrive
      prefixed with the server name and show up in the tool picker straight away.
    </p>

    <div v-if="adding" class="integration-add">
      <strong>{{ editing ? `Configure ${editing}` : 'Add a server' }}</strong>
      <DeclaredField
        v-for="field in state.fields" :key="field.key" v-model="draft[field.key]"
        :field="field" :credential="editingCredential"
        :disabled="Boolean(editing) && field.key === 'name'"
      />
      <p class="caption dim">
        The server is contacted before it is saved, because one target that cannot
        answer takes the whole endpoint down with it.
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
      <div class="integration-meta caption dim">
        <span class="mono truncate">{{ server.url }}</span>
        <span>{{ credentialLabel(server.credential) }}</span>
        <span v-if="!server.managed">from the gateway config file</span>
      </div>
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

  <div class="setting">
    <div class="setting__label">Tools on offer</div>
    <p v-if="!toolGroups.length" class="caption dim">No MCP server answered.</p>
    <template v-for="group in toolGroups" :key="group.name">
      <div class="line">
        <span class="grow truncate">{{ group.name }}</span>
        <span v-if="group.host" class="caption dim mono truncate">{{ group.host }}</span>
        <span class="chip chip--success">{{ group.tools.length }} tools</span>
      </div>
      <div v-for="tool in group.tools" :key="tool.name" class="line line--stacked line--nested">
        <span class="mono">{{ tool.name }}</span>
        <span v-if="tool.description" class="caption dim">{{ tool.description }}</span>
      </div>
    </template>
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

const state = reactive({ servers: [], fields: [], storage_mode: 'unknown', writable: false })
const tests = reactive({})
const draft = reactive({})
const loading = ref(false)
const adding = ref(false)
const editing = ref('')
const busy = ref('')

const draftValid = computed(() => draftIsValid(state.fields, draft))

const editingCredential = computed(() =>
  state.servers.find((item) => item.name === editing.value)?.credential || null)

const toolGroups = computed(() =>
  (store.catalog.tool_servers || []).map((server) => ({
    ...server,
    tools: (store.catalog.tools || []).filter((tool) => tool.server === server.name)
  })))

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
  resetDraft(draft, state.fields, server || {})
}

const startAdd = () => openForm(null)
const edit = (server) => openForm(server)

function cancelAdd () {
  adding.value = false
  editing.value = ''
}

async function save () {
  const name = draft.name
  busy.value = name
  try {
    Object.assign(state, await api.saveMcpServer(name, { ...draft }))
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
