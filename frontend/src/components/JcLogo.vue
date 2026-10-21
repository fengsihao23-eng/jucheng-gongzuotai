<template>
  <span class="jc-logo" :class="{ inverse }">
    <svg :width="size" :height="size" viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="12" :fill="inverse ? '#fff' : '#1B3275'" />
      <path :fill="inverse ? '#1B3275' : '#fff'" d="M22 14h7v26c0 6-4 10-10 10h-5v-7h4c2.5 0 4-1.3 4-4z" />
      <path :fill="inverse ? '#1B3275' : '#fff'" d="M45 14h5v12h-5c-4.5 0-7 2.4-7 7v4c0 4.6 2.5 7 7 7h5v6h-6c-9 0-13-5-13-13v-6c0-8 4-17 14-17z" />
    </svg>
    <span v-if="withText" class="txt">
      <b>聚成数智</b>
      <small>Ju Cheng Technology</small>
    </span>
  </span>
</template>

<script setup>
defineProps({ size: { type: Number, default: 32 }, withText: { type: Boolean, default: true }, inverse: Boolean })
</script>

<style scoped>
.jc-logo { display: inline-flex; align-items: center; gap: 10px; }
.txt { display: flex; flex-direction: column; line-height: 1.15; }
.txt b { font-family: var(--jc-font-serif); font-size: 16px; letter-spacing: 0.12em; color: var(--jc-ink); }
.txt small { font-size: 10px; letter-spacing: 0.06em; color: var(--jc-ink-3); }
.inverse .txt b { color: #fff; }
.inverse .txt small { color: rgba(255, 255, 255, 0.6); }
</style>
