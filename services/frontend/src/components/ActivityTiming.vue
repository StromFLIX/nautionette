<template>
  <div v-if="parts.length" class="activity-timing" role="group" aria-label="Response timing" :title="explanation">
    <span v-if="timing.partial" class="activity-timing__partial">Recorded</span>
    <span v-for="part in parts" :key="part.key" class="activity-timing__part">{{ part.label }} {{ part.duration }}</span>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { activityBreakdown } from '../activity-timing'

const props = defineProps({
  timing: { type: Object, required: true },
  live: { type: Boolean, default: false }
})
const now = ref(Date.now())
let timer
watch(() => props.live && props.timing.active, (active) => {
  clearInterval(timer)
  now.value = Date.now()
  if (active) timer = setInterval(() => { now.value = Date.now() }, 250)
}, { immediate: true })
onUnmounted(() => clearInterval(timer))
const parts = computed(() => activityBreakdown(props.timing, props.live, now.value))
const explanation = computed(() => [
  'Elapsed time for this response, not just this tool group. Parallel tools count once. Thinking measures reported reasoning only; Reply measures streamed text. Model wait includes request preparation, waiting for the provider and unreported model work (which may include hidden reasoning). Tool input measures streamed tool arguments, not execution. Other includes setup, retries, compaction and unclassified time. Older recordings may include model waiting and tool preparation in Other.',
  props.timing.partial ? 'Interrupted: only time measured before the last saved update is included.' : ''
].filter(Boolean).join(' '))
</script>

<style scoped>
.activity-timing {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  margin: 0 0 8px 18px;
  color: var(--text-muted);
  font-size: 0.6875rem;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}
.activity-timing__part { white-space: nowrap; }
.activity-timing__part + .activity-timing__part::before {
  content: '·';
  margin-right: 8px;
  color: var(--text-dim);
}
</style>
