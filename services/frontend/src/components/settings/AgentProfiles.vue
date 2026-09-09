<template>
  <section id="agent-profiles" class="setting profiles">
    <div class="row profiles__head">
      <div class="grow"><h3 class="setting__label">Agents</h3><p class="caption dim">One configuration. Ready for any chat.</p></div>
      <button v-if="!editing" class="btn btn--primary btn--sm" type="button" :disabled="busy || !store.catalogLoaded" @click="open()"><span class="material-icons" aria-hidden="true">add</span>New agent</button>
    </div>
    <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }}</p>
    <p v-if="!store.catalogLoaded" class="caption" :role="store.catalogError ? 'alert' : 'status'">
      {{ store.catalogError || 'Loading agents…' }}
      <button v-if="store.catalogError" class="btn btn--sm" type="button" @click="actions.loadCatalog(true)">Retry agents</button>
    </p>

    <form v-if="editing" class="profile-editor" aria-label="Agent configuration" @submit.prevent="save">
      <div class="row profile-editor__head"><strong class="grow">{{ draft.id ? 'Edit agent' : 'New agent' }}</strong>
        <button class="btn btn--icon btn--sm" type="button" aria-label="Close agent editor" :disabled="busy" @click="close"><span class="material-icons" aria-hidden="true">close</span></button>
      </div>
      <label class="profile-editor__label" for="profile-name">Agent name</label>
      <input id="profile-name" ref="nameInput" v-model="draft.name" class="field" maxlength="80" required :disabled="busy" placeholder="e.g. Code review" />
      <label class="profile-editor__label" for="profile-description">Description <span class="dim">optional</span></label>
      <input id="profile-description" v-model="draft.description" class="field" maxlength="500" :disabled="busy" placeholder="What this agent is for" />
      <AgentConfigFields v-model="draft.config" :defaults="globalConfig" inheritable :disabled="busy" />
      <AgentPackages :key="draft.id" :model-value="draft.config.packages || []" :disabled="busy" @update:model-value="draft.config.packages = $event" @busy="packageBusy = $event" />
      <div class="row profile-editor__actions">
        <span class="caption dim grow">Existing chats stay unchanged.</span>
        <button class="btn" type="button" :disabled="busy" @click="close">Cancel</button>
        <button class="btn btn--primary" type="submit" :disabled="busy || packageBusy || !draft.name.trim()">{{ busy ? 'Saving…' : 'Save agent' }}</button>
      </div>
    </form>

    <div v-else-if="store.catalogLoaded" class="profiles__list">
      <article class="profile-card">
        <span class="profile-card__mark material-icons" aria-hidden="true">tune</span>
        <div class="grow profile-card__info"><strong>Global defaults</strong><span class="caption dim">{{ summary(globalConfig) }}</span></div>
        <span v-if="!defaultId" class="chip chip--accent">Default</span>
        <div class="profile-card__actions">
          <button class="btn btn--icon btn--sm" type="button" aria-label="Make Global defaults the default agent" :disabled="busy || !defaultId" @click="makeDefault(null)"><span class="material-icons" aria-hidden="true">star_outline</span><q-tooltip>Use for new chats</q-tooltip></button>
          <RouterLink class="btn btn--icon btn--sm" aria-label="Edit global defaults" to="/settings/general#default-model"><span class="material-icons" aria-hidden="true">edit</span></RouterLink>
        </div>
      </article>
      <article v-for="agent in agents" :key="agent.id" class="profile-card">
        <span class="profile-card__mark material-icons" aria-hidden="true">smart_toy</span>
        <button class="profile-card__info profile-card__open grow" type="button" :aria-label="`Edit ${agent.name}`" :disabled="busy" @click="open(agent)">
          <strong>{{ agent.name }}</strong><span v-if="agent.description" class="caption dim">{{ agent.description }}</span>
          <span class="caption dim">{{ summary(agent.resolved || resolveAgentConfig(agent.config, globalConfig)) }}</span>
        </button>
        <span v-if="defaultId === agent.id" class="chip chip--accent">Default</span>
        <div class="profile-card__actions">
          <button class="btn btn--icon btn--sm" type="button" :aria-label="`Make ${agent.name} the default agent`" :disabled="busy || defaultId === agent.id" @click="makeDefault(agent.id)"><span class="material-icons" aria-hidden="true">star_outline</span><q-tooltip>Use for new chats</q-tooltip></button>
          <button class="btn btn--icon btn--sm" type="button" :aria-label="`Duplicate ${agent.name}`" :disabled="busy" @click="open(agent, true)"><span class="material-icons" aria-hidden="true">content_copy</span><q-tooltip>Duplicate agent</q-tooltip></button>
          <button class="btn btn--icon btn--sm" type="button" :aria-label="`Delete ${agent.name}`" :disabled="busy" @click="remove(agent)"><span class="material-icons" aria-hidden="true">delete_outline</span><q-tooltip>Delete agent</q-tooltip></button>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import AgentConfigFields from './AgentConfigFields.vue'
import AgentPackages from './AgentPackages.vue'
import { api } from '../../api'
import { actions, store } from '../../store'
import { copyConfig, globalChatConfig, resolveAgentConfig, toolLabel, projectLabel } from '../../agent-config'

const $q = useQuasar()
const editing = ref(false)
const busy = ref(false), packageBusy = ref(false)
const error = ref('')
const nameInput = ref(null)
const draft = reactive({ id: null, name: '', description: '', config: {} })
const agents = computed(() => store.catalog.agents || [])
const defaultId = computed(() => store.catalog.default_agent_id ?? null)
const globalConfig = computed(() => globalChatConfig(store.catalog))
function summary (config) {
  const model = store.catalog.models?.find(item => item.id === config.model)?.name || config.model?.split('/').pop() || 'No model'
  return `${model} · ${toolLabel(config.tools)} · ${projectLabel(config.project_ids)}${config.packages?.length ? ` · ${config.packages.length} packages` : ''}`
}
function open (agent = null, duplicate = false) {
  if (packageBusy.value) return
  error.value = ''
  Object.assign(draft, {
    id: duplicate ? null : agent?.id ?? null,
    name: agent ? `${agent.name}${duplicate ? ' copy' : ''}`.slice(0, 80) : '',
    description: agent?.description || '', config: copyConfig(agent?.config || {})
  })
  editing.value = true
  nextTick(() => nameInput.value?.focus())
}
function close () { if (!packageBusy.value) { editing.value = false; error.value = '' } }
async function save () {
  if (busy.value || packageBusy.value || !draft.name.trim()) return
  busy.value = true
  error.value = ''
  try {
    const body = { name: draft.name.trim(), description: draft.description, config: draft.config }
    const saved = draft.id ? await api.updateAgent(draft.id, body) : await api.createAgent(body)
    draft.id = saved.id // A failed refresh must not make a retry create a duplicate.
    actions.syncAgentCatalog({ agents: [...agents.value.filter(agent => agent.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)) })
    close()
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: 'Agent saved' })
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
async function makeDefault (id) {
  busy.value = true
  error.value = ''
  try {
    const saved = await api.saveSettings({ default_agent_id: id })
    actions.applyAgentSettings(saved.settings)
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: 'Default agent updated' })
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
function remove (agent) {
  $q.dialog({ title: `Delete ${agent.name}?`, message: 'Existing chats keep their configuration. If this is your default agent, new chats return to global defaults.', cancel: true })
    .onOk(async () => {
      busy.value = true
      error.value = ''
      try {
        await api.deleteAgent(agent.id)
        actions.syncAgentCatalog({ agents: agents.value.filter(item => item.id !== agent.id),
          default_agent_id: defaultId.value === agent.id ? null : defaultId.value })
        await actions.loadCatalog(true)
        $q.notify({ type: 'positive', message: 'Agent deleted' })
      } catch (failure) { error.value = failure.message }
      finally { busy.value = false }
    })
}
</script>

<style scoped>
.profiles__head { gap: 12px; margin-bottom: 16px; }
.profiles__head h3 { margin: 0; }
.profiles__head p { margin: 6px 0 0; }
.profiles__head > .btn { flex: none; }
.profiles__head .material-icons { font-size: 1rem; }
.profiles__list { display: grid; gap: 10px; }
.profile-card { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); }
.profile-card__mark { display: grid; place-items: center; width: 34px; height: 34px; flex: none; clip-path: var(--octagon); background: var(--accent-soft); color: var(--accent); font-size: 1.0625rem; }
.profile-card__info { display: grid; gap: 4px; min-width: 100px; flex-basis: 45%; overflow-wrap: anywhere; }
.profile-card__info strong { font-size: 0.8125rem; font-weight: 600; }
.profile-card__open { border: 0; padding: 0; text-align: left; background: none; color: var(--text); cursor: pointer; font: inherit; }
.profile-card__open:hover strong { color: var(--accent); }
.profile-card__actions { display: flex; gap: 2px; margin-left: auto; }
.profile-card__actions .material-icons { font-size: 1.0625rem; }
.profile-editor { padding: 18px; border: 1px solid var(--border-strong); border-radius: var(--radius-md); background: var(--surface-panel); }
.profile-editor__head { margin-bottom: 16px; }
.profile-editor__label { display: block; margin: 16px 0 8px; font-size: 0.75rem; }
.profile-editor__label .dim { margin-left: 6px; font-size: 0.6875rem; }
.profile-editor__actions { flex-wrap: wrap; gap: 6px; padding-top: 18px; border-top: 1px solid var(--border); }
@media (max-width: 640px) {
  .profile-card { padding: 12px; }
  .profile-editor { padding: 12px; }
  .profile-editor__actions > span { flex-basis: 100%; margin-bottom: 8px; }
}
</style>
