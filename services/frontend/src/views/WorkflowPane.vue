<template>
  <div v-if="!name" class="empty">
    <span class="material-icons" style="font-size: 40px">account_tree</span>
    <div class="pane-head__title">Workflows</div>
  </div>

  <!-- a draft waiting for a human -->
  <div v-else-if="draft" class="stack grow">
    <header class="pane-head">
      <button class="btn btn--icon pane-head__back" @click="$router.push('/workflows')">
        <span class="material-icons">arrow_back</span>
      </button>
      <div class="grow">
        <div class="pane-head__title truncate">{{ draft.name }}</div>
        <div class="caption dim truncate">{{ draft.meta?.message || 'Draft' }}</div>
      </div>
      <span class="chip" :class="validation?.valid ? 'chip--success' : 'chip--danger'">
        {{ validation?.valid ? 'validates' : 'has errors' }}
      </span>
      <button class="btn btn--danger btn--sm" @click="discard">Discard</button>
      <button
        class="btn btn--primary btn--sm" :disabled="!validation?.valid || approving"
        @click="approve"
      >
        {{ approving ? 'Deploying…' : 'Approve and deploy' }}
      </button>
    </header>

    <div class="pane-body scroll-y grow">
      <section v-if="validation?.steps?.length" class="block">
        <div class="row" style="flex-wrap: wrap">
          <span
            v-for="step in validation.steps" :key="step.step"
            class="chip" :class="step.ok ? 'chip--success' : 'chip--danger'"
          >{{ step.step }}</span>
        </div>
        <p v-for="error in validation.errors || []" :key="error" class="caption" style="color: var(--danger)">
          {{ error }}
        </p>
        <p v-for="note in validation.warnings || []" :key="note" class="caption" style="color: var(--warning)">
          {{ note }}
        </p>
      </section>

      <section class="block">
        <div class="section-label">Diff</div>
        <pre class="code" style="margin-top: 8px"><span
          v-for="(line, index) in diffLines(draft.diff)" :key="index" :class="line.cls"
        >{{ line.text }}
</span></pre>
      </section>
    </div>
  </div>

  <!-- a published workflow -->
  <div v-else-if="workflow" class="stack grow">
    <header class="pane-head">
      <button class="btn btn--icon pane-head__back" @click="$router.push('/workflows')">
        <span class="material-icons">arrow_back</span>
      </button>
      <div class="avatar-sq" :style="avatarStyle(workflow.name)">
        <span class="material-icons">account_tree</span>
      </div>
      <div class="grow">
        <div class="pane-head__title truncate">{{ workflow.title || workflow.name }}</div>
        <div class="caption dim truncate">{{ workflow.description || workflow.name }}</div>
      </div>
      <span v-if="disabled" class="chip chip--warning">disabled</span>
      <span v-else-if="workflow.schedule" class="chip chip--accent">
        <span class="material-icons" style="font-size: 13px">schedule</span>{{ workflow.schedule.cron }}
      </span>
      <button class="btn btn--icon">
        <span class="material-icons">more_vert</span>
        <q-menu anchor="bottom right" self="top right" class="pick-menu">
          <button class="pick-menu__item" @click="toggleDisabled">
            <span class="material-icons pick__icon">{{ disabled ? 'play_circle' : 'pause_circle' }}</span>
            {{ disabled ? 'Enable' : 'Disable' }}
          </button>
          <button class="pick-menu__item" @click="remove">
            <span class="material-icons pick__icon" style="color: var(--danger)">delete</span>Delete
          </button>
        </q-menu>
      </button>
    </header>

    <nav class="tabs">
      <button
        v-for="item in tabs" :key="item" class="tab"
        :class="{ 'tab--active': tab === item }" @click="tab = item"
      >{{ item }}</button>
    </nav>

    <div class="pane-body scroll-y grow">
      <template v-if="tab === 'Run'">
        <section v-if="disabled" class="notice">
          <span class="material-icons">pause_circle</span>
          <span class="grow">Disabled — triggers and schedules are refused.</span>
          <button class="btn btn--sm btn--outline" @click="toggleDisabled">Enable</button>
        </section>

        <section class="block">
          <div v-for="(schema, key) in inputProperties" :key="key" class="field-row">
            <label class="field-row__label">
              {{ key }}
              <span v-if="(workflow.manifest?.inputs?.required || []).includes(key)" class="dim">*</span>
            </label>
            <input v-model="inputs[key]" class="field" :placeholder="schema.description || schema.type || ''" />
          </div>
          <div class="row" style="margin-top: 14px">
            <button class="btn btn--primary" :disabled="running || disabled" @click="run">
              <span class="material-icons" style="font-size: 17px">play_arrow</span>
              {{ running ? 'Starting…' : 'Run now' }}
            </button>
            <input v-model="cron" class="field" style="width: 150px" placeholder="0 8 * * *" />
            <button class="btn btn--outline" :disabled="disabled" @click="schedule">
              {{ workflow.schedule ? 'Update schedule' : 'Schedule' }}
            </button>
            <button v-if="workflow.schedule" class="btn btn--danger" @click="unschedule">Unschedule</button>
          </div>
        </section>

        <section class="block">
          <div class="section-label">Results go to</div>
          <div class="row segmented" style="margin-top: 8px">
            <button
              v-for="option in chatModes" :key="option.value" class="segment"
              :class="{ 'segment--active': chatMode === option.value }"
              @click="setChatMode(option.value)"
            >{{ option.label }}</button>
          </div>
        </section>

        <section class="block">
          <div class="section-label">Trigger</div>
          <TriggerSnippet class="block__panel" :workflow="workflow.name" :inputs="inputs" />
        </section>
      </template>

      <section v-else-if="tab === 'Code'" class="block block--wide">
        <CodeViewer :files="files" />
      </section>

      <section v-else class="block">
        <RouterLink
          v-for="entry in workflow.runs || []" :key="entry.workflow_id"
          class="run-row" :to="`/runs/${entry.workflow_id}`"
        >
          <span class="chip" :class="`chip--${RUN_TONE[entry.status] || ''}`">{{ entry.status }}</span>
          <span class="mono grow truncate">{{ entry.workflow_id }}</span>
          <span class="caption dim">{{ entry.trigger }}</span>
          <span class="caption dim">{{ fullTime(entry.created_at) }}</span>
        </RouterLink>
        <p v-if="!workflow.runs?.length" class="caption dim">No runs yet.</p>
      </section>
    </div>
  </div>

  <div v-else class="empty" />
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import CodeViewer from '../components/CodeViewer.vue'
import TriggerSnippet from '../components/TriggerSnippet.vue'
import { RUN_TONE, avatarStyle, diffLines, fullTime } from '../format'
import { actions, store } from '../store'
import { api } from '../api'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const tabs = ['Run', 'Code', 'History']
const chatModes = [
  { value: 'same', label: 'One chat' },
  { value: 'new', label: 'A chat per run' }
]
const tab = ref('Run')
const workflow = ref(null)
const draft = ref(null)
const validation = ref(null)
const inputs = ref({})
const cron = ref('0 8 * * *')
const running = ref(false)
const approving = ref(false)

const name = computed(() => route.params.name || '')
const inputProperties = computed(() => workflow.value?.manifest?.inputs?.properties || {})
const disabled = computed(() => Boolean(workflow.value?.settings?.disabled))
const chatMode = computed(() => workflow.value?.settings?.chat_mode || 'same')

const files = computed(() => [
  { name: `${workflow.value.name}.py`, code: workflow.value.code, language: 'python', icon: 'description' },
  {
    name: 'manifest.json',
    code: JSON.stringify(workflow.value.manifest, null, 2),
    language: 'json',
    icon: 'data_object'
  }
])

async function load () {
  workflow.value = null
  draft.value = null
  validation.value = null
  if (!name.value) return
  if (store.drafts.some((item) => item.name === name.value)) {
    draft.value = await api.draft(name.value)
    validation.value = draft.value.validation || await api.validate(name.value, draft.value.code)
    return
  }
  workflow.value = await api.workflow(name.value)
  inputs.value = {}
  tab.value = 'Run'
  cron.value = workflow.value.schedule?.cron || '0 8 * * *'
}

function payload () {
  return Object.fromEntries(Object.entries(inputs.value).filter(([, value]) => value !== '' && value != null))
}

async function run () {
  running.value = true
  try {
    const started = await api.runWorkflow(workflow.value.name, payload())
    router.push(`/runs/${started.workflow_id}`)
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    running.value = false
  }
}

async function schedule () {
  try {
    await api.schedule(workflow.value.name, cron.value, payload())
    await actions.loadWorkflows()
    load()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  }
}

async function unschedule () {
  await api.unschedule(workflow.value.name)
  await actions.loadWorkflows()
  load()
}

async function toggleDisabled () {
  workflow.value.settings = await api.workflowSettings(workflow.value.name, { disabled: !disabled.value })
  actions.loadWorkflows()
}

async function setChatMode (mode) {
  workflow.value.settings = await api.workflowSettings(workflow.value.name, { chat_mode: mode })
}

function remove () {
  $q.dialog({ title: 'Delete workflow', message: `Delete ${workflow.value.name}?`, cancel: true })
    .onOk(async () => {
      await api.deleteWorkflow(workflow.value.name)
      await actions.loadWorkflows()
      router.push('/workflows')
    })
}

async function approve () {
  approving.value = true
  try {
    await api.approveDraft(draft.value.name)
    await actions.loadWorkflows()
    load()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    approving.value = false
  }
}

async function discard () {
  await api.discardDraft(draft.value.name)
  await actions.loadWorkflows()
  router.push('/workflows')
}

watch(name, load)
onMounted(load)
</script>

<style scoped>
.pane-body {
  padding: 18px 22px 40px;
}

.block {
  max-width: 760px;
  margin-bottom: 26px;
}

.block--wide {
  max-width: 1100px;
}

.notice {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 760px;
  margin-bottom: 20px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--warning-soft);
  color: var(--warning);
  font-size: 13px;
}

.notice .material-icons {
  font-size: 18px;
}

.segmented {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
}

.segment {
  padding: 5px 12px;
  border: none;
  border-radius: var(--radius-xs);
  background: none;
  color: var(--text-muted);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
}

.segment:hover {
  color: var(--text);
}

.segment--active {
  background: var(--accent-soft);
  color: var(--accent-hover);
}

.field-row {
  margin-top: 10px;
}

.field-row__label {
  display: block;
  margin-bottom: 5px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-muted);
}

.run-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: var(--radius-sm);
  color: inherit;
  text-decoration: none;
}

.run-row:hover {
  background: var(--surface-hover);
}

.avatar-sq {
  display: grid;
  place-items: center;
  flex: none;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-sm);
  color: #fff;
}

.avatar-sq .material-icons {
  font-size: 18px;
}

.pane-head__back {
  display: none;
}

@media (max-width: 900px) {
  .pane-head__back {
    display: grid;
  }

  .pane-body {
    padding: 16px 14px 32px;
  }
}
</style>
