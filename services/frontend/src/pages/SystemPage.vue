<template>
  <q-page class="q-pa-md">
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6">
        <div class="panel q-pa-md">
          <div class="text-h6 q-mb-md">Components</div>
          <q-list separator>
            <q-item v-for="component in status.components || []" :key="component.name">
              <q-item-section avatar>
                <q-icon
                  :name="component.status === 'ok' ? 'check_circle' : 'error'"
                  :color="component.status === 'ok' ? 'green' : 'red'"
                />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ component.name }}</q-item-label>
                <q-item-label caption class="mono" lines="3">{{ render(component.detail) }}</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </div>

        <div class="panel q-pa-md q-mt-md">
          <div class="text-h6 q-mb-sm">Agent sets</div>
          <div class="text-caption text-grey-5 q-mb-md">
            Pi scales to zero: a container is started per call and gone when the call returns.
          </div>
          <q-list separator>
            <q-item v-for="set in status.agent_sets || []" :key="set.name">
              <q-item-section>
                <q-item-label>{{ set.name }}</q-item-label>
                <q-item-label caption class="mono">{{ set.image }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-badge :color="set.ready ? 'green' : 'orange'">
                  {{ set.ready ? 'image ready' : 'building' }}
                </q-badge>
              </q-item-section>
            </q-item>
            <q-item v-if="!(status.agent_sets || []).length">
              <q-item-section class="text-grey-6">No agent sets reported yet.</q-item-section>
            </q-item>
          </q-list>
          <div class="q-mt-md text-caption">
            Model: <code class="mono">{{ status.model }}</code>
            <q-badge class="q-ml-sm" :color="status.model_key_present ? 'green' : 'orange'">
              {{ status.model_key_present ? 'key configured' : 'no key' }}
            </q-badge>
          </div>
        </div>
      </div>

      <div class="col-12 col-md-6">
        <div class="panel q-pa-md">
          <div class="text-h6 q-mb-sm">Live events</div>
          <q-list dense separator style="max-height: 70vh; overflow: auto">
            <q-item v-for="(event, index) in events" :key="index">
              <q-item-section>
                <q-item-label class="mono">{{ event.kind }}</q-item-label>
                <q-item-label caption class="mono">{{ summary(event) }}</q-item-label>
              </q-item-section>
              <q-item-section side class="text-caption">
                {{ new Date(event.at * 1000).toLocaleTimeString() }}
              </q-item-section>
            </q-item>
            <q-item v-if="!events.length">
              <q-item-section class="text-grey-6">Waiting for the system to do something.</q-item-section>
            </q-item>
          </q-list>
        </div>
      </div>
    </div>
  </q-page>
</template>

<script setup>
import { inject, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

defineProps({ status: { type: Object, default: () => ({}) } })

const events = ref([])
const onLiveEvent = inject('onLiveEvent', () => () => {})
let off = () => {}

function render (detail) {
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}

function summary (event) {
  const { kind, at, ...rest } = event
  return JSON.stringify(rest).slice(0, 160)
}

onMounted(async () => {
  events.value = (await api.events()).events.slice().reverse()
  off = onLiveEvent((event) => {
    events.value.unshift(event)
    events.value = events.value.slice(0, 200)
  })
})
onUnmounted(() => off())
</script>
