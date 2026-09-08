<template>
  <section v-if="visibleProjects.length || error" class="project-changes" aria-label="Project changes">
    <p v-if="error" class="project-changes__notice" role="status">{{ error }}</p>
    <details v-for="project in visibleProjects" :key="`${chatId}:${project.id}`" class="project-changes__project">
      <summary :aria-label="`${project.full_name}: ${project.error ? 'changes unavailable' : `${project.file_count} changed files`}`">
        <span v-if="!project.error" class="project-changes__stats" :aria-label="`${project.additions} lines added, ${project.deletions} lines deleted`">
          <span class="project-changes__added">+{{ number(project.additions) }}</span>
          <span class="project-changes__deleted">−{{ number(project.deletions) }}</span>
        </span>
        <span v-else class="project-changes__unavailable">Unavailable</span>
        <span class="project-changes__name" :title="project.full_name">{{ project.full_name }}</span>
        <span v-if="!project.error" class="project-changes__count">{{ number(project.file_count) }} {{ project.file_count === 1 ? 'file' : 'files' }}</span>
        <span class="material-icons project-changes__arrow" aria-hidden="true">expand_more</span>
      </summary>
      <div class="project-changes__panel">
        <p v-if="project.error" class="project-changes__notice" role="status">{{ project.error }}</p>
        <template v-else>
          <p class="project-changes__scope">{{ project.scope === 'uncommitted' ? 'Uncommitted changes · starting revision unavailable for this older workspace' : 'Pending changes · unpushed commits and uncommitted edits' }}</p>
          <div class="project-changes__files">
            <ProjectChangeTree :nodes="project.tree" :label="`Changed files in ${project.full_name}`" />
          </div>
        </template>
      </div>
    </details>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { buildChangeTree } from '../projectChanges'
import ProjectChangeTree from './ProjectChangeTree.vue'

const props = defineProps({
  chatId: { type: String, required: true },
  projectIds: { type: Array, default: () => [] },
  running: { type: Boolean, default: false }
})
const projects = ref([])
const error = ref('')
const visibleProjects = computed(() => projects.value.filter((project) =>
  props.projectIds.includes(project.id) && (project.file_count > 0 || project.error))
  .map((project) => ({ ...project, tree: buildChangeTree(project.files) })))
const number = (value) => new Intl.NumberFormat().format(value)

watch([() => props.chatId, () => props.projectIds.join(','), () => props.running], ([chatId, selected], previous, onCleanup) => {
  if (previous?.[0] !== chatId) projects.value = []
  error.value = ''
  if (!chatId || !selected) return
  let stopped = false
  let pending = false
  let timer
  const controller = new AbortController()
  async function refresh () {
    clearTimeout(timer)
    if (stopped || pending || document.visibilityState === 'hidden') return
    pending = true
    try {
      const result = await api.projectChanges(chatId, AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]))
      if (!stopped) {
        projects.value = result.projects || []
        error.value = ''
      }
    } catch {
      if (!stopped) error.value = projects.value.length
        ? 'Change updates unavailable · showing last known counts'
        : 'Project changes unavailable · retrying automatically'
    } finally {
      pending = false
      if (!stopped) timer = setTimeout(refresh, props.running ? 3000 : 15000)
    }
  }
  // Poll only the visible conversation, without overlapping requests. Refresh at turn end,
  // on selection changes, and when returning to the tab; stale navigation responses are ignored.
  document.addEventListener('visibilitychange', refresh)
  window.addEventListener('focus', refresh)
  refresh()
  onCleanup(() => {
    stopped = true
    clearTimeout(timer)
    controller.abort()
    document.removeEventListener('visibilitychange', refresh)
    window.removeEventListener('focus', refresh)
  })
}, { immediate: true })
</script>

<style scoped>
.project-changes {
  margin: 0 auto 8px;
  max-width: var(--content-width);
  max-height: 40dvh;
  overflow-y: auto;
  overscroll-behavior: contain;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-panel);
  font-size: 12px;
}
.project-changes__project + .project-changes__project { border-top: 1px solid var(--border); }
summary {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 38px;
  padding: 8px 12px;
  cursor: pointer;
  list-style: none;
  transition: background 140ms ease;
}
summary::-webkit-details-marker { display: none; }
summary:hover { background: var(--surface-hover); }
summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: 8px; }
.project-changes__stats { display: inline-flex; gap: 8px; flex-shrink: 0; font-variant-numeric: tabular-nums; font-family: var(--font-mono, monospace); font-size: 11px; }
.project-changes__added { color: var(--success); }
.project-changes__deleted { color: var(--danger); }
.project-changes__name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-muted); }
.project-changes__count { flex-shrink: 0; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.project-changes__arrow { font-size: 17px; color: var(--text-muted); transition: transform 160ms ease; }
[open] > summary .project-changes__arrow { transform: rotate(180deg); }
.project-changes__panel { border-top: 1px solid var(--border); }
.project-changes__scope { margin: 9px 12px; color: var(--text-muted); font-size: 10px; }
.project-changes__files { list-style: none; margin: 0; padding: 0 6px 6px; max-height: min(260px, 30dvh); overflow-y: auto; overscroll-behavior: contain; }
.project-changes__notice, .project-changes__unavailable { color: var(--warning); font-size: 11px; }
.project-changes__notice { margin: 8px 12px; }
@media (max-width: 480px) {
  summary { gap: 8px; padding: 8px; }
  .project-changes { margin-inline: 0; }
  .project-changes__stats { gap: 5px; }
}
@media (prefers-reduced-motion: reduce) {
  summary, .project-changes__arrow { transition: none; }
}
</style>
