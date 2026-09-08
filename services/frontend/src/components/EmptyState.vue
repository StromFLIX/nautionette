<template>
  <section class="empty workspace-empty">
    <div class="workspace-empty__glyph"><span class="material-icons" aria-hidden="true">{{ icon }}</span></div>
    <h1>{{ title }}</h1>
    <p>{{ description }}</p>
    <RouterLink v-if="to" :to="to" class="btn btn--outline">{{ action }}<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink>
  </section>
</template>

<script setup>
defineProps({ icon: String, title: String, description: String, to: String, action: String })
</script>

<style scoped>
.workspace-empty { min-height: 0; flex: 1; gap: 12px; }
.workspace-empty__glyph { display: grid; place-items: center; width: 72px; height: 72px; clip-path: var(--octagon); background: var(--accent-soft); color: var(--accent); margin-bottom: 12px; }
.workspace-empty__glyph .material-icons { font-size: 30px; }
.workspace-empty h1 { color: var(--text); font-size: 23px; font-weight: 500; letter-spacing: -0.03em; }
.workspace-empty p { margin: 0 0 14px; max-width: 320px; color: var(--text-muted); font-size: 13px; line-height: 1.7; }
.workspace-empty .btn { text-decoration: none; }
.workspace-empty .btn .material-icons { font-size: 16px; }
</style>
