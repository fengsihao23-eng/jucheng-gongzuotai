<template>
  <article class="business-action" :class="{ pending: action.state === 'pending' }" aria-label="业务操作确认单">
    <button type="button" class="action-toggle" :aria-expanded="expanded" :aria-controls="detailsId" @click="expanded = !expanded">
      <b>{{ action.title }}</b><span class="action-state" :class="action.state">{{ labels[action.state] || action.state }}</span>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 4 4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </button>
    <div :id="detailsId" :hidden="!expanded" class="action-details">
      <template v-if="expanded">
        <p v-if="action.data_mode === 'mock'" class="action-note">演示环境数据</p>
        <p v-else-if="action.data_mode === 'local'" class="action-note">工作台数据</p>
        <p v-if="action.state === 'pending'" class="action-note">请核对内容，确认后小聚将按此单提交。</p>
        <BusinessData v-if="action.request" :value="requestView" />
        <div v-if="action.targets" class="action-targets"><b>{{ action.operation_id === 'submissions.remind' ? '本次实际接收范围' : '本次办理内容' }}</b><BusinessData :value="action.targets" /></div>
        <a v-if="action.operation_id === 'imports.decision' && action.request?.path?.batch_id" :href="`/imports?batch=${Number(action.request.path.batch_id)}`" target="_blank" rel="noopener noreferrer">查看完整导入清单</a>
        <ul v-if="action.attachments?.length" class="action-files"><li v-for="file in action.attachments" :key="file.id">{{ file.filename }} · {{ Math.ceil(file.size / 1024) }} KB</li></ul>
        <details v-if="action.before"><summary>查看变更前的记录</summary><BusinessData :value="action.before.data" /></details>
        <p v-if="action.error || error" class="action-error">{{ error || action.error }}</p>
        <div v-if="action.state === 'pending'" class="action-buttons"><el-button type="primary" size="small" :loading="deciding" :disabled="disabled" @click="decide(true)">确认执行</el-button><el-button size="small" :disabled="disabled || deciding" @click="decide(false)">取消这项操作</el-button><small>有效至 {{ expiry }}</small></div>
      </template>
    </div>
  </article>
</template>

<script setup>
import { computed, ref, useId, watch } from 'vue'
import api from '@/api/client'
import BusinessData from '@/components/BusinessData.vue'
const props = defineProps({ action: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['decision'])
const deciding = ref(false), error = ref('')
const expanded = ref(false), detailsId = useId()
const labels = { pending: '等待你确认', executing: '提交中', executed: '已执行', cancelled: '已取消', expired: '已过期', stale: '记录已变化', failed: '未完成', denied: '权限已变化' }
const expiry = computed(() => new Date(props.action.expires_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
const requestView = computed(() => Object.fromEntries(Object.entries(props.action.request || {}).filter(([key, value]) => key !== 'files' && value !== null && (typeof value !== 'object' || Object.keys(value).length))))
watch(() => props.action.id, () => { expanded.value = false; error.value = '' })
watch(() => props.action.state, state => { if (state === 'executed' || state === 'cancelled') expanded.value = false })
async function decide(approve) {
  if (deciding.value || props.disabled) return
  deciding.value = true; error.value = ''
  try { const result = await api.post(`/assistant/business/actions/${props.action.id}/confirm`, { approve }, { silent: true }); emit('decision', result, approve) }
  catch (e) { error.value = e.message || '提交未完成，请核对记录'; emit('decision', null, false) }
  finally { deciding.value = false }
}
</script>

<style scoped>
.business-action { border: 1px solid var(--jc-line); border-radius: 8px; margin-top: 9px; background: var(--jc-surface); font-size: 12px; }
.business-action.pending { border-color: var(--jc-brand-100); }.action-toggle { display: flex; width: 100%; align-items: center; gap: 8px; padding: 9px 12px; border: 0; border-radius: 7px; background: transparent; color: var(--jc-ink); text-align: left; font: inherit; cursor: pointer; }
.action-toggle:hover { background: var(--jc-surface-2); }.action-toggle:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 1px; }.action-toggle b { flex: 1; min-width: 0; font-weight: 600; line-height: 1.55; overflow-wrap: anywhere; }.action-toggle svg { flex: 0 0 16px; color: var(--jc-ink-3); }.action-toggle[aria-expanded='true'] svg { transform: rotate(90deg); }
.action-state { flex-shrink: 0; color: var(--jc-ink-3); }.action-state.pending { color: var(--jc-brand); font-weight: 600; }.action-state.executed { color: var(--jc-up); }.action-state.failed { color: var(--jc-down); }
.action-details { padding: 9px 12px 12px; border-top: 1px solid var(--jc-line-2); }.action-note { line-height: 1.8; color: var(--jc-ink-3); margin: 0 0 6px; }.action-buttons { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; }.action-buttons .el-button { margin: 0; }.action-buttons small { color: var(--jc-ink-3); font-size: 10px; }.business-action details { margin-top: 10px; }.business-action summary { color: var(--jc-ink-3); cursor: pointer; padding: 7px 0; }.action-error { color: var(--jc-down); line-height: 1.8; }.action-files { padding-left: 18px; line-height: 1.8; }
</style>
