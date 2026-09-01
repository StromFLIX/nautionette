<template>
  <div class="trigger">
    <div class="trigger__tabs">
      <button
        v-for="option in languages" :key="option" class="tab"
        :class="{ 'tab--active': language === option }" @click="language = option"
      >{{ option }}</button>
      <span class="grow" />
      <button class="btn btn--sm btn--outline" @click="copy">
        {{ copied ? 'Copied' : 'Copy' }}
      </button>
    </div>
    <pre class="trigger__code mono" v-html="rendered" />
    <label class="trigger__token caption">
      <input v-model="withToken" type="checkbox" />
      include the access token
    </label>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { highlight } from '../highlight'
import { auth, server } from '../api'

const props = defineProps({
  workflow: { type: String, required: true },
  inputs: { type: Object, default: () => ({}) }
})

const languages = ['curl', 'python', 'node', 'GET']
const language = ref('curl')
const withToken = ref(Boolean(auth.token))
const copied = ref(false)

const url = computed(() => `${server.url || window.location.origin}/api/triggers/${props.workflow}`)
const body = computed(() => JSON.stringify(props.inputs || {}, null, 2))
const token = computed(() => (withToken.value && auth.token ? auth.token : 'YOUR_TOKEN'))

const snippet = computed(() => ({
  curl: `curl -X POST ${url.value} \\
  -H 'Authorization: Bearer ${token.value}' \\
  -H 'Content-Type: application/json' \\
  -d '${JSON.stringify(props.inputs || {})}'`,

  python: `import httpx

httpx.post(
    "${url.value}",
    headers={"Authorization": "Bearer ${token.value}"},
    json=${body.value.replace(/\n/g, '\n    ')},
).raise_for_status()`,

  node: `await fetch("${url.value}", {
  method: "POST",
  headers: {
    "Authorization": "Bearer ${token.value}",
    "Content-Type": "application/json"
  },
  body: JSON.stringify(${body.value.replace(/\n/g, '\n  ')})
})`,

  // For a webhook that can only issue a GET, the token rides in the query.
  GET: `${url.value}?token=${token.value}${Object.entries(props.inputs || {})
    .map(([key, value]) => `&${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('')}`
}[language.value]))

const grammar = computed(() =>
  ({ curl: 'bash', python: 'python', node: 'javascript', GET: 'bash' }[language.value]))

const rendered = computed(() => highlight(snippet.value, grammar.value))

async function copy () {
  await navigator.clipboard.writeText(snippet.value)
  copied.value = true
  setTimeout(() => { copied.value = false }, 1200)
}
</script>

<style scoped>
.trigger {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: #0b0d12;
  overflow: hidden;
}

.trigger__tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 6px 0 6px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-panel);
}

.trigger__tabs .tab {
  padding: 8px 10px;
  font-size: 12.5px;
}

.trigger__code {
  margin: 0;
  padding: 12px 14px;
  overflow-x: auto;
  line-height: 1.6;
  white-space: pre;
}

.trigger__token {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-top: 1px solid var(--border);
  background: var(--surface-panel);
  color: var(--text-dim);
  cursor: pointer;
}

.trigger__token input {
  accent-color: var(--accent);
}
</style>
