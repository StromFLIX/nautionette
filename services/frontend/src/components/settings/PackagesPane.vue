<template>
  <h2 id="pi-packages" class="settings__title">Pi packages</h2>
  <p class="caption dim">Install once, select and configure separately for each saved agent. New chats use the saved revisions; use the chat's agent selector to apply changes to an existing chat.</p>
  <label class="setting__label" for="package-agent">Agent</label>
  <select id="package-agent" v-model="agentId" class="field" :disabled="busy || packageBusy || dirty">
    <option value="">Choose a saved agent…</option>
    <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option>
  </select>
  <p v-if="!agents.length" class="caption"><RouterLink to="/settings/agents#agent-profiles">Create an agent first</RouterLink></p>
  <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }}</p>
  <template v-if="agentId">
    <AgentPackages :key="agentId" v-model="packages" :disabled="busy" :initial-source="initialSource" @busy="packageBusy = $event" />
    <div class="row packages-actions"><span class="caption dim grow">{{ dirty ? 'Unsaved agent selection' : 'Agent selection saved' }}</span>
      <button type="button" class="btn" :disabled="busy || packageBusy || !dirty" @click="reset">Discard selection changes</button>
      <button type="button" class="btn btn--primary" :disabled="busy || packageBusy || !dirty" @click="save">Save agent packages</button>
    </div>
  </template>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useQuasar } from 'quasar'
import AgentPackages from './AgentPackages.vue'
import { api } from '../../api'
import { actions, store } from '../../store'
const route = useRoute(), $q = useQuasar()
const agentId = ref(''), packages = ref([]), original = ref([]), busy = ref(false), packageBusy = ref(false), error = ref('')
const agents = computed(() => store.catalog.agents || [])
const initialSource = computed(() => typeof route.query.source === 'string' ? route.query.source : '')
const dirty = computed(() => JSON.stringify(packages.value) !== JSON.stringify(original.value))
watch(agentId, () => {
  const ids = agents.value.find(agent => agent.id === agentId.value)?.config?.packages || []
  packages.value = [...ids]; original.value = [...ids]; error.value = ''
})
function reset () { packages.value = [...original.value] }
async function save () {
  busy.value = true; error.value = ''
  try {
    // Fetch the current profile rather than overwriting unrelated settings from
    // an old catalog snapshot. Package revisions themselves never mutate.
    const agent = await api.agent(agentId.value)
    const saved = await api.updateAgent(agent.id, { config: { ...agent.config, packages: packages.value } })
    original.value = [...packages.value]
    actions.syncAgentCatalog({ agents: agents.value.map(item => item.id === saved.id ? saved : item) })
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: 'Agent packages saved. Existing chats are unchanged.' })
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
</script>
<style scoped>
#package-agent { width: 100%; max-width: 100%; margin-top: 10px; }
.packages-actions { flex-wrap: wrap; gap: 8px; }
.packages-actions > span { flex-basis: 100%; }
.packages-actions button { white-space: normal; }
</style>
