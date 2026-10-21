<!-- 内容面板：标题 + 副标题 + 右侧 extra 插槽；padding=false 时内容贴边（表格） -->
<template>
  <section class="jc-card panel" :class="{ flush: !padding }">
    <div v-if="title || $slots.extra" class="hd">
      <div>
        <h3>{{ title }}</h3>
        <span v-if="sub" class="jc-muted jc-small">{{ sub }}</span>
      </div>
      <div class="extra"><slot name="extra" /></div>
    </div>
    <div class="bd" v-loading="loading"><slot /></div>
  </section>
</template>

<script setup>
defineProps({ title: String, sub: String, padding: { type: Boolean, default: true }, loading: Boolean })
</script>

<style scoped>
.panel { display: flex; flex-direction: column; min-width: 0; }
.hd { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--jc-line-2); }
.hd h3 { margin: 0; font-size: 15px; font-weight: 600; display: inline; margin-right: 8px; }
.extra { display: flex; align-items: center; gap: 8px; }
.bd { padding: 16px 18px; flex: 1; min-height: 0; }
.flush .bd { padding: 0; }
.flush :deep(.el-table) { border-radius: 0 0 var(--jc-radius) var(--jc-radius); }
</style>
