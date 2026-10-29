<!-- 小聚标识：四点汇聚成星，取「聚」意。 -->
<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 2.8c.5 4.6 4.6 8.7 9.2 9.2-4.6.5-8.7 4.6-9.2 9.2-.5-4.6-4.6-8.7-9.2-9.2 4.6-.5 8.7-4.6 9.2-9.2z" fill="currentColor" />
  </svg>
</template>

<script setup>
defineProps({ size: { type: Number, default: 20 } })
</script>
