<!-- 状态胶囊：tone = up | down | warn | info | brand | (默认中性) -->
<template>
  <span class="jc-pill" :class="tone"><i v-if="dot" class="jc-dot" :class="dotTone" />{{ text }}</span>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ text: [String, Number], tone: String, dot: Boolean })
const dotTone = computed(() => ({ up: 'up', down: 'down', warn: 'warn', brand: 'brand' })[props.tone] || '')
</script>
