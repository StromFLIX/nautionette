<template>
  <div v-if="!name" class="empty">
    <span class="material-icons" style="font-size: 40px">account_tree</span>
    <div class="pane-head__title">Workflows</div>
  </div>

  <!-- a draft waiting for a human -->
  <div v-else-if="draft" class="stack grow">
    <header class="pane-head draft-head">
      <button class="btn btn--icon pane-head__back" @click="backTo('/workflows')">
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

    <nav class="tabs" aria-label="Draft views">
      <button v-for="item in ['Flow', 'Code diff']" :key="item" class="tab" :class="{ 'tab--active': tab === item }" @click="tab = item">{{ item }}</button>
    </nav>
    <WorkflowGraph v-if="tab === 'Flow'" :graph="draftGraph">
      <template #source>
        <select v-model="draftMode" class="field flow-picker" aria-label="Draft comparison version">
          <option value="changes">{{ draft.previous_graph ? 'Changes' : 'New workflow' }}</option>
          <option value="proposed">Proposed</option>
          <option v-if="draft.previous_graph" value="deployed">Deployed</option>
        </select>
      </template>
    </WorkflowGraph>
    <div v-else class="pane-body scroll-y grow">
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
      <button class="btn btn--icon pane-head__back" @click="backTo('/workflows')">
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
      <span
        v-else-if="workflow.schedule" class="chip chip--accent"
        :title="`${workflow.schedule.description} · ${workflow.schedule.timezone}`"
      >
        <span class="material-icons" style="font-size: 13px">schedule</span>
        {{ workflow.schedule.next_run
          ? `Next ${scheduleTime(workflow.schedule.next_run, workflow.schedule.timezone)}`
          : workflow.schedule.description }}
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

    <template v-if="tab === 'Flow'">
      <ExecutionFlow v-if="selectedRun" :key="selectedRun" :workflow-id="selectedRun">
        <template #source>
          <select v-model="selectedRun" class="field flow-picker" aria-label="Flow source">
            <option value="">Definition</option>
            <option v-for="entry in workflow.runs || []" :key="entry.workflow_id" :value="entry.workflow_id">{{ entry.status }} - {{ fullTime(entry.created_at) }}</option>
          </select>
        </template>
      </ExecutionFlow>
      <WorkflowGraph v-else :key="name" :graph="workflow.graph">
        <template #source>
          <select v-model="selectedRun" class="field flow-picker" aria-label="Flow source">
            <option value="">Definition</option>
            <option v-for="entry in workflow.runs || []" :key="entry.workflow_id" :value="entry.workflow_id">{{ entry.status }} - {{ fullTime(entry.created_at) }}</option>
          </select>
        </template>
      </WorkflowGraph>
    </template>
    <div v-else class="pane-body scroll-y grow">
      <template v-if="tab === 'Run'">
        <section v-if="disabled" class="notice">
          <span class="material-icons">pause_circle</span>
          <span class="grow">Disabled — triggers and schedules are refused.</span>
          <button class="btn btn--sm btn--outline" @click="toggleDisabled">Enable</button>
        </section>

        <section class="block">
          <div v-for="(schema, key) in inputProperties" :key="key" class="field-row">
            <label class="field-row__label" :for="`workflow-input-${key}`">
              {{ key }}
              <span v-if="(workflow.manifest?.inputs?.required || []).includes(key)" class="dim">*</span>
            </label>
            <input
              :id="`workflow-input-${key}`" v-model="inputs[key]" class="field"
              :placeholder="schema.description || schema.type || ''"
            />
          </div>
          <div class="row" style="margin-top: 14px">
            <button class="btn btn--primary" :disabled="running || disabled" @click="run">
              <span class="material-icons" style="font-size: 17px">play_arrow</span>
              {{ running ? 'Starting…' : 'Run now' }}
            </button>
          </div>
        </section>

        <section class="block schedule-block">
          <div class="section-label">Schedule</div>

          <div v-if="workflow.schedule" class="schedule-current">
            <span class="material-icons schedule-current__icon">event_repeat</span>
            <div class="schedule-current__rule">
              <strong>{{ workflow.schedule.description }}</strong>
              <span class="caption dim">{{ workflow.schedule.timezone }}</span>
            </div>
            <div class="schedule-current__next">
              <span class="caption dim">Next run</span>
              <strong>{{ workflow.schedule.next_run
                ? scheduleTime(workflow.schedule.next_run, workflow.schedule.timezone)
                : 'Calculating…' }}</strong>
            </div>
          </div>

          <div v-if="scheduleFrequency !== 'custom'" class="schedule-grid">
            <label class="schedule-field">
              <span>Repeat</span>
              <select v-model="scheduleFrequency" class="field">
                <option value="hourly">Every hour</option>
                <option value="daily">Every day</option>
                <option value="weekly">Selected days</option>
                <option value="monthly">Every month</option>
              </select>
            </label>

            <label v-if="scheduleFrequency === 'hourly'" class="schedule-field">
              <span>At minute</span>
              <input v-model.number="scheduleMinute" class="field" type="number" min="0" max="59" />
            </label>
            <label v-else class="schedule-field">
              <span>At</span>
              <input v-model="scheduleAt" class="field" type="time" required />
            </label>

            <label v-if="scheduleFrequency === 'monthly'" class="schedule-field">
              <span>Day of month</span>
              <input v-model.number="scheduleMonthDay" class="field" type="number" min="1" max="31" />
            </label>

            <label class="schedule-field schedule-field--timezone">
              <span>Timezone</span>
              <input v-model.trim="scheduleTimezone" class="field" list="workflow-timezones" autocomplete="off" />
              <datalist id="workflow-timezones">
                <option v-for="zone in timezones" :key="zone" :value="zone" />
              </datalist>
            </label>
          </div>

          <div v-if="scheduleFrequency === 'weekly'" class="schedule-days">
            <span class="schedule-days__label">Run on</span>
            <div class="segmented" aria-label="Days of the week">
              <button
                v-for="day in weekDays" :key="day.value" type="button" class="segment schedule-day"
                :class="{ 'segment--active': scheduleDays.includes(day.value) }"
                :aria-pressed="scheduleDays.includes(day.value)"
                @click="toggleScheduleDay(day.value)"
              >{{ day.label }}</button>
            </div>
          </div>

          <div class="row schedule-actions">
            <button
              v-if="scheduleFrequency === 'custom'" class="btn btn--primary"
              :disabled="disabled || scheduling" @click="beginScheduleReplacement"
            >
              <span class="material-icons" style="font-size: 17px">edit_calendar</span>
              Replace schedule
            </button>
            <button
              v-else
              class="btn btn--primary" :disabled="disabled || scheduling || !scheduleReady"
              @click="schedule"
            >
              <span class="material-icons" style="font-size: 17px">event_repeat</span>
              {{ scheduling ? 'Saving…' : workflow.schedule ? 'Update schedule' : 'Save schedule' }}
            </button>
            <button v-if="workflow.schedule" class="btn btn--danger" :disabled="scheduling" @click="unschedule">
              <span class="material-icons" style="font-size: 17px">event_busy</span>
              Remove schedule
            </button>
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

  <div v-else class="empty">
    <span>{{ loadError || 'Loading...' }}</span>
    <button v-if="loadError" class="btn btn--outline" @click="load">Retry</button>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import CodeViewer from '../components/CodeViewer.vue'
import TriggerSnippet from '../components/TriggerSnippet.vue'
import WorkflowGraph from '../components/WorkflowGraph.vue'
import ExecutionFlow from '../components/ExecutionFlow.vue'
import { compareGraphs } from '../flow'
import { RUN_TONE, avatarStyle, diffLines, fullTime, scheduleTime } from '../format'
import { backTo } from '../router'
import { actions, store } from '../store'
import { api } from '../api'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const tabs = ['Flow', 'Run', 'Code', 'History']
const chatModes = [
  { value: 'same', label: 'One chat' },
  { value: 'new', label: 'A chat per run' }
]
const tab = ref('Flow')
const selectedRun = ref('')
const draftMode = ref('changes')
const loadError = ref('')
let loadVersion = 0
const workflow = ref(null)
const draft = ref(null)
const validation = ref(null)
const inputs = ref({})
const running = ref(false)
const scheduling = ref(false)
const approving = ref(false)
const scheduleFrequency = ref('daily')
const scheduleAt = ref('')
const scheduleMinute = ref(0)
const scheduleDays = ref([])
const scheduleMonthDay = ref(new Date().getDate())
const scheduleTimezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
const weekDays = [
  { value: 'monday', label: 'Mon' },
  { value: 'tuesday', label: 'Tue' },
  { value: 'wednesday', label: 'Wed' },
  { value: 'thursday', label: 'Thu' },
  { value: 'friday', label: 'Fri' },
  { value: 'saturday', label: 'Sat' },
  { value: 'sunday', label: 'Sun' }
]
const timezones = [...new Set([
  scheduleTimezone.value,
  'UTC',
  ...(typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [])
])]

const name = computed(() => route.params.name || '')
const inputProperties = computed(() => workflow.value?.manifest?.inputs?.properties || {})
const disabled = computed(() => Boolean(workflow.value?.settings?.disabled))
const chatMode = computed(() => workflow.value?.settings?.chat_mode || 'same')
const scheduleReady = computed(() => {
  if (!scheduleTimezone.value) return false
  if (scheduleFrequency.value === 'custom') return false
  if (scheduleFrequency.value === 'hourly') {
    return scheduleMinute.value >= 0 && scheduleMinute.value <= 59
  }
  if (!scheduleAt.value) return false
  if (scheduleFrequency.value === 'weekly') return scheduleDays.value.length > 0
  if (scheduleFrequency.value === 'monthly') {
    return scheduleMonthDay.value >= 1 && scheduleMonthDay.value <= 31
  }
  return true
})
const draftGraph = computed(() => {
  if (!draft.value) return null
  if (draftMode.value === 'proposed') return draft.value.graph
  if (draftMode.value === 'deployed') return draft.value.previous_graph
  return compareGraphs(draft.value.previous_graph, draft.value.graph)
})

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
  const version = ++loadVersion
  workflow.value = null
  draft.value = null
  validation.value = null
  loadError.value = ''
  tab.value = 'Flow'
  selectedRun.value = ''
  draftMode.value = 'changes'
  if (!name.value) return
  try {
    if (store.drafts.some((item) => item.name === name.value)) {
      const result = await api.draft(name.value)
      if (version !== loadVersion) return
      draft.value = result
      const report = result.validation || await api.validate(name.value, result.code)
      if (version === loadVersion) validation.value = report
      return
    }
    const result = await api.workflow(name.value)
    if (version !== loadVersion) return
    workflow.value = result
    inputs.value = { ...(result.schedule?.input || {}) }
    loadSchedule(result.schedule)
  } catch (error) {
    if (version === loadVersion) loadError.value = error.message
  }
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
  scheduling.value = true
  try {
    const definition = {
      frequency: scheduleFrequency.value,
      timezone: scheduleTimezone.value,
      input: payload()
    }
    if (scheduleFrequency.value === 'hourly') definition.minute = scheduleMinute.value
    else definition.at = scheduleAt.value
    if (scheduleFrequency.value === 'weekly') definition.days = scheduleDays.value
    if (scheduleFrequency.value === 'monthly') definition.day = scheduleMonthDay.value
    const saved = await api.schedule(workflow.value.name, definition)
    workflow.value.schedule = saved
    loadSchedule(saved)
    await actions.loadWorkflows()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    scheduling.value = false
  }
}

async function unschedule () {
  scheduling.value = true
  try {
    await api.unschedule(workflow.value.name)
    workflow.value.schedule = null
    await actions.loadWorkflows()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    scheduling.value = false
  }
}

function loadSchedule (definition) {
  const supported = ['hourly', 'daily', 'weekly', 'monthly']
  scheduleFrequency.value = definition && !supported.includes(definition.frequency)
    ? 'custom'
    : definition?.frequency || 'daily'
  scheduleAt.value = definition?.at || ''
  scheduleMinute.value = definition?.minute ?? 0
  scheduleDays.value = [...(definition?.days || [])]
  scheduleMonthDay.value = definition?.day || new Date().getDate()
  scheduleTimezone.value = definition?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

function beginScheduleReplacement () {
  scheduleFrequency.value = 'daily'
  scheduleAt.value = ''
  scheduleDays.value = []
  scheduleMonthDay.value = new Date().getDate()
  scheduleTimezone.value = workflow.value.schedule?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

function toggleScheduleDay (day) {
  scheduleDays.value = scheduleDays.value.includes(day)
    ? scheduleDays.value.filter((value) => value !== day)
    : [...scheduleDays.value, day]
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
watch(() => store.drafts.some((item) => item.name === name.value), load)
onMounted(load)
</script>

<style scoped>
.flow-picker {
  width: 100%;
  max-width: 290px;
  min-width: 0;
  height: 32px;
  padding: 4px 8px;
  font-size: 12px;
  text-overflow: ellipsis;
}

.draft-head {
  flex-wrap: wrap;
}

.draft-head > .grow {
  min-width: 100px;
}

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

.schedule-block {
  padding-top: 2px;
}

.schedule-current {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) minmax(180px, auto);
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  padding: 10px 0 10px 12px;
  border-left: 3px solid var(--accent);
}

.schedule-current__icon {
  color: var(--accent);
  font-size: 20px;
}

.schedule-current__rule,
.schedule-current__next {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.schedule-current__next {
  text-align: right;
}

.schedule-grid {
  display: grid;
  grid-template-columns: minmax(140px, 0.8fr) minmax(120px, 0.6fr) minmax(180px, 1.4fr);
  gap: 10px;
  margin-top: 14px;
}

.schedule-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
  color: var(--text-muted);
  font-size: 12.5px;
  font-weight: 500;
}

.schedule-field--timezone {
  grid-column: -2 / -1;
}

.schedule-days {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}

.schedule-days__label {
  color: var(--text-muted);
  font-size: 12.5px;
  font-weight: 500;
}

.schedule-day {
  width: 42px;
  padding-inline: 0;
}

.schedule-actions {
  margin-top: 14px;
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
    padding: 16px max(14px, env(safe-area-inset-right)) max(32px, env(safe-area-inset-bottom)) max(14px, env(safe-area-inset-left));
  }

  .pane-body .row {
    flex-wrap: wrap;
  }

  .pane-body .field {
    min-width: 0;
  }

  .schedule-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .schedule-field--timezone {
    grid-column: auto;
  }
}

@media (max-width: 560px) {
  .pane-head .chip {
    max-width: 92px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .pane-head .btn--danger {
    padding: 0 8px;
  }

  .pane-head .btn--primary {
    width: 36px;
    padding: 0;
    overflow: hidden;
    font-size: 0;
  }

  .pane-head .btn--primary::after {
    content: '✓';
    font-size: 16px;
  }

  .run-row {
    flex-wrap: wrap;
  }

  .run-row .mono {
    flex-basis: calc(100% - 90px);
  }

  .schedule-current {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .schedule-current__next {
    grid-column: 2;
    text-align: left;
  }

  .schedule-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .schedule-days {
    align-items: flex-start;
    flex-direction: column;
  }

  .schedule-days .segmented {
    display: grid;
    width: 100%;
    grid-template-columns: repeat(7, minmax(0, 1fr));
  }

  .schedule-day {
    width: auto;
  }
}
</style>
