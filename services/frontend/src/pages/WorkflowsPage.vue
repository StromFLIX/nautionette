<template>
  <q-page class="q-pa-md">
    <div v-if="drafts.length" class="panel q-pa-md q-mb-md">
      <div class="text-subtitle1 q-mb-sm">
        <q-icon name="rate_review" class="q-mr-sm" />Waiting for approval
      </div>
      <q-list separator>
        <q-item v-for="draft in drafts" :key="draft.name">
          <q-item-section>
            <q-item-label>{{ draft.name }}</q-item-label>
            <q-item-label caption>{{ draft.meta?.message || draft.description || 'draft' }}</q-item-label>
          </q-item-section>
          <q-item-section side class="q-gutter-sm row no-wrap">
            <q-btn dense outline size="sm" label="Review" @click="review(draft.name)" />
            <q-btn dense flat size="sm" color="red-4" label="Discard" @click="discard(draft.name)" />
          </q-item-section>
        </q-item>
      </q-list>
    </div>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-4">
        <div class="panel">
          <q-list separator>
            <q-item-label header class="row items-center">
              Workflows
              <q-space />
              <q-btn dense flat round size="sm" icon="refresh" @click="load" />
            </q-item-label>
            <q-item
              v-for="workflow in workflows" :key="workflow.name" clickable v-ripple
              :active="workflow.name === selected?.name" active-class="bg-blue-grey-10"
              @click="select(workflow.name)"
            >
              <q-item-section>
                <q-item-label>{{ workflow.title || workflow.name }}</q-item-label>
                <q-item-label caption lines="2">{{ workflow.description }}</q-item-label>
              </q-item-section>
              <q-item-section side v-if="workflow.schedule">
                <q-icon name="schedule" color="primary" size="18px">
                  <q-tooltip>{{ workflow.schedule.cron }}</q-tooltip>
                </q-icon>
              </q-item-section>
            </q-item>
            <q-item v-if="!workflows.length">
              <q-item-section class="text-grey-6">
                Nothing yet. Promote a chat and it lands here.
              </q-item-section>
            </q-item>
          </q-list>
        </div>
      </div>

      <div class="col-12 col-md-8">
        <div v-if="!selected" class="panel q-pa-xl text-center text-grey-6">
          Pick a workflow to see its manifest, its code and its runs.
        </div>

        <div v-else class="panel q-pa-md">
          <div class="row items-center q-mb-sm">
            <div class="text-h6">{{ selected.title || selected.name }}</div>
            <q-space />
            <q-btn dense flat round icon="delete" color="red-4" @click="remove" />
          </div>
          <div class="text-grey-5 q-mb-md">{{ selected.description }}</div>

          <q-tabs v-model="tab" dense align="left" narrow-indicator class="text-grey-5">
            <q-tab name="run" label="Run" />
            <q-tab name="code" label="Code" />
            <q-tab name="manifest" label="Manifest" />
            <q-tab name="runs" label="History" />
          </q-tabs>
          <q-separator class="q-mb-md" />

          <div v-if="tab === 'run'">
            <div v-for="(schema, key) in inputProperties" :key="key" class="q-mb-sm">
              <q-input
                v-model="inputs[key]" outlined dense :label="key" :hint="schema.description"
              />
            </div>
            <div class="row q-gutter-sm q-mt-md items-center">
              <q-btn color="primary" unelevated icon="play_arrow" label="Run now" :loading="running" @click="run" />
              <q-input v-model="cron" outlined dense style="width: 170px" label="cron" placeholder="0 8 * * *" />
              <q-btn outline color="primary" icon="schedule" label="Schedule" @click="schedule" />
              <q-btn v-if="selected.schedule" flat color="red-4" label="Unschedule" @click="unschedule" />
            </div>
            <div v-if="selected.schedule" class="text-caption text-grey-5 q-mt-sm">
              Scheduled: <code>{{ selected.schedule.cron }}</code>
            </div>
            <div class="text-caption text-grey-6 q-mt-md">
              Webhook: <code class="mono">POST /api/triggers/{{ selected.name }}</code>
            </div>
          </div>

          <pre v-else-if="tab === 'code'" class="code mono">{{ selected.code }}</pre>

          <pre v-else-if="tab === 'manifest'" class="code mono">{{ JSON.stringify(selected.manifest, null, 2) }}</pre>

          <q-list v-else separator>
            <q-item v-for="entry in selected.runs || []" :key="entry.workflow_id">
              <q-item-section>
                <q-item-label class="mono">{{ entry.workflow_id }}</q-item-label>
                <q-item-label caption>{{ entry.trigger }} · {{ new Date(entry.created_at * 1000).toLocaleString() }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-badge :color="badge(entry.status)">{{ entry.status }}</q-badge>
              </q-item-section>
            </q-item>
            <q-item v-if="!selected.runs?.length">
              <q-item-section class="text-grey-6">No runs yet.</q-item-section>
            </q-item>
          </q-list>
        </div>
      </div>
    </div>

    <q-dialog v-model="reviewOpen">
      <q-card style="width: 900px; max-width: 95vw">
        <q-card-section class="row items-center">
          <div class="text-h6">Review {{ reviewing?.name }}</div>
          <q-space />
          <q-badge :color="reviewing?.validation?.valid ? 'green' : 'red'">
            {{ reviewing?.validation?.valid ? 'validates' : 'has errors' }}
          </q-badge>
        </q-card-section>
        <q-card-section v-if="reviewing?.validation?.steps?.length" class="q-pt-none">
          <q-chip
            v-for="step in reviewing.validation.steps" :key="step.step" dense square size="sm"
            :color="step.ok ? 'green-9' : 'red-9'" text-color="white"
          >
            {{ step.step }}
          </q-chip>
          <div v-for="error in reviewing.validation.errors || []" :key="error" class="text-red-4 text-caption">
            {{ error }}
          </div>
        </q-card-section>
        <q-card-section class="q-pt-none">
          <div class="text-caption text-grey-5 q-mb-xs">Diff a human can read</div>
          <pre class="code mono"><span
            v-for="(line, index) in diffLines" :key="index" :class="line.cls"
          >{{ line.text }}
</span></pre>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat label="Close" v-close-popup />
          <q-btn flat color="red-4" label="Discard" @click="discard(reviewing.name)" v-close-popup />
          <q-btn
            unelevated color="primary" label="Approve and deploy"
            :disable="!reviewing?.validation?.valid" :loading="approving" @click="approve"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const emit = defineEmits(['changed'])
const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const workflows = ref([])
const drafts = ref([])
const selected = ref(null)
const tab = ref('run')
const inputs = ref({})
const cron = ref('0 8 * * *')
const running = ref(false)
const approving = ref(false)
const reviewOpen = ref(false)
const reviewing = ref(null)

const inputProperties = computed(() => selected.value?.manifest?.inputs?.properties || {})
const diffLines = computed(() => (reviewing.value?.diff || '(new file)').split('\n').map((text) => ({
  text,
  cls: text.startsWith('+') ? 'diff-line-add' : text.startsWith('-') ? 'diff-line-del'
    : text.startsWith('@@') ? 'diff-line-meta' : ''
})))

function badge (status) {
  return { completed: 'green', running: 'blue', failed: 'red', canceled: 'grey' }[status] || 'grey'
}

async function load () {
  workflows.value = (await api.workflows()).workflows
  drafts.value = (await api.drafts()).drafts
}

async function select (name) {
  selected.value = await api.workflow(name)
  inputs.value = {}
  tab.value = 'run'
  router.replace(`/workflows/${name}`)
}

async function run () {
  running.value = true
  try {
    const payload = Object.fromEntries(Object.entries(inputs.value).filter(([, v]) => v !== '' && v != null))
    const started = await api.runWorkflow(selected.value.name, payload)
    $q.notify({ type: 'positive', message: `Started ${started.workflow_id}` })
    select(selected.value.name)
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    running.value = false
  }
}

async function schedule () {
  try {
    await api.schedule(selected.value.name, cron.value, inputs.value)
    $q.notify({ type: 'positive', message: `Scheduled ${cron.value}` })
    await load()
    select(selected.value.name)
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  }
}

async function unschedule () {
  await api.unschedule(selected.value.name)
  await load()
  select(selected.value.name)
}

async function remove () {
  $q.dialog({ title: 'Delete workflow', message: `Delete ${selected.value.name}?`, cancel: true })
    .onOk(async () => {
      await api.deleteWorkflow(selected.value.name)
      selected.value = null
      await load()
      emit('changed')
    })
}

async function review (name) {
  reviewing.value = await api.draft(name)
  if (!reviewing.value.validation) {
    reviewing.value.validation = await api.validate(name, reviewing.value.code)
  }
  reviewOpen.value = true
}

async function approve () {
  approving.value = true
  try {
    await api.approveDraft(reviewing.value.name)
    $q.notify({ type: 'positive', message: 'Deployed. The worker is reloading.' })
    reviewOpen.value = false
    await load()
    emit('changed')
    select(reviewing.value.name)
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    approving.value = false
  }
}

async function discard (name) {
  await api.discardDraft(name)
  await load()
  emit('changed')
}

watch(() => route.params.name, async (name) => {
  if (!name) return
  await load()
  const draft = drafts.value.find((item) => item.name === name)
  if (draft) review(name)
  else if (workflows.value.some((item) => item.name === name)) select(name)
})

onMounted(async () => {
  await load()
  const name = route.params.name
  if (!name) return
  if (drafts.value.some((item) => item.name === name)) review(name)
  else if (workflows.value.some((item) => item.name === name)) select(name)
})
</script>
