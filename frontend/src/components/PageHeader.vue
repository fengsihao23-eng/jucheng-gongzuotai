<!-- 页面标题区：eyebrow（所属模块）+ 衬线标题 + 描述 + 右侧操作 -->
<template>
  <header class="ph">
    <div class="l">
      <div v-if="eyebrow" class="jc-eyebrow">{{ eyebrow }}</div>
      <h1 class="jc-serif">{{ title }}</h1>
      <p v-if="desc" class="jc-muted">{{ desc }}</p>
      <slot name="meta" />
    </div>
    <div class="r"><slot /></div>
  </header>
</template>

<script setup>
defineProps({ title: String, eyebrow: String, desc: String })
</script>

<style scoped>
.ph { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
h1 { margin: 4px 0 0; font-size: 24px; color: var(--jc-ink); }
p { margin: 6px 0 0; font-size: 13px; max-width: 760px; }
.r { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
