<!-- 文件选择框（拖拽/点击）。只负责选文件，上传由父组件调用 api.upload 完成，便于附带业务字段。 -->
<template>
  <div class="ub" :class="{ over }" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="drop" @click="input.click()">
    <input ref="input" type="file" :accept="accept" hidden @change="pick" />
    <div v-if="!file" class="hint">
      <div class="t">{{ title }}</div>
      <div class="jc-muted jc-small">{{ tip }}</div>
    </div>
    <div v-else class="picked">
      <span class="jc-ellipsis">{{ file.name }}</span>
      <span class="jc-muted jc-small">{{ (file.size / 1024).toFixed(0) }} KB</span>
      <el-button link type="primary" @click.stop="clear">更换</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const props = defineProps({
  accept: { type: String, default: '.xlsx,.xls,.csv' },
  title: { type: String, default: '拖拽文件到此处，或点击选择' },
  tip: { type: String, default: '支持 xlsx / xls / csv' },
})
const emit = defineEmits(['change'])
const input = ref()
const file = ref(null)
const over = ref(false)
const set = (f) => { file.value = f; emit('change', f) }
const pick = (e) => e.target.files[0] && set(e.target.files[0])
const drop = (e) => { over.value = false; e.dataTransfer.files[0] && set(e.dataTransfer.files[0]) }
const clear = () => { file.value = null; input.value.value = ''; emit('change', null); input.value.click() }
defineExpose({ reset: () => { file.value = null; if (input.value) input.value.value = '' } })
</script>

<style scoped>
.ub { border: 1px dashed var(--jc-line); border-radius: var(--jc-radius); padding: 22px; text-align: center; cursor: pointer; background: var(--jc-surface-2); transition: border-color 0.15s; }
.ub:hover, .ub.over { border-color: var(--jc-brand); }
.t { font-size: 14px; color: var(--jc-ink-2); margin-bottom: 4px; }
.picked { display: flex; align-items: center; justify-content: center; gap: 12px; }
</style>
