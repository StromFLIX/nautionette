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
        <select v-model="groupBy" class="field field--sm" aria-label="Group chats by">
          <option value="none">No grouping</option>
          <option value="project">Group by project</option>
          <option value="model">Group by model</option>
          <option value="internet">Group by internet access</option>
        </select>
        <select v-model.number="activeMinutes" class="field field--sm" aria-label="Active within">
          <option :value="10">Active: last 10m</option>
          <option :value="30">Active: last 30m</option>
          <option :value="60">Active: last 60m</option>
          <option :value="1440">Active: last 24h</option>
          <option :value="0">Active: none (all inactive)</option>
        </select>
      </div>
    </header>

    <div class="side__list scroll-y grow">
      <!-- chats -->
      <template v-if="section === 'chats'">
        <div v-for="group in chatGroups" :key="group.key" class="side__chat-group">
          <div v-if="group.key !== '__all__'" class="side__group section-label">{{ group.label }}</div>

          <div v-for="chat in group.active" :key="chat.id" class="chat-list-item">
            <ChatRow :chat="chat" :active-route-id="route.params.id" @toggle-unread="setUnread" :read-busy="readBusy" />
          </div>

          <details v-if="group.inactive.length" class="side__inactive" :open="isExpanded(group.key)" @toggle="onToggle(group.key, $event)">
            <summary class="side__inactive-summary">
              <span class="material-icons" aria-hidden="true">expand_more</span>
              Inactive ({{ group.inactive.length }})
            </summary>
            <div v-for="chat in group.inactive" :key="chat.id" class="chat-list-item">
              <ChatRow :chat="chat" :active-route-id="route.params.id" @toggle-unread="setUnread" :read-busy="readBusy" />
            </div>
          </details>
        </div>
        <p v-if="readError" class="side__error caption" role="alert">{{ readError }}</p>
        <p v-if="!filteredChats.length" class="side__empty caption">
          {{ query ? 'Nothing matches that.' : 'No chats yet.' }}
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
import { computed, ref } from 'vue'
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
const groupBy = ref('none')
const activeMinutes = ref(30)
const expanded = ref(new Set())
const needsInternet = (chat) => ['pending', 'deciding'].includes(chat.internet_status)

function isExpanded (key) {
  return expanded.value.has(key)
}

function onToggle (key, event) {
  const next = new Set(expanded.value)
  if (event.target.open) next.add(key)
  else next.delete(key)
  expanded.value = next
}

function isChatActive (chat) {
  if (chat.unread || chat.answering || needsInternet(chat)) return true
  if (!activeMinutes.value) return false
  if (!chat.updated_at) return false
  return (Date.now() / 1000 - chat.updated_at) <= activeMinutes.value * 60
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
    return chat.project_ids?.length ? chat.project_ids.map((id) => [id, projectLabel(id)]) : [['__none__', 'No project']]
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

const chatGroups = computed(() => {
  const byKey = new Map()
  for (const chat of filteredChats.value) {
    for (const [key, label] of groupsFor(chat)) {
      if (!byKey.has(key)) byKey.set(key, { key, label, active: [], inactive: [] })
      const group = byKey.get(key)
      if (isChatActive(chat)) group.active.push(chat)
      else group.inactive.push(chat)
    }
  }
  return [...byKey.values()].sort((a, b) => a.label.localeCompare(b.label))
})

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
  display: flex;
  gap: 6px;
  padding: 8px 2px 0;
}

.field--sm {
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  border-radius: var(--radius-sm, 6px);
  background: var(--surface-input);
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 12px;
}

.side__chat-group + .side__chat-group {
  margin-top: 6px;
}

.side__inactive {
  margin-top: 2px;
}

.side__inactive-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  cursor: pointer;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  list-style: none;
}

.side__inactive-summary::-webkit-details-marker {
  display: none;
}

.side__inactive-summary .material-icons {
  font-size: 16px;
  transition: transform var(--transition);
}

.side__inactive[open] .side__inactive-summary .material-icons {
  transform: rotate(180deg);
}

.side__list {
  padding: 6px;
}

.side__group {
  padding: 12px 10px 6px;
}

.side__empty {
  padding: 24px 14px;
  color: var(--text-dim);
  text-align: center;
}

.chat-list-item {
  display: flex;
  align-items: center;
  min-width: 0;
}

.chat-list-item > .row-item {
  flex: 1;
  min-width: 0;
}

.chat-list-item__menu {
  flex: none;
  color: var(--text-muted);
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
