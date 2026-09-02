<template>
  <h2 class="settings__title">Agents and models</h2>

  <div class="setting">
    <div class="setting__label">Agent sets</div>
    <div v-for="set in store.catalog.agent_sets || []" :key="set.name" class="line">
      <span class="grow truncate">{{ set.name }}</span>
      <span v-if="set.image" class="caption dim mono truncate">{{ set.image }}</span>
      <span class="chip" :class="set.ready === false ? 'chip--warning' : 'chip--success'">
        {{ set.ready === false ? 'building' : 'ready' }}
      </span>
    </div>
  </div>

  <div class="setting">
    <div class="row integration-head">
      <div class="setting__label grow">Model integrations</div>
      <button class="btn btn--sm btn--outline" :disabled="loading" @click="refresh">
        {{ loading ? 'Checking…' : 'Refresh models' }}
      </button>
      <button
        class="btn btn--sm btn--primary"
        :disabled="!state.writable || !state.available.length" @click="toggleAdd"
      >
        Add integration
      </button>
    </div>
    <p class="caption dim">
      Provider routes live in agentgateway. Their models are discovered automatically and
      labeled by both integration and model vendor in every picker.
    </p>

    <div v-if="adding" class="integration-add">
      <template v-if="!selected">
        <div class="section-label">Available integrations</div>
        <button
          v-for="integration in state.available" :key="integration.type"
          class="integration-option" @click="choose(integration)"
        >
          <span class="material-icons">add_circle_outline</span>
          <span class="grow">
            <strong>{{ integration.name }}</strong>
            <span class="caption dim">{{ integration.description }}</span>
          </span>
        </button>
      </template>
      <template v-else>
        <div class="row">
          <button class="btn btn--icon btn--sm" @click="back">
            <span class="material-icons">arrow_back</span>
          </button>
          <strong class="grow">
            {{ selected.configured ? 'Configure' : 'Add' }} {{ selected.name }}
          </strong>
        </div>
        <p class="caption dim">{{ selected.description }}</p>
        <DeclaredField
          v-for="field in selected.fields" :key="field.key" v-model="draft[field.key]"
          :field="field" :credential="selected.credential"
          :disabled="selected.configured && field.key === 'slug'"
        />
        <p class="caption dim">
          Credentials stay on agentgateway and are never returned to this page.
        </p>
        <div class="row integration-actions">
          <button class="btn btn--sm" @click="cancelAdd">Cancel</button>
          <button
            class="btn btn--sm btn--primary" :disabled="Boolean(busy) || !draftValid" @click="save"
          >
            {{ busy
              ? 'Saving…'
              : selected.configured ? 'Save configuration' : 'Add integration' }}
          </button>
        </div>
      </template>
    </div>

    <p v-if="!state.integrations.length && !loading" class="caption dim integration-empty">
      No model integration is configured.
    </p>
    <div v-for="item in state.integrations" :key="item.instance" class="integration-card">
      <div class="row">
        <span class="material-icons integration-card__icon">hub</span>
        <strong class="grow">{{ item.name }}</strong>
        <span class="chip" :class="item.discovery.ok ? 'chip--success' : 'chip--warning'">
          {{ item.discovery.ok ? `${item.model_count} models` : 'needs attention' }}
        </span>
      </div>
      <p class="caption dim">{{ item.description }}</p>
      <div class="integration-meta caption dim">
        <span>route <code class="mono">{{ item.model_match }}</code></span>
        <span>{{ credentialLabel(item.credential) }}</span>
      </div>
      <p v-if="!item.discovery.ok" class="caption integration-warning">
        {{ item.discovery.message }}
      </p>
      <div v-if="tests[item.instance]" class="line">
        <span class="dot" :class="tests[item.instance].ok ? 'dot--ok' : 'dot--bad'" />
        <span class="caption grow">{{ tests[item.instance].message }}</span>
        <span v-if="tests[item.instance].status" class="caption dim">
          HTTP {{ tests[item.instance].status }}
        </span>
      </div>
      <div class="row integration-actions">
        <button
          v-if="item.fields.length" class="btn btn--sm" :disabled="Boolean(busy)"
          @click="choose(item)"
        >
          Configure
        </button>
        <button
          class="btn btn--sm btn--outline" :disabled="Boolean(busy)" @click="test(item)"
        >
          {{ busy === item.instance && action === 'test' ? 'Testing…' : 'Test' }}
        </button>
        <span class="grow" />
        <button
          class="btn btn--sm btn--danger" :disabled="Boolean(busy)" @click="remove(item)"
        >
          Remove
        </button>
      </div>
    </div>
  </div>

  <div class="setting">
    <div class="setting__label">Models reachable through the gateway</div>
    <template v-for="gateway in modelGroups" :key="gateway.name">
      <p class="caption dim" style="margin-top: 10px">
        via <code class="mono">{{ gateway.name }}</code> · {{ gateway.total }} models
      </p>
      <div v-for="provider in gateway.providers" :key="provider.name" class="line">
        <span class="grow truncate">{{ provider.name }}</span>
        <span v-if="provider.hasDefault" class="chip chip--accent">default</span>
        <span class="caption dim">{{ provider.count }}</span>
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
const state = reactive({ integrations: [], available: [], storage_mode: 'unknown', writable: false })
const tests = reactive({})
const draft = reactive({})
const loading = ref(false)
const adding = ref(false)
const choice = ref('')
const busy = ref('')
const action = ref('')

const selected = computed(() =>
  [...state.integrations, ...state.available]
    .find((item) => (item.instance || item.type) === choice.value))

const draftValid = computed(() => draftIsValid(selected.value?.fields, draft))

const modelGroups = computed(() => {
  const byGateway = new Map()
  for (const model of store.catalog.models || []) {
    const gateway = model.gateway || 'gateway'
    if (!byGateway.has(gateway)) byGateway.set(gateway, new Map())
    const byProvider = byGateway.get(gateway)
    const provider = model.provider || 'other'
    byProvider.set(provider, [...(byProvider.get(provider) || []), model])
  }
  return [...byGateway].map(([name, byProvider]) => ({
    name,
    total: [...byProvider.values()].reduce((sum, list) => sum + list.length, 0),
    providers: [...byProvider]
      .map(([provider, list]) => ({
        name: provider,
        count: list.length,
        hasDefault: list.some((model) => model.id === store.catalog.default_model)
      }))
      .sort((a, b) => b.count - a.count)
  }))
})

function apply (data) {
  state.integrations = data.integrations || []
  state.available = data.available || []
  state.storage_mode = data.storage_mode || 'unknown'
  state.writable = Boolean(data.writable)
}

async function load () {
  loading.value = true
  try {
    apply(await api.modelIntegrations())
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    loading.value = false
  }
}

async function refresh () {
  await Promise.all([load(), actions.loadCatalog(true)])
}

function choose (integration) {
  adding.value = true
  choice.value = integration.instance || integration.type
  resetDraft(draft, integration.fields, integration.config)
}

function clearDraft () {
  choice.value = ''
  resetDraft(draft, [])
}

function cancelAdd () {
  adding.value = false
  clearDraft()
}

function back () {
  if (selected.value?.configured) cancelAdd()
  else clearDraft()
}

function toggleAdd () {
  if (adding.value) return cancelAdd()
  clearDraft()
  adding.value = true
}

async function save () {
  const integration = selected.value
  if (!integration) return
  busy.value = integration.instance || integration.type
  action.value = 'save'
  try {
    apply(await api.addModelIntegration(busy.value, { ...draft }))
    cancelAdd()
    await actions.loadCatalog(true)
    const verb = integration.configured ? 'updated' : 'added'
    $q.notify({ type: 'positive', message: `${integration.name} ${verb}` })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    busy.value = ''
    action.value = ''
  }
}

function remove (integration) {
  $q.dialog({
    title: `Remove ${integration.name}`,
    message: 'Its models will disappear from the pickers. Existing chats keep their selected model.',
    cancel: true
  }).onOk(async () => {
    busy.value = integration.instance
    action.value = 'remove'
    try {
      const data = await api.removeModelIntegration(integration.instance)
      apply(data)
      delete tests[integration.instance]
      await actions.loadCatalog(true)
      $q.notify({ type: 'positive', message: `${integration.name} removed` })
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
    } finally {
      busy.value = ''
      action.value = ''
    }
  })
}

async function test (integration) {
  busy.value = integration.instance
  action.value = 'test'
  delete tests[integration.instance]
  try {
    const result = await api.testModelIntegration(integration.instance)
    tests[integration.instance] = result
    if (result.ok) await Promise.all([load(), actions.loadCatalog(true)])
  } catch (error) {
    tests[integration.instance] = { ok: false, message: error.message }
  } finally {
    busy.value = ''
    action.value = ''
  }
}

onMounted(load)
</script>
