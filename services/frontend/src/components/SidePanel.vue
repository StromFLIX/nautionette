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
      </div>
      <div class="side__search">
        <span class="material-icons">search</span>
        <input v-model="query" class="side__search-input" :placeholder="`Search ${heading.toLowerCase()}`" />
        <button v-if="query" class="btn btn--icon btn--sm" @click="query = ''">
          <span class="material-icons" style="font-size: 16px">close</span>
        </button>
      </div>
    </header>

    <div class="side__list scroll-y grow">
      <!-- chats -->
      <template v-if="section === 'chats'">
        <RouterLink
          v-for="chat in filteredChats" :key="chat.id" :to="`/chats/${chat.id}`"
          class="row-item" :class="{ 'row-item--active': route.params.id === chat.id }"
        >
          <div class="avatar" :style="avatarStyle(chat.id)">{{ initials(chat.title) }}</div>
          <div class="grow">
            <div class="row">
              <span class="row-item__title grow truncate">{{ chat.title }}</span>
              <span class="row-item__time">{{ shortTime(chat.updated_at) }}</span>
            </div>
            <div class="row-item__sub truncate">
              <span v-if="chat.last_message?.role === 'user'" class="dim">You: </span>
              {{ chat.last_message?.preview || 'No messages yet' }}
            </div>
          </div>
        </RouterLink>
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
            <div class="row-item__sub truncate">{{ workflow.description || workflow.name }}</div>
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
import { RUN_TONE, avatarStyle, initials, shortTime } from '../format'
import { actions, store } from '../store'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const query = ref('')

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
  letter-spacing: -0.01em;
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

.row-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  color: inherit;
  text-decoration: none;
  cursor: pointer;
  transition: background var(--transition);
}

.row-item:hover {
  background: var(--surface-hover);
}

.row-item--active {
  background: var(--accent-soft);
}

.row-item--active .row-item__sub {
  color: #b9cdf5;
}

.row-item__title {
  font-size: 13.5px;
  font-weight: 550;
}

.row-item__sub {
  font-size: 12.5px;
  color: var(--text-muted);
}

.row-item__time {
  flex: none;
  font-size: 11px;
  color: var(--text-dim);
}

.row-item__pin {
  font-size: 15px;
  color: var(--accent-hover);
}

.avatar {
  display: grid;
  place-items: center;
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  color: #fff;
  font-size: 13px;
  font-weight: 650;
  letter-spacing: 0.02em;
}

.avatar--square {
  border-radius: var(--radius-md);
}

.avatar--draft {
  background: var(--warning-soft);
  color: var(--warning);
}

.avatar .material-icons {
  font-size: 20px;
}
</style>
