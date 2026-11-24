<template>
  <el-button @click="router.push({ name: 'sporadic-collections' })">零星回款</el-button>
  <el-button type="primary" @click="openSummary">回款总结</el-button>
  <el-dialog v-model="visible" title="回款总结" width="min(720px, calc(100vw - 24px))" append-to-body destroy-on-close>
    <div v-loading="loading" class="report-body" :aria-busy="loading">
      <p v-if="loading" class="jc-muted">正在生成回款报告…</p>
      <el-alert v-else-if="error" :title="error" type="error" :closable="false" show-icon>
        <el-button link type="primary" @click="generate">重试</el-button>
      </el-alert>
      <template v-else-if="report">
        <p class="jc-small jc-muted report-source">{{ report.assisted ? 'AI 已整理项目反馈' : '按台账记录生成' }}</p>
        <pre class="report-text">{{ report.text }}</pre>
      </template>
    </div>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button type="primary" :disabled="!report" @click="copy">复制报告</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'

const router = useRouter()
const visible = ref(false)
const loading = ref(false)
const report = ref(null)
const error = ref('')

function openSummary() {
  visible.value = true
  generate()
}

async function generate() {
  if (loading.value) return
  loading.value = true
  report.value = null
  error.value = ''
  try {
    report.value = await api.post('/collections/summary', {}, { silent: true, timeout: 30000 })
  } catch (err) {
    error.value = err.message || '生成回款总结失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(report.value.text)
    ElMessage.success('回款报告已复制')
  } catch {
    ElMessage.warning('可选中报告正文手动复制')
  }
}
</script>

<style scoped>
.report-body { min-height: 64px; }
.report-source { margin: 0 0 10px; }
.report-text {
  font: inherit; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere;
  max-height: 62dvh; overflow: auto; overscroll-behavior: contain;
  margin: 0; padding: 16px; border: 1px solid var(--jc-line); border-radius: 8px; background: var(--jc-bg-2);
}
</style>
