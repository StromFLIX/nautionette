<template>
  <div class="side stack grow">
    <header class="side__head">
      <div class="row">
        <h1 class="side__title grow">{{ heading }}</h1>
        <button
          v-if="section === 'chats'" class="btn btn--icon" title="New chat"
          @click="startChat"
        >
          <span class="material-icons">add</span>
        </button>
        <button
          v-else class="btn btn--icon" title="Refresh"
          @click="refresh"
        >
          <span class="material-icons">refresh</span>
        </button>
        <RouterLink
          class="btn btn--icon side__cog" :to="`/settings/${health === 'degraded' ? 'system' : 'general'}`"
          :title="health === 'degraded' ? 'Settings — something needs attention' : 'Settings'"
        >
          <span class="material-icons">settings</span>
          <span v-if="health !== 'ok'" class="dot side__cog-dot" :class="{ 'dot--bad': health === 'degraded' }" />
        </RouterLink>
      </div>
      <div class="side__search">
        <span class="material-icons">search</span>
        <input v-model="query" class="side__search-input" :placeholder="`Search ${heading.toLowerCase()}`" />
        <button v-if="query" class="btn btn--icon btn--sm" @click="query = ''">
          <span class="material-icons" style="font-size: 16px">close</span>
        </button>
      </div>
      <div v-if="section === 'chats'" class="side__controls">
        <div class="side__control">
          <span class="side__control-label">Group</span>
          <div class="seg" role="group" aria-label="Group chats by">
            <button
              v-for="opt in groupOptions" :key="opt.value" type="button"
              class="seg__btn" :class="{ 'seg__btn--active': groupBy === opt.value }"
              :aria-pressed="groupBy === opt.value" @click="groupBy = opt.value"
            >{{ opt.label }}</button>
          </div>
        </div>
        <div class="side__control">
          <span class="side__control-label">Active</span>
          <div class="seg" role="group" aria-label="Show chats active within">
            <button
              v-for="opt in activeOptions" :key="opt.value" type="button"
              class="seg__btn" :class="{ 'seg__btn--active': activeMinutes === opt.value }"
              :aria-pressed="activeMinutes === opt.value" :title="opt.title" @click="activeMinutes = opt.value"
            >{{ opt.label }}</button>
          </div>
        </div>
      </div>
    </header>

    <div class="side__list scroll-y grow">
      <!-- chats -->
      <template v-if="section === 'chats'">
        <div v-for="group in chatGroups" :key="group.key" class="side__chat-group">
          <div v-if="group.key !== '__all__'" class="side__group section-label">
            <span class="truncate">{{ group.label }}</span>
            <span class="side__group-count">{{ group.visible.length + (showOlder ? group.older.length : 0) }}</span>
          </div>

          <ChatRow
            v-for="chat in group.visible" :key="chat.id"
            :chat="chat" :active-route-id="route.params.id" :read-busy="readBusy" @toggle-unread="setUnread"
          />

          <template v-if="showOlder">
            <ChatRow
              v-for="chat in group.older" :key="chat.id"
              :chat="chat" :active-route-id="route.params.id" :read-busy="readBusy" @toggle-unread="setUnread"
            />
          </template>
        </div>

        <button
          v-if="hiddenCount" type="button" class="side__more"
          @click="showOlder = !showOlder"
        >
          <span class="material-icons" aria-hidden="true">{{ showOlder ? 'expand_less' : 'expand_more' }}</span>
          {{ showOlder ? `Hide ${hiddenCount} older` : `Show ${hiddenCount} older` }}
        </button>
        <p v-if="readError" class="side__error caption" role="alert">{{ readError }}</p>
        <p v-if="!filteredChats.length" class="side__empty caption">
          {{ query ? 'Nothing matches that.' : 'No chats yet.' }}
        </p>
        <p v-else-if="!visibleCount && !showOlder" class="side__empty caption">
          Nothing active in the last {{ activeLabel }}.
          <button type="button" class="side__empty-link" @click="showOlder = true">Show older chats</button>
        </p>
      </template>

      <!-- workflows -->
      <template v-else-if="section === 'workflows'">
        <template v-if="filteredDrafts.length">
          <div class="side__group section-label">Waiting for review</div>
          <RouterLink
            v-for="draft in filteredDrafts" :key="draft.name" :to="`/workflows/${draft.name}`"
            class="row-item" :class="{ 'row-item--active': route.params.name === draft.name }"
          >
            <div class="avatar avatar--square avatar--draft">
              <span class="material-icons">rate_review</span>
            </div>
            <div class="grow">
              <div class="row-item__title truncate">{{ draft.name }}</div>
              <div class="row-item__sub truncate">{{ draft.meta?.message || draft.description || 'Draft workflow' }}</div>
            </div>
            <span class="chip chip--warning">draft</span>
          </RouterLink>
          <div class="side__group section-label">Published</div>
        </template>

        <RouterLink
          v-for="workflow in filteredWorkflows" :key="workflow.name" :to="`/workflows/${workflow.name}`"
          class="row-item" :class="{ 'row-item--active': route.params.name === workflow.name }"
        >
          <div class="avatar avatar--square" :style="avatarStyle(workflow.name)">
            <span class="material-icons">account_tree</span>
          </div>
          <div class="grow">
            <div class="row">
              <span class="row-item__title grow truncate">{{ workflow.title || workflow.name }}</span>
              <span v-if="workflow.settings?.disabled" class="material-icons row-item__pin dim">pause_circle</span>
              <span v-else-if="workflow.schedule" class="material-icons row-item__pin">schedule</span>
            </div>
            <div
              class="row-item__sub truncate"
              :title="workflow.schedule ? `${workflow.schedule.description} · ${workflow.schedule.timezone}` : ''"
            >
              {{ workflow.schedule?.next_run
                ? `Next ${scheduleTime(workflow.schedule.next_run, workflow.schedule.timezone)}`
                : workflow.schedule?.description || workflow.description || workflow.name }}
            </div>
          </div>
        </RouterLink>
        <p v-if="!filteredWorkflows.length && !filteredDrafts.length" class="side__empty caption">
          {{ query ? 'Nothing matches that.' : 'No workflows yet.' }}
        </p>
      </template>

      <!-- runs -->
      <template v-else>
        <RouterLink
          v-for="run in filteredRuns" :key="run.workflow_id" :to="`/runs/${run.workflow_id}`"
          class="row-item" :class="{ 'row-item--active': route.params.id === run.workflow_id }"
        >
          <div class="avatar avatar--square" :style="avatarStyle(run.workflow)">
            <span class="material-icons">bolt</span>
          </div>
          <div class="grow">
            <div class="row">
              <span class="row-item__title grow truncate">{{ run.workflow }}</span>
              <span class="row-item__time">{{ shortTime(run.created_at) }}</span>
            </div>
            <div class="row">
              <span class="chip" :class="`chip--${RUN_TONE[run.status] || ''}`">{{ run.status }}</span>
              <span class="row-item__sub dim truncate">{{ run.trigger }}</span>
            </div>
          </div>
        </RouterLink>
        <p v-if="!filteredRuns.length" class="side__empty caption">
          {{ query ? 'Nothing matches that.' : 'No runs yet.' }}
        </p>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { RUN_TONE, avatarStyle, scheduleTime, shortTime } from '../format'
import { actions, health, store } from '../store'
import { api } from '../api'
import ChatRow from './ChatRow.vue'

const route = useRoute()
const router = useRouter()
const query = ref('')
const readBusy = ref('')
const readError = ref('')
const GROUP_KEY = 'nautionette.chatGroupBy'
const RANGE_KEY = 'nautionette.chatActiveMinutes'

const groupOptions = [
  { value: 'none', label: 'All' },
  { value: 'project', label: 'Project' },
  { value: 'model', label: 'Model' },
  { value: 'internet', label: 'Internet' }
]

// A real ladder of ranges instead of three flavours of "a few minutes".
const activeOptions = [
  { value: 60, label: '1h', title: 'Active in the last hour' },
  { value: 24 * 60, label: '24h', title: 'Active in the last 24 hours' },
  { value: 7 * 24 * 60, label: '7d', title: 'Active in the last 7 days' },
  { value: 30 * 24 * 60, label: '30d', title: 'Active in the last 30 days' },
  { value: 0, label: 'All', title: 'No time limit' }
]

function storedGroupBy () {
  const saved = localStorage.getItem(GROUP_KEY)
  return groupOptions.some((opt) => opt.value === saved) ? saved : 'none'
}

function storedRange () {
  const saved = Number(localStorage.getItem(RANGE_KEY))
  return activeOptions.some((opt) => opt.value === saved) ? saved : 24 * 60
}

const groupBy = ref(storedGroupBy())
const activeMinutes = ref(storedRange())
const showOlder = ref(false)
const needsInternet = (chat) => ['pending', 'deciding'].includes(chat.internet_status)

watch(groupBy, (value) => localStorage.setItem(GROUP_KEY, value))
watch(activeMinutes, (value) => {
  localStorage.setItem(RANGE_KEY, String(value))
  showOlder.value = false
})

const activeLabel = computed(() =>
  ({ 60: 'hour', 1440: '24 hours', 10080: '7 days', 43200: '30 days' }[activeMinutes.value] || 'selected range'))

/** In range means: still needs me, or touched inside the chosen window. */
function isChatActive (chat) {
  if (chat.unread || chat.answering || needsInternet(chat)) return true
  if (!activeMinutes.value) return true
  if (!chat.updated_at) return false
  return (now.value / 1000 - chat.updated_at) <= activeMinutes.value * 60
}

function projectLabel (id) {
  return store.projects.find((project) => project.id === id)?.full_name || id
}

function modelLabel (chat) {
  const id = chat.model || store.catalog.default_model
  return store.catalog.models.find((model) => model.id === id)?.name || id || 'Default model'
}

function internetLabel (chat) {
  if (needsInternet(chat)) return 'Needs approval'
  return { allowed: 'Internet allowed', blocked: 'Internet blocked' }[chat.internet_status] || chat.internet_status || 'Unknown'
}

function groupsFor (chat) {
  if (groupBy.value === 'project') {
    const ids = Array.isArray(chat.project_ids) ? chat.project_ids : []
    return ids.length ? ids.map((id) => [id, projectLabel(id)]) : [['__none__', 'No project']]
  }
  if (groupBy.value === 'model') {
    const id = chat.model || store.catalog.default_model || '__none__'
    return [[id, modelLabel(chat)]]
  }
  if (groupBy.value === 'internet') {
    return [[chat.internet_status || '__none__', internetLabel(chat)]]
  }
  return [['__all__', 'Chats']]
}

const byRecency = (a, b) => (b.updated_at || 0) - (a.updated_at || 0)

const chatGroups = computed(() => {
  const byKey = new Map()
  for (const chat of filteredChats.value) {
    for (const [key, label] of groupsFor(chat)) {
      if (!byKey.has(key)) byKey.set(key, { key, label: label || 'Unknown', visible: [], older: [] })
      const group = byKey.get(key)
      if (isChatActive(chat)) group.visible.push(chat)
      else group.older.push(chat)
    }
  }
  const groups = [...byKey.values()]
    .map((group) => ({
      ...group,
      visible: group.visible.sort(byRecency),
      older: group.older.sort(byRecency),
      // Rank by what is actually shown; the newest chat wins the top slot.
      recency: Math.max(
        ...(showOlder.value ? [...group.visible, ...group.older] : group.visible).map((chat) => chat.updated_at || 0),
        -1
      )
    }))
    .filter((group) => group.visible.length || (showOlder.value && group.older.length))
  // Newest activity first; a catch-all bucket never outranks a named one.
  const rank = (group) => (group.key === '__none__' ? 1 : 0)
  return groups.sort((a, b) => rank(a) - rank(b) || b.recency - a.recency || String(a.label).localeCompare(String(b.label)))
})

const visibleCount = computed(() => chatGroups.value.reduce((total, group) => total + group.visible.length, 0))
const hiddenCount = computed(() =>
  filteredChats.value.length - filteredChats.value.filter((chat) => isChatActive(chat)).length)

async function setUnread (chat, unread) {
  readBusy.value = chat.id
  readError.value = ''
  try {
    // Leave the open chat first so it is not immediately acknowledged again.
    if (unread && route.params.id === chat.id) await router.push('/chats')
    await api.updateChatReadState(chat.id, { unread })
    await actions.loadChats()
  } catch (error) {
    readError.value = `Could not update read status: ${error.message}`
  } finally {
    readBusy.value = ''
  }
}

const section = computed(() => route.name || 'chats')
const heading = computed(() => ({ chats: 'Chats', workflows: 'Workflows', runs: 'Runs' }[section.value]))

const matches = (haystack) => haystack.toLowerCase().includes(query.value.trim().toLowerCase())

const filteredChats = computed(() =>
  store.chats.filter((chat) => matches(`${chat.title} ${chat.last_message?.preview || ''}`)))

// Relative windows have to age on their own, or a chat stays "active" forever.
const now = ref(Date.now())
let ticker = null
onMounted(() => { ticker = setInterval(() => { now.value = Date.now() }, 30000) })
onUnmounted(() => clearInterval(ticker))

const filteredWorkflows = computed(() =>
  store.workflows.filter((workflow) => matches(`${workflow.name} ${workflow.title || ''} ${workflow.description || ''}`)))

const filteredDrafts = computed(() =>
  store.drafts.filter((draft) => matches(`${draft.name} ${draft.description || ''}`)))

const filteredRuns = computed(() =>
  store.runs.filter((run) => matches(`${run.workflow} ${run.status} ${run.trigger}`)))

async function startChat () {
  const chat = await api.createChat({
    agent_set: store.catalog.default_agent_set,
    model: store.catalog.default_model
  })
  await actions.loadChats()
  router.push(`/chats/${chat.id}`)
}

function refresh () {
  if (section.value === 'workflows') actions.loadWorkflows()
  else actions.loadRuns()
}
</script>

<style scoped>
.side {
  overflow: hidden;
}

.side__head {
  padding: 10px 12px 8px;
  border-bottom: 1px solid var(--border);
}

.side__title {
  margin: 0 0 8px;
  font-size: 17px;
  font-weight: 650;
  line-height: 1.3;
  letter-spacing: -0.01em;
}

.side__cog {
  position: relative;
  color: var(--text-muted);
}

.side__cog-dot {
  position: absolute;
  top: 6px;
  right: 6px;
  border: 2px solid var(--surface-panel);
}

.side__search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 6px 0 10px;
  height: 34px;
  border-radius: var(--radius-pill);
  background: var(--surface-input);
  border: 1px solid transparent;
  transition: border-color var(--transition);
}

.side__search:focus-within {
  border-color: var(--accent);
}

.side__search .material-icons {
  font-size: 17px;
  color: var(--text-dim);
}

.side__search-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: none;
  outline: none;
  color: var(--text);
  font: inherit;
  font-size: 13px;
}

.side__search-input::placeholder {
  color: var(--text-dim);
}

.side__controls {
  display: grid;
  gap: 6px;
  padding: 8px 2px 0;
}

.side__control {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.side__control-label {
  flex: none;
  width: 46px;
  color: var(--text-dim);
  font-size: 11px;
  font-weight: 650;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.seg {
  display: flex;
  flex: 1;
  min-width: 0;
  padding: 2px;
  border-radius: var(--radius-pill);
  background: var(--surface-input);
  border: 1px solid var(--border);
}

.seg__btn {
  flex: 1 1 0;
  min-width: 0;
  padding: 4px 6px;
  border-radius: var(--radius-pill);
  border: none;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 11.5px;
  font-weight: 600;
  line-height: 1.6;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  transition: background var(--transition), color var(--transition);
}

.seg__btn:hover {
  color: var(--text);
  background: var(--surface-hover);
}

.seg__btn--active,
.seg__btn--active:hover {
  background: var(--accent);
  color: var(--accent-text);
}

.seg__btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.side__more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  margin-top: 8px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.side__more:hover {
  color: var(--text);
  background: var(--surface-hover);
}

.side__more .material-icons {
  font-size: 16px;
}

.side__empty-link {
  display: block;
  margin: 6px auto 0;
  border: none;
  background: none;
  color: var(--accent-hover);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.side__chat-group + .side__chat-group {
  margin-top: 6px;
}

.side__list {
  padding: 6px;
  /* Rows follow the panel width; long titles truncate instead of stretching. */
  min-width: 0;
  overflow-x: hidden;
}

.side__list > * {
  max-width: 100%;
}

.side__chat-group {
  min-width: 0;
}

.side__group {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 14px 10px 6px;
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-panel);
}

.side__group-count {
  color: var(--text-dim);
  font-weight: 500;
  font-size: 11px;
}

.side__empty {
  padding: 24px 14px;
  color: var(--text-dim);
  text-align: center;
}

.side__error {
  padding: 8px 10px;
  color: var(--danger);
}

@media (max-width: 900px) {
  .side__head {
    padding-top: calc(10px + env(safe-area-inset-top));
    padding-right: max(12px, env(safe-area-inset-right));
    padding-left: max(12px, env(safe-area-inset-left));
  }

  .side__list {
    padding-right: max(6px, env(safe-area-inset-right));
    padding-left: max(6px, env(safe-area-inset-left));
  }

  .row-item {
    min-height: 58px;
  }
}
</style>
