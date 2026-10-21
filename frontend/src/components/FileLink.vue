<!-- 文件链接：file = {filename, url, size, version}；点击打开签名下载链接 -->
<template>
  <a v-if="file" class="fl" :href="file.url" target="_blank" :title="file.filename">
    <span class="ext">{{ ext }}</span>
    <span class="jc-ellipsis">{{ label || file.filename }}</span>
    <span v-if="file.version > 1" class="jc-muted jc-small">v{{ file.version }}</span>
  </a>
  <span v-else class="jc-muted">—</span>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ file: Object, label: String })
const ext = computed(() => (props.file?.filename?.split('.').pop() || '').toUpperCase().slice(0, 4))
</script>

<style scoped>
.fl { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; color: var(--jc-ink); }
.fl:hover { color: var(--jc-brand); }
.ext { font-size: 10px; font-weight: 600; padding: 1px 4px; border-radius: 3px; background: var(--jc-brand-50); color: var(--jc-brand); }
</style>
