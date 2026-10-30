<template>
  <q-dialog v-model="open" no-refocus no-route-dismiss aria-label="Search chats and settings" @before-show="rememberFocus" @show="onShow" @hide="restoreFocus">
    <q-card class="command-palette" @keydown="onKeydown">
      <div class="command-palette__search">
        <span class="material-icons" aria-hidden="true">search</span>
        <input
          ref="input" v-model="query" type="text" role="combobox" autofocus
          aria-label="Search chats and settings" placeholder="Search chats and settings…"
          aria-autocomplete="list" aria-expanded="true" aria-controls="command-results"
          :aria-activedescendant="selected ? optionId(selected) : undefined"
          autocomplete="off" spellcheck="false"
        />
        <button type="button" class="btn btn--sm" aria-label="Close search" @click="open = false"><kbd>esc</kbd></button>
      </div>
      <div class="command-palette__scopes" role="group" aria-label="Search in">
        <button v-for="item in scopes" :key="item.key" type="button" class="command-palette__scope"
          :class="{ 'command-palette__scope--active': scope === item.key }" :aria-pressed="scope === item.key"
          @click="scope = item.key; focusSearch()">{{ item.label }}</button>
      </div>
      <div id="command-results" class="command-palette__results scroll-y" role="listbox" aria-label="Search results">
        <div v-for="group in groups" :key="group.key" role="group" :aria-labelledby="`command-group-${group.key}`">
          <div :id="`command-group-${group.key}`" class="command-palette__heading">
            {{ group.label }}<span>{{ group.results.length < group.count ? `${group.results.length} of ${group.count}` : group.count }}</span>
          </div>
          <div
            v-for="result in group.results" :id="optionId(result)" :key="result.key"
            role="option" :aria-selected="selected?.key === result.key" :aria-label="result.label"
            class="command-palette__result" :class="{ 'command-palette__result--active': selected?.key === result.key }"
            @pointermove="activeKey = result.key" @mousedown.prevent @click="activate(result)"
          >
            <span class="material-icons command-palette__icon" aria-hidden="true">{{ result.icon }}</span>
            <div class="command-palette__copy">
              <div class="command-palette__title truncate">{{ result.label }}</div>
              <div class="command-palette__description truncate">{{ result.description }}</div>
            </div>
            <span v-if="selected?.key === result.key" class="material-icons command-palette__enter" aria-hidden="true">keyboard_return</span>
          </div>
        </div>
        <p v-if="searching" class="command-palette__notice" role="status">Searching full conversations…</p>
        <p v-if="searchError" class="command-palette__notice" role="alert">{{ searchError }}</p>
        <p v-if="!results.length && !searching" class="command-palette__empty">No results for “{{ query }}”. Try another search.</p>
      </div>
      <div class="sr-only" role="status">{{ results.length }} results shown</div>
      <footer class="command-palette__footer">
        <span><kbd>↑</kbd> <kbd>↓</kbd> to navigate <kbd>↵</kbd> to open</span>
        <span>{{ searchHint }}</span>
      </footer>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store } from '../store'
import { commandSearchGroups } from '../command-search'
import { api } from '../api'

const open = defineModel({ type: Boolean, default: false })
const props = defineProps({ initialQuery: { type: String, default: '' } })
const route = useRoute()
const router = useRouter()
const input = ref(null)
const query = ref('')
const scope = ref('all')
const activeKey = ref('')
const navigating = ref(false)
const scopes = [{ key: 'all', label: 'All' }, { key: 'chats', label: 'Chats' }, { key: 'settings', label: 'Settings' }]
const remoteResults = ref(null)
const searching = ref(false)
const searchError = ref('')
const semanticState = ref('')
const searchHint = computed(() => {
  if (!query.value.trim() || scope.value === 'settings') return 'Chats & settings'
  if (['ready', 'indexing'].includes(semanticState.value)) return semanticState.value === 'indexing' ? 'Hybrid search · indexing history' : 'Keyword + semantic search'
  if (semanticState.value === 'loading') return 'Keyword search · loading semantic model'
  return remoteResults.value === null ? 'Titles & latest previews' : 'Keyword search'
})
const groups = computed(() => commandSearchGroups(store.chats, query.value, scope.value, remoteResults.value))
const results = computed(() => groups.value.flatMap(group => group.results))
const selected = computed(() => results.value.find(result => result.key === activeKey.value) || results.value[0])
const optionId = result => `command-option-${result.key}`

let searchTimer = null
let searchController = null
let searchGeneration = 0
function cancelSearch () {
  clearTimeout(searchTimer)
  searchController?.abort()
  searchGeneration++
}
watch([open, query, scope], () => {
  cancelSearch()
  remoteResults.value = null
  semanticState.value = ''
  searchError.value = ''
  searching.value = false
  const text = query.value.trim()
  if (!open.value || !text || scope.value === 'settings') return
  searching.value = true
  const version = searchGeneration
  searchTimer = setTimeout(async () => {
    searchController = new AbortController()
    try {
      const response = await api.searchChats(text, AbortSignal.any([searchController.signal, AbortSignal.timeout(5000)]))
      if (version !== searchGeneration) return
      if (!Array.isArray(response.results) || !response.semantic?.state) throw new Error('Invalid search response')
      remoteResults.value = response.results
      semanticState.value = response.semantic.state
    } catch {
      if (version !== searchGeneration) return
      searchError.value = 'Full-history search unavailable. Showing title and latest-message matches.'
    } finally {
      if (version === searchGeneration) searching.value = false
    }
  }, 180)
})
onUnmounted(cancelSearch)

let previousFocus = null
function rememberFocus () { previousFocus = document.activeElement }
function restoreFocus () {
  if (!navigating.value && previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
  previousFocus = null
}
function focusSearch () { input.value?.focus() }
function revealSelected () {
  if (open.value && selected.value) document.getElementById(optionId(selected.value))?.scrollIntoView({ block: 'nearest' })
}
function onShow () {
  focusSearch()
  // Early keyboard navigation can happen while the dialog is still scaling in.
  revealSelected()
}
watch(open, value => {
  if (!value) return
  query.value = props.initialQuery
  scope.value = 'all'
  activeKey.value = ''
  navigating.value = false
})
watch([query, scope], () => { activeKey.value = ''; nextTick(() => { document.getElementById('command-results')?.scrollTo(0, 0) }) })
watch(() => route.fullPath, () => {
  if (!open.value) return
  navigating.value = true
  open.value = false
})

function onKeydown (event) {
  if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return
  // Keep native button/Tab behavior; the combobox owns result navigation.
  if (event.target !== input.value) return
  if (event.key === 'Enter') {
    event.preventDefault()
    if (selected.value) activate(selected.value)
  } else if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault()
    if (!results.value.length) return
    const index = results.value.findIndex(result => result.key === selected.value?.key)
    const next = (index + (event.key === 'ArrowDown' ? 1 : -1) + results.value.length) % results.value.length
    activeKey.value = results.value[next].key
    nextTick(revealSelected)
  }
}

async function activate (result) {
  // Don't restore focus to the old page while a setting deep-link takes focus.
  navigating.value = router.resolve(result.to).fullPath !== route.fullPath
  open.value = false
  await router.push(result.to)
}
</script>

<style scoped>
.command-palette {
  display: flex;
  flex-direction: column;
  width: 720px;
  max-width: calc(100vw - 64px);
  height: min(620px, calc(100dvh - 96px));
  overflow: hidden;
}
.command-palette__search { display: flex; align-items: center; gap: 16px; padding: 20px; border-bottom: 1px solid var(--border); }
.command-palette__search > .material-icons { color: var(--accent); font-size: 1.5rem; }
.command-palette__search input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--text); font: inherit; font-size: 1.0625rem; }
.command-palette__search input::placeholder { color: var(--text-dim); }
.command-palette__scopes { display: flex; flex: none; gap: 6px; padding: 12px 20px 4px; }
.command-palette__scope { padding: 5px 12px; border: 0; border-radius: var(--radius-pill); color: var(--text-muted); background: transparent; font: inherit; font-size: 0.75rem; cursor: pointer; }
.command-palette__scope:hover { background: var(--surface-hover); }
.command-palette__scope--active { color: var(--accent); background: var(--accent-soft); }
.command-palette__results { flex: 1; min-height: 0; padding: 0 10px 12px; }
.command-palette__heading { display: flex; gap: 8px; padding: 16px 12px 8px; color: var(--text-dim); font-size: 0.6875rem; font-weight: 600; }
.command-palette__heading span { font-weight: 400; }
.command-palette__result { display: flex; align-items: center; gap: 14px; padding: 12px; border-radius: var(--radius-sm); cursor: pointer; }
.command-palette__result--active { background: var(--accent-soft); }
.command-palette__icon { flex: none; color: var(--text-muted); font-size: 1.125rem; }
.command-palette__result--active .command-palette__icon { color: var(--accent); }
.command-palette__copy { flex: 1; min-width: 0; }
.command-palette__title { font-size: 0.875rem; font-weight: 550; }
.command-palette__description { margin-top: 3px; font-size: 0.75rem; color: var(--text-dim); }
.command-palette__enter { color: var(--accent); font-size: 1rem; }
.command-palette__notice { padding: 8px 12px; color: var(--text-dim); font-size: 0.75rem; }
.command-palette__empty { padding: 48px 20px; text-align: center; color: var(--text-muted); overflow-wrap: anywhere; }
.command-palette__footer { display: flex; flex: none; justify-content: space-between; gap: 12px; padding: 12px 20px; border-top: 1px solid var(--border); color: var(--text-dim); font-size: 0.6875rem; }
.command-palette__footer kbd { font-size: inherit; }
</style>
