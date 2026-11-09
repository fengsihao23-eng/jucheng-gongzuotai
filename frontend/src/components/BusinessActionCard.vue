<template>
  <article class="business-action" aria-label="业务操作确认单">
    <header><b>{{ action.title }}</b><span>{{ labels[action.state] || action.state }}</span></header>
    <p v-if="action.data_mode === 'mock'" class="action-note">演示环境数据</p>
    <p v-else-if="action.data_mode === 'local'" class="action-note">工作台资料 · 请核对分类、共享范围与实际处理状态</p>
    <p v-if="action.state === 'pending'" class="action-note">请核对目标记录和执行内容。确认后，小聚将按此单提交。</p>
    <BusinessData v-if="action.request" :value="requestView" />
    <div v-if="action.targets" class="action-targets"><b>{{ action.operation_id === 'submissions.remind' ? '本次实际接收范围' : '本次办理内容' }}</b><BusinessData :value="action.targets" /></div>
    <a v-if="action.operation_id === 'imports.decision' && action.request?.path?.batch_id" :href="`/imports?batch=${Number(action.request.path.batch_id)}`" target="_blank" rel="noopener noreferrer">查看完整导入清单</a>
    <ul v-if="action.attachments?.length" class="action-files"><li v-for="file in action.attachments" :key="file.id">{{ file.filename }} · {{ Math.ceil(file.size / 1024) }} KB</li></ul>
    <details v-if="action.before"><summary>查看变更前的记录</summary><BusinessData :value="action.before.data" /></details>
    <p v-if="action.error || error" class="action-error">{{ error || action.error }}</p>
    <div v-if="action.state === 'pending'" class="action-buttons"><el-button type="primary" size="small" :loading="deciding" :disabled="disabled" @click="decide(true)">确认执行</el-button><el-button size="small" :disabled="disabled || deciding" @click="decide(false)">取消这项操作</el-button><small>有效至 {{ expiry }}</small></div>
  </article>
</template>

<script setup>
import { computed, ref } from 'vue'
import api from '@/api/client'
import BusinessData from '@/components/BusinessData.vue'
const props = defineProps({ action: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['decision'])
const deciding = ref(false), error = ref('')
const labels = { pending: '等待你确认', executing: '提交中', executed: '已执行', cancelled: '已取消', expired: '已过期', stale: '记录已变化', failed: '未完成', denied: '权限已变化' }
const expiry = computed(() => new Date(props.action.expires_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
const requestView = computed(() => Object.fromEntries(Object.entries(props.action.request || {}).filter(([key, value]) => key !== 'files' && value !== null && (typeof value !== 'object' || Object.keys(value).length))))
async function decide(approve) {
  if (deciding.value || props.disabled) return
  deciding.value = true; error.value = ''
  try { const result = await api.post(`/assistant/business/actions/${props.action.id}/confirm`, { approve }, { silent: true }); emit('decision', result, approve) }
  catch (e) { error.value = e.message || '提交未完成，请核对记录'; emit('decision', null, false) }
  finally { deciding.value = false }
}
</script>

<style scoped>
.business-action { padding: 15px; border: 1px solid var(--jc-brand); border-radius: 9px; margin-top: 15px; font-size: 12px; }.business-action header { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }.business-action header b { font-weight: 600; line-height: 1.6; }.business-action header span { color: var(--jc-brand); font-size: 10px; flex-shrink: 0; }.action-note { line-height: 1.8; color: var(--jc-ink-3); }.action-buttons { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 15px; }.action-buttons .el-button { margin: 0; }.action-buttons small { color: var(--jc-ink-3); font-size: 10px; }.business-action details { margin-top: 10px; }.business-action summary { color: var(--jc-ink-3); cursor: pointer; padding: 7px 0; }.action-error { color: var(--jc-down); line-height: 1.8; }.action-files { padding-left: 18px; line-height: 1.8; }
</style>
