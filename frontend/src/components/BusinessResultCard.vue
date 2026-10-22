<template>
  <article class="business-result" aria-label="业务执行回执">
    <header><b>{{ result.title }}</b><span :class="result.ok ? 'success' : 'failure'">{{ result.reused ? '复用已有回执' : result.ok ? '已执行' : '未完成' }}</span></header>
    <p v-if="result.data_mode === 'mock'" class="demo-note">演示环境数据</p>
    <p v-else-if="result.data_source === 'wecom_cli'" class="demo-note">企业微信真实数据 · 个人授权</p>
    <p class="result-message">{{ result.message }}</p>
    <div v-if="result.files?.length" class="result-files"><FileLink v-for="file in result.files" :key="file.id || file.url" :file="file" /></div>
    <div v-if="result.links?.length" class="result-files"><a v-for="link in result.links" :key="link.url" :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.label }} ↗</a></div>
    <div class="result-actions"><el-button v-if="result.result_id" text size="small" :loading="loading" @click="toggleDetails">{{ expanded ? '收起明细' : '查看完整结果' }}</el-button><el-button v-if="result.page" text size="small" @click="$emit('navigate', result.page)">查看业务页面</el-button></div>
    <div v-if="expanded" class="result-details"><p v-if="error" class="failure">{{ error }}</p><BusinessData v-else :value="full?.data ?? result.data" /><small class="receipt-id">回执 {{ result.result_id }}</small></div>
  </article>
</template>

<script setup>
import { ref } from 'vue'
import api from '@/api/client'
import FileLink from '@/components/FileLink.vue'
import BusinessData from '@/components/BusinessData.vue'
const props = defineProps({ result: { type: Object, required: true } })
defineEmits(['navigate'])
const expanded = ref(false), loading = ref(false), full = ref(null), error = ref('')
async function toggleDetails() {
  expanded.value = !expanded.value
  if (!expanded.value || full.value || !props.result.result_id) return
  loading.value = true
  try { full.value = await api.get(`/assistant/business/results/${props.result.result_id}`, { full: true }, { silent: true }) }
  catch (e) { error.value = e.message || '暂时无法读取回执' }
  finally { loading.value = false }
}
</script>

<style scoped>
.business-result { padding: 15px; border: 1px solid var(--jc-line); border-radius: 9px; background: var(--jc-surface); margin-top: 14px; font-size: 12px; }.business-result header { display: flex; align-items: baseline; gap: 8px; justify-content: space-between; }.business-result header b { font-weight: 600; line-height: 1.6; }.business-result header span { flex-shrink: 0; font-size: 10px; }.success { color: var(--jc-up); }.failure { color: var(--jc-down); }.demo-note { display: inline-block; padding: 2px 7px; background: var(--jc-surface-2); border-radius: 4px; color: var(--jc-ink-3); font-size: 10px; margin: 9px 0 0; }.result-message { line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; margin: 9px 0; }.result-files { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; margin: 12px 0; }.result-actions { display: flex; flex-wrap: wrap; gap: 10px; }.result-actions .el-button { margin: 0; padding-left: 0; padding-right: 0; }.result-details { border-top: 1px solid var(--jc-line-2); padding-top: 12px; margin-top: 8px; }.receipt-id { display: block; margin-top: 12px; color: var(--jc-ink-3); overflow-wrap: anywhere; font: 10px/1.5 monospace; }
</style>
