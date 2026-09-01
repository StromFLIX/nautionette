<template>
  <q-page class="q-pa-md">
    <div class="panel q-pa-md">
      <div class="row items-center q-mb-md">
        <div class="text-h6">Runs</div>
        <q-space />
        <q-btn dense flat round icon="refresh" :loading="loading" @click="load" />
      </div>
      <q-table
        flat dense :rows="runs" :columns="columns" row-key="workflow_id"
        :pagination="{ rowsPerPage: 25 }" :loading="loading"
      >
        <template #body-cell-status="props">
          <q-td :props="props">
            <q-badge :color="badge(props.value)">{{ props.value }}</q-badge>
          </q-td>
        </template>
        <template #body-cell-actions="props">
          <q-td :props="props">
            <q-btn dense flat size="sm" label="details" @click="details(props.row)" />
          </q-td>
        </template>
      </q-table>
    </div>

    <q-dialog v-model="open">
      <q-card style="width: 760px; max-width: 95vw">
        <q-card-section class="text-h6">{{ current?.run?.workflow || current?.temporal?.workflow_type }}</q-card-section>
        <q-card-section class="q-pt-none">
          <pre class="code mono">{{ JSON.stringify(current, null, 2) }}</pre>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat label="Close" v-close-popup />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import { inject, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const runs = ref([])
const loading = ref(false)
const open = ref(false)
const current = ref(null)
const onLiveEvent = inject('onLiveEvent', () => () => {})
let off = () => {}

const columns = [
  { name: 'workflow', label: 'Workflow', field: 'workflow', align: 'left' },
  { name: 'workflow_id', label: 'Run', field: 'workflow_id', align: 'left', classes: 'mono' },
  { name: 'trigger', label: 'Trigger', field: 'trigger', align: 'left' },
  { name: 'status', label: 'Status', field: 'status', align: 'left' },
  {
    name: 'created_at',
    label: 'Started',
    field: 'created_at',
    align: 'left',
    format: (value) => new Date(value * 1000).toLocaleString()
  },
  { name: 'actions', label: '', field: 'actions', align: 'right' }
]

function badge (status) {
  return { completed: 'green', running: 'blue', failed: 'red', canceled: 'grey', terminated: 'red' }[status] || 'grey'
}

async function load () {
  loading.value = true
  try {
    runs.value = (await api.runs()).runs
  } finally {
    loading.value = false
  }
}

async function details (row) {
  current.value = await api.run(row.workflow_id)
  open.value = true
}

onMounted(() => {
  load()
  off = onLiveEvent((event) => {
    if (event.kind?.startsWith('run.')) load()
  })
})
onUnmounted(() => off())
</script>
