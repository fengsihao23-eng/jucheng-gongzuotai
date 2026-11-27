<template>
  <div class="feedback-attachment-picker">
    <input ref="input" type="file" multiple hidden :disabled="disabled" aria-label="选择反馈附件" @change="pick" />
    <el-button plain :icon="Paperclip" :disabled="disabled" @click="input?.click()">上传附件</el-button>
    <span v-if="!files.length" class="jc-small jc-muted file-hint">不限格式 · 单个 ≤ 50 MB</span>
    <ul v-else class="selected-files" aria-label="待上传附件">
      <li v-for="(file, index) in files" :key="index" class="selected-file">
        <span :title="file.name">{{ file.name }}</span>
        <el-button link :icon="Close" :aria-label="`移除 ${file.name}`" :disabled="disabled" @click="remove(index)" />
      </li>
    </ul>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, Paperclip } from '@element-plus/icons-vue'
import { feedbackFileLimit, feedbackFileSize } from '@/api/feedback'

defineProps({ disabled: Boolean })
const files = defineModel({ type: Array, required: true })
const input = ref(null)

function pick(event) {
  const chosen = Array.from(event.target.files)
  event.target.value = ''
  const oversized = chosen.find(file => file.size > feedbackFileSize)
  if (oversized) return ElMessage.warning(`「${oversized.name}」超过 50 MB`)
  if (chosen.length + files.value.length > feedbackFileLimit) return ElMessage.warning(`每次提交最多上传 ${feedbackFileLimit} 个附件`)
  files.value = [...files.value, ...chosen]
}

function remove(index) {
  files.value = files.value.filter((_, position) => position !== index)
}
</script>

<style scoped>
.feedback-attachment-picker { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; max-width: 100%; min-width: 0; }
.file-hint { white-space: nowrap; }
.selected-files { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; list-style: none; margin: 0; padding: 0; max-height: 84px; max-width: min(290px, 100%); overflow: auto; }
.selected-file { display: inline-flex; gap: 4px; align-items: center; min-width: 0; max-width: 100%; padding: 2px 5px 2px 7px; border: 1px solid var(--jc-line); border-radius: 5px; font-size: 12px; }
.selected-file span { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.selected-file .el-button { flex: none; padding: 3px; }
</style>
