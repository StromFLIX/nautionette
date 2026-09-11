<template>
  <div class="skin-preview" aria-hidden="true">
    <iframe :srcdoc="document" sandbox="" tabindex="-1" title="Skin style preview" loading="lazy" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { cssTokens } from '../../themes'
import { compileSkinCss } from '../../skins'

const props = defineProps({ skin: { type: Object, required: true } })
// Sandboxed, network-free miniature. Untrusted CSS never styles the library card.
const document = computed(() => {
  const tokens = Object.entries(cssTokens(props.skin.base, props.skin.tokens)).map(([key, value]) => `${key}:${value}`).join(';')
  return `<!doctype html><html data-skin="${props.skin.id}"><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"><style>
    :root {${tokens}} * {box-sizing:border-box} html,body{height:100%;margin:0;overflow:hidden} body{font:14px var(--font);color:var(--text);background:var(--surface-app)}
    .shell{display:grid;grid-template-columns:60px 1fr;height:100%}.rail{padding:8px;display:flex;flex-direction:column;gap:8px;background:var(--surface-rail)}
    .rail__item{padding:10px 4px;font-size:10px;text-align:center}.shell__main{min-width:0;background:var(--surface-panel)}.pane-head{padding:8px 12px;font-weight:bold}
    .thread__body{padding:12px}.msg{margin-bottom:10px}.bubble{padding:10px;font-size:12px;border-radius:var(--radius-md)}.msg--user{margin-left:18px}.msg--user .bubble{background:var(--bubble-out);color:var(--bubble-text)}
    .composer{padding:8px;margin:8px 12px;border:1px solid var(--border-strong)}.btn{padding:5px 10px;font:inherit;font-size:11px;background:var(--accent);color:var(--accent-text)}
  </style><style>${compileSkinCss(props.skin, true)}</style></head><body>
  <div class="shell" data-skin-part="shell"><nav class="rail" data-skin-part="navigation"><div class="rail__item rail__item--active">Chats</div><div class="rail__item">Flows</div></nav>
  <main class="shell__main" data-skin-part="main"><header class="pane-head" data-skin-part="header">Nautionette</header><div class="thread__body" data-skin-part="conversation">
  <div class="msg msg--user" data-skin-part="message" data-role="user"><div class="bubble">Make it feel like me.</div></div><div class="msg" data-skin-part="message" data-role="assistant"><div class="bubble">Your workspace. Your design.</div></div></div>
  <div class="composer" data-skin-part="composer"><button class="btn">Send message</button></div></main></div></body></html>`
})
</script>

<style scoped>
.skin-preview { position: relative; aspect-ratio: 1.7; overflow: hidden; border-radius: 4px; background: var(--surface-app); }
.skin-preview iframe { position: absolute; inset: 0; width: 200%; height: 200%; border: 0; transform: scale(0.5); transform-origin: top left; pointer-events: none; }
</style>
