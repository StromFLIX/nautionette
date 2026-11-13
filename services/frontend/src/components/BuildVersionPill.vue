<template>
  <button type="button" class="version-pill" aria-label="Build versions" aria-haspopup="dialog" :aria-expanded="open">
    <span v-if="different" class="version-pill__segment version-pill__segment--client">{{ client.label }}</span>
    <span class="version-pill__segment">{{ server.label }}</span>
    <q-menu v-model="open" class="version-details" anchor="bottom right" self="top right" :offset="[0, 8]" @click.stop>
      <section role="dialog" aria-label="Build version details" class="version-details__body">
        <h2>Build versions</h2>
        <p v-if="different" class="version-details__note">Client and server are running different versions.</p>
        <dl>
          <div v-for="entry in entries" :key="entry.name" class="version-details__row">
            <dt>{{ entry.name }}</dt>
            <dd>
              <span class="mono">{{ entry.info.label }}</span>
              <a v-if="entry.info.label.startsWith('v')" :href="`${brand.repository}/releases/tag/${encodeURIComponent(entry.info.label)}`" target="_blank" rel="noopener noreferrer">Release notes<span class="sr-only"> for {{ entry.name }}</span></a>
              <a v-if="entry.commit" :href="`${brand.repository}/commit/${entry.commit}`" target="_blank" rel="noopener noreferrer">Commit {{ entry.commit.slice(0, 7) }}<span class="sr-only"> for {{ entry.name }}</span></a>
            </dd>
          </div>
        </dl>
        <a :href="`${brand.repository}/releases`" target="_blank" rel="noopener noreferrer">All releases</a>
      </section>
    </q-menu>
  </button>
</template>

<script setup>
import { computed, ref } from 'vue'
import { brand } from '../brand'
import { formatBuildInfo } from '../build-info'

const props = defineProps({
  clientSystem: { type: Object, default: () => ({}) },
  serverSystem: { type: Object, default: () => ({}) }
})
const open = ref(false)
const client = computed(() => formatBuildInfo(props.clientSystem))
const server = computed(() => formatBuildInfo(props.serverSystem))
const different = computed(() => (client.value.commit || client.value.label) !== (server.value.commit || server.value.label))
const entries = computed(() => [
  { name: 'Client', metadata: props.clientSystem, info: client.value },
  { name: 'Server', metadata: props.serverSystem, info: server.value }
].map(entry => ({ ...entry, commit: formatBuildInfo({ version: entry.metadata?.build_commit || entry.metadata?.version }).commit })))
</script>

<style scoped>
.version-pill { display: inline-flex; align-items: stretch; flex-shrink: 0; padding: 0; min-height: 32px; border: 1px solid var(--border-strong); border-radius: 999px; background: var(--surface-panel); color: var(--text-muted); font: inherit; font-size: .75rem; cursor: pointer; touch-action: manipulation; -webkit-tap-highlight-color: transparent; }
.version-pill__segment { display: flex; align-items: center; padding: 5px 11px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.version-pill__segment--client { border-right: 1px solid var(--border-strong); }
.version-pill:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.version-pill:active { transform: scale(.97); }
@media (hover: hover) { .version-pill:hover { background: var(--surface-hover); color: var(--text); } }
@media (pointer: coarse) { .version-pill { min-height: 44px; } }
.version-details__body { width: 300px; max-width: calc(100vw - 32px); padding: 16px; color: var(--text); background: var(--surface-panel); font-size: .8125rem; }
.version-details__body h2 { margin: 0; font-size: .875rem; font-weight: 600; }
.version-details__note { margin: 10px 0 0; color: var(--text-muted); line-height: 1.5; }
.version-details__body dl { margin: 14px 0; }
.version-details__row { display: grid; grid-template-columns: 56px minmax(0, 1fr); gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.version-details__row dt { color: var(--text-muted); }
.version-details__row dd { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin: 0; overflow-wrap: anywhere; }
.version-details__body a { color: var(--accent); text-underline-offset: .2em; }
.version-details__body a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
