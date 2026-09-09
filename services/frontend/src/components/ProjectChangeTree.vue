<template>
  <ul class="change-tree" :aria-label="label">
    <li v-for="node in nodes" :key="`${node.children ? 'directory' : 'file'}:${node.path}`">
      <details v-if="node.children" class="change-tree__directory" open>
        <summary :aria-label="`Folder ${node.path}`">
          <span class="material-icons change-tree__arrow" aria-hidden="true">chevron_right</span>
          <span class="material-icons change-tree__folder" aria-hidden="true">folder</span>
          <span class="project-changes__path" :title="node.path">{{ node.name }}</span>
        </summary>
        <ProjectChangeTree :nodes="node.children" :label="`Changes in ${node.path}`" />
      </details>
      <div v-else class="project-changes__file">
        <span class="project-changes__status" :class="`project-changes__status--${node.status}`" :title="node.status" :aria-label="node.status">{{ statusLabel(node.status) }}</span>
        <span class="project-changes__path" :title="node.path"><span v-if="node.previous_path" class="project-changes__previous">{{ node.previous_path }} → </span>{{ node.name }}</span>
        <span v-if="node.binary" class="project-changes__file-note">Binary</span>
        <span v-else-if="node.additions === null || node.deletions === null" class="project-changes__file-note">Not counted</span>
        <span v-else class="project-changes__stats" :aria-label="`${node.additions} lines added, ${node.deletions} lines deleted`">
          <span class="project-changes__added">+{{ number(node.additions) }}</span>
          <span class="project-changes__deleted">−{{ number(node.deletions) }}</span>
        </span>
      </div>
    </li>
  </ul>
</template>

<script setup>
defineProps({ nodes: { type: Array, required: true }, label: { type: String, required: true } })
const number = (value) => new Intl.NumberFormat().format(value)
const statusLabel = (status) => ({ added: 'A', untracked: 'U', deleted: 'D', modified: 'M', typechanged: 'T', renamed: 'R', copied: 'C' })[status] || 'M'
</script>

<style scoped>
.change-tree { list-style: none; margin: 0; padding: 0; }
.change-tree .change-tree { margin-left: 12px; padding-left: 3px; border-left: 1px solid var(--border); }
summary { display: flex; align-items: center; gap: 5px; padding: 6px; cursor: pointer; list-style: none; border-radius: 5px; }
summary::-webkit-details-marker { display: none; }
summary:hover, .project-changes__file:hover { background: var(--surface-hover); }
summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.change-tree__arrow, .change-tree__folder { font-size: 0.875rem; color: var(--text-muted); }
[open] > summary > .change-tree__arrow { transform: rotate(90deg); }
.project-changes__file { display: flex; align-items: baseline; gap: 9px; padding: 6px; border-radius: 5px; }
.project-changes__status { flex: 0 0 12px; color: var(--text-muted); font: 0.625rem var(--font-mono, monospace); }
.project-changes__status--added, .project-changes__status--untracked { color: var(--success); }
.project-changes__status--deleted { color: var(--danger); }
.project-changes__path { flex: 1; min-width: 0; overflow-wrap: anywhere; white-space: pre-wrap; color: var(--text); font: 0.6875rem/1.5 var(--font-mono, monospace); }
.project-changes__previous { color: var(--text-dim); }
.project-changes__file-note { color: var(--text-muted); font-size: 0.625rem; flex-shrink: 0; }
.project-changes__stats { display: inline-flex; gap: 8px; flex-shrink: 0; font-variant-numeric: tabular-nums; font-family: var(--font-mono, monospace); font-size: 0.6875rem; }
.project-changes__added { color: var(--success); }
.project-changes__deleted { color: var(--danger); }
@media (max-width: 480px) { .project-changes__stats { gap: 5px; } }
</style>
