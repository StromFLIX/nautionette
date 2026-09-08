<template>
  <h2 class="settings__title">Projects</h2>
  <p v-if="error" class="projects-error" role="alert">{{ error }}</p>
  <p v-if="route.query.github === 'pending'" class="caption" role="status">Waiting for organization approval.</p>
  <div id="github-app" class="setting">
    <div class="row project-heading">
      <div class="setting__label grow">GitHub App</div>
      <span v-if="app.configured" class="chip chip--success">Connected</span>
      <span v-else-if="app.registered" class="chip chip--warning">{{ connectionState }}</span>
      <button class="btn btn--icon btn--sm" aria-label="Refresh GitHub connection" :disabled="appLoading" @click="loadApp">
        <span class="material-icons">refresh</span><q-tooltip>Refresh connection</q-tooltip>
      </button>
    </div>
    <div v-if="app.configured" class="project-connection">
      <strong class="project-name">{{ app.account || app.slug || 'GitHub' }}</strong>
      <span v-if="app.automatic" class="caption dim">{{ app.webhook?.last_received_at ? `Webhook received ${new Date(app.webhook.last_received_at * 1000).toLocaleString()}` : 'Awaiting first webhook' }}</span>
      <a v-if="!app.automatic && app.install_url" class="btn btn--sm" :href="app.install_url" target="_blank" rel="noopener noreferrer">
        <span class="material-icons">open_in_new</span>Repository access
      </a>
    </div>
    <form class="project-form" @submit.prevent="connectApp">
      <label v-if="!app.automatic" class="project-form__wide">Public instance URL
        <input v-model="form.public_url" class="field" type="url" placeholder="https://nautionette.example.com" :readonly="app.registered" required />
      </label>
      <label v-if="!app.registered">App owner
        <select v-model="owner" class="field"><option value="personal">Personal account</option><option value="organization">Organization</option></select>
      </label>
      <label v-if="!app.registered && owner === 'organization'">Organization
        <input v-model="form.organization" class="field" placeholder="your-organization" pattern="[A-Za-z0-9-]+" maxlength="39" required />
      </label>
      <div class="project-form__wide row project-actions">
        <button class="btn btn--primary btn--sm" :disabled="Boolean(busy) || appLoading">
          <span class="material-icons">open_in_new</span>{{ busy === 'app' ? 'Connecting...' : app.automatic && app.configured ? 'Repository access' : app.registered ? 'Complete installation' : 'Connect GitHub' }}
        </button>
      </div>
    </form>
  </div>

  <div id="repositories" class="setting">
    <div class="row project-heading">
      <div class="setting__label grow">Repositories</div>
      <span class="caption mono dim">/projects</span>
      <button class="btn btn--icon btn--sm" aria-label="Refresh projects" :disabled="loading" @click="refresh">
        <span class="material-icons">refresh</span><q-tooltip>Refresh projects</q-tooltip>
      </button>
    </div>
    <p v-if="loading && !projects.length" class="caption dim" role="status">Loading projects...</p>
    <p v-else-if="!projects.length" class="caption dim">No projects added.</p>
    <div v-for="project in projects" :key="project.id" class="project-row">
      <span class="material-icons project-row__icon">folder_open</span>
      <div class="grow">
        <strong class="project-name">{{ project.full_name }}</strong>
        <span class="caption dim mono project-path">{{ project.path }}</span>
        <p v-if="project.error" class="projects-error caption">{{ project.error }}</p>
      </div>
      <span class="chip" :class="project.status === 'ready' ? 'chip--success' : 'chip--warning'">{{ project.status === 'cloning' ? 'Downloading' : project.status }}</span>
      <button v-if="project.status === 'failed'" class="btn btn--icon btn--sm" :aria-label="`Retry ${project.full_name}`" :disabled="Boolean(busy)" @click="add(project.full_name)">
        <span class="material-icons">refresh</span><q-tooltip>Retry download</q-tooltip>
      </button>
      <button class="btn btn--icon btn--sm" :aria-label="`Remove ${project.full_name}`" :disabled="Boolean(busy) || project.status === 'cloning'" @click="remove(project)">
        <span class="material-icons">remove_circle_outline</span><q-tooltip>Remove from projects; keep files</q-tooltip>
      </button>
    </div>
  </div>

  <div id="available-repositories" class="setting">
    <div class="row project-heading">
      <div class="setting__label grow">Available repositories</div>
      <button v-if="app.configured" class="btn btn--icon btn--sm" aria-label="Refresh GitHub repositories" :disabled="repositoryLoading" @click="loadRepositories(1)">
        <span class="material-icons">refresh</span><q-tooltip>Refresh repositories</q-tooltip>
      </button>
    </div>
    <input v-if="app.configured" v-model="query" class="field project-search" aria-label="Search GitHub repositories" placeholder="Search repositories" />
    <p v-if="!app.configured" class="caption dim">Connect GitHub to browse available repositories.</p>
    <p v-else-if="repositoryLoading" class="caption dim" role="status">Loading repositories...</p>
    <p v-else-if="!filtered.length" class="caption dim">No matching repositories on this page.</p>
    <div v-for="repository in filtered" :key="repository.id" class="project-row">
      <span class="material-icons project-row__icon">{{ repository.private ? 'lock' : 'code' }}</span>
      <div class="grow project-name">{{ repository.full_name }}</div>
      <button class="btn btn--sm btn--outline" :disabled="Boolean(busy) || added.has(repository.id)" @click="add(repository.full_name)">
        <span class="material-icons">{{ added.has(repository.id) ? 'check' : 'download' }}</span>{{ added.has(repository.id) ? 'Added' : 'Add' }}
      </button>
    </div>
    <div v-if="total > 50" class="row project-pagination">
      <button class="btn btn--icon btn--sm" aria-label="Previous repositories" :disabled="page === 1 || repositoryLoading" @click="loadRepositories(page - 1)"><span class="material-icons">chevron_left</span></button>
      <span class="caption dim">{{ page }} / {{ Math.ceil(total / 50) }}</span>
      <button class="btn btn--icon btn--sm" aria-label="Next repositories" :disabled="page * 50 >= total || repositoryLoading" @click="loadRepositories(page + 1)"><span class="material-icons">chevron_right</span></button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import { api, server } from '../../api'

const $q = useQuasar()
const route = useRoute()
const app = ref({ configured: false })
const form = reactive({ public_url: server.url || location.origin, organization: '' })
const owner = ref('personal')
const appLoading = ref(true)
const connectionState = computed(() => ({ suspended: 'Suspended', removed: 'Installation removed', permissions_required: 'Permissions required' }[app.value.installation_status] || 'Installation pending'))
const projects = ref([])
const repositories = ref([])
const page = ref(1)
const total = ref(0)
const query = ref('')
const loading = ref(false)
const repositoryLoading = ref(false)
const busy = ref('')
const error = ref('')
let timer
let disposed = false
const added = computed(() => new Set(projects.value.map((project) => project.repository_id)))
const filtered = computed(() => repositories.value.filter((repository) => repository.full_name.toLowerCase().includes(query.value.trim().toLowerCase())))

async function refresh () {
  if (loading.value) return
  clearTimeout(timer)
  loading.value = true
  try { projects.value = (await api.projects()).projects }
  catch (failure) { error.value = failure.message }
  finally {
    loading.value = false
    if (!disposed && projects.value.some((project) => project.status === 'cloning')) timer = setTimeout(refresh, 2000)
  }
}

async function loadRepositories (nextPage) {
  repositoryLoading.value = true
  try {
    const result = await api.projectRepositories(nextPage)
    repositories.value = result.repositories
    total.value = result.total_count
    page.value = nextPage
  } catch (failure) { error.value = failure.message }
  finally { repositoryLoading.value = false }
}

async function loadApp () {
  appLoading.value = true
  try {
    app.value = await api.projectApp()
    if (app.value.public_url) form.public_url = app.value.public_url
    if (app.value.configured && !disposed) await loadRepositories(1)
    else repositories.value = []
  } catch (failure) { error.value = failure.message }
  finally { appLoading.value = false }
}

async function connectApp () {
  busy.value = 'app'
  error.value = ''
  try {
    const result = await api.connectProjectApp({ public_url: form.public_url, organization: owner.value === 'organization' ? form.organization : '' })
    window.location.assign(result.start_url)
  } catch (failure) { error.value = failure.message }
  finally { busy.value = '' }
}

async function add (fullName) {
  busy.value = fullName
  error.value = ''
  try { await api.addProject(fullName); await refresh() }
  catch (failure) { error.value = failure.message }
  finally { busy.value = '' }
}

function remove (project) {
  $q.dialog({ title: `Remove ${project.full_name}?`, message: 'Local files and unpushed changes will be kept.', cancel: true }).onOk(async () => {
    busy.value = project.id
    error.value = ''
    try { await api.removeProject(project.id); await refresh() }
    catch (failure) { error.value = failure.message }
    finally { busy.value = '' }
  })
}

onMounted(async () => {
  await refresh()
  await loadApp()
})
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>

<style scoped>
.project-heading { gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.project-heading .material-icons, .project-actions .material-icons, .project-row .btn .material-icons { font-size: 16px; }
.project-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.project-form label { display: flex; flex-direction: column; gap: 6px; min-width: 0; font-size: 13px; }
.project-form input { min-width: 0; max-width: 100%; }
.project-form__wide { grid-column: 1 / -1; }
.project-actions { gap: 8px; flex-wrap: wrap; }
.project-actions .btn { white-space: normal; text-align: left; }
.project-connection { display: grid; gap: 6px; margin-bottom: 14px; overflow-wrap: anywhere; }
.project-row { display: flex; align-items: center; gap: 8px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.project-row > .grow { min-width: 0; }
.project-row__icon { flex: none; font-size: 20px; color: var(--text-dim); }
.project-name { display: block; overflow-wrap: anywhere; font-size: 13px; }
.project-path { display: block; overflow-wrap: anywhere; margin-top: 4px; }
.projects-error { color: var(--danger); overflow-wrap: anywhere; }
.project-search { width: 100%; }
.project-pagination { justify-content: flex-end; gap: 8px; padding-top: 10px; }
@media (max-width: 420px) {
  .project-form { grid-template-columns: minmax(0, 1fr); }
  .project-row { flex-wrap: wrap; }
  .project-row > .grow { flex-basis: calc(100% - 36px); }
}
</style>