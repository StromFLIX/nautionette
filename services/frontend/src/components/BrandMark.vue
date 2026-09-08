<template>
  <svg class="brand-mark" viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">
    <path class="brand-mark__field" d="M21 5h22l16 16v22L43 59H21L5 43V21Z" />
    <path class="brand-mark__edge" d="M21 5h22l16 16v22L43 59H21L5 43V21Z" />
    <path class="brand-mark__letter" d="M23 42V22l18 20V22" />
  </svg>
</template>

<style scoped>
.brand-mark { display: block; flex: none; width: 40px; height: 40px; color: var(--accent); }
.brand-mark__field { fill: currentColor; opacity: 0.06; }
.brand-mark__edge { stroke: currentColor; stroke-width: 1.5; opacity: 0.55; }
.brand-mark__letter { stroke: currentColor; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
</style>
