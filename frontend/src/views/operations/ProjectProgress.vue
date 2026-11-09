<template>
  <section class="project-progress" aria-label="项目进度">
    <header>
      <div><h3>项目进度</h3><p v-if="latest">{{ latest.author_name }} · {{ datetime(latest.created_at) }}</p><p v-else>尚未填写项目进度</p></div>
      <div v-if="!readOnly" class="actions">
        <el-button v-if="latest || $slots['history-actions']" size="small" @click="historyOpen = true">进度记录</el-button>
        <el-button v-if="project.can_manage" type="primary" size="small" @click="start">填写项目进度</el-button>
      </div>
    </header>
    <dl class="progress-grid" :class="{ empty: !latest }">
      <div><dt><i />进度情况</dt><dd>{{ latest?.progress || '待填写' }}</dd></div>
      <div><dt><i class="issues" />遇到的问题</dt><dd>{{ latest ? latest.problems || '未填写问题' : '待填写' }}</dd></div>
      <div><dt><i class="next" />下一步计划</dt><dd>{{ latest?.next_plan || '未填写计划' }}</dd><dd v-if="latest?.next_due_date" class="plan-date">计划完成时间 <strong>{{ latest.next_due_date }}</strong></dd></div>
    </dl>
  </section>
  <el-dialog v-if="!readOnly" v-model="formOpen" title="填写项目进度" width="min(660px, calc(100vw - 24px))" align-center append-to-body destroy-on-close :close-on-click-modal="false">
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="进度情况" required><el-input v-model="form.progress" type="textarea" :rows="3" maxlength="6000" placeholder="写清楚目前做到哪一步、完成了什么" /></el-form-item>
      <el-form-item label="遇到的问题"><el-input v-model="form.problems" type="textarea" :rows="2" maxlength="6000" placeholder="有哪些问题需要协调？没有问题可留空" /></el-form-item>
      <el-form-item label="下一步计划"><el-input v-model="form.next_plan" type="textarea" :rows="2" maxlength="6000" placeholder="下一步要做什么" /></el-form-item>
      <el-form-item label="计划完成时间" :required="Boolean(form.next_plan.trim())"><el-date-picker v-model="form.next_due_date" type="date" value-format="YYYY-MM-DD" placeholder="选择计划完成日期" /></el-form-item>
    </el-form>
    <template #footer><div class="form-footer"><el-button link type="primary" @click="withXiaoju">用小聚填写</el-button><div class="actions"><el-button @click="formOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存项目进度</el-button></div></div></template>
  </el-dialog>
  <el-dialog v-if="!readOnly" v-model="historyOpen" title="项目进度记录" class="ops-progress-history" width="min(1140px, calc(100vw - 24px))" align-center append-to-body destroy-on-close @closed="historyClosed">
    <div v-loading="loading" class="history-list">
      <div v-if="history.length" class="history-caption"><strong>{{ project.name }}</strong><span>最近填写的记录在前</span></div>
      <ProgressHistoryTable v-if="history.length" :entries="history" />
      <el-empty v-if="!history.length && !loading" :description="failed ? '暂时无法读取记录' : '暂无进度记录'" :image-size="64" />
    </div>
    <template #footer>
      <slot name="history-actions" :close="closeHistory" />
      <el-button v-if="failed || nextBefore" :loading="loading" @click="loadHistory(nextBefore)">{{ failed ? '重试' : '查看更多' }}</el-button>
      <el-button @click="closeHistory()">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { datetime } from '@/utils/format'
import { announceChange } from './shared'
import ProgressHistoryTable from './ProgressHistoryTable.vue'

const props = defineProps({ project: { type: Object, required: true }, readOnly: Boolean })
const emit = defineEmits(['saved'])
const assistant = useAssistant(), latest = computed(() => props.project.latest_progress)
const formOpen = ref(false), historyOpen = ref(false), saving = ref(false), loading = ref(false), failed = ref(false)
const history = ref([]), nextBefore = ref(null)
const form = reactive({ progress: '', problems: '', next_plan: '', next_due_date: null })
let historyVersion = 0, afterHistoryClose

function closeHistory(after) { afterHistoryClose = after; historyOpen.value = false }
function historyClosed() { const after = afterHistoryClose; afterHistoryClose = undefined; after?.() }
function start() {
  Object.assign(form, { progress: '', problems: '', next_plan: '', next_due_date: null })
  formOpen.value = true
}
function withXiaoju() {
  const detail = form.progress.trim() || form.problems.trim() || form.next_plan.trim()
  const values = detail ? `\n进度情况：${form.progress}\n遇到的问题：${form.problems}\n下一步计划：${form.next_plan}\n计划完成日期：${form.next_due_date || ''}` : '进度情况：\n遇到的问题：\n下一步计划和计划完成日期：'
  assistant.openDraft(`帮我填写运营项目「${props.project.name}」的项目进度。${values}`)
}
async function save() {
  if (saving.value) return
  if (!form.progress.trim()) return ElMessage.warning('请填写进度情况')
  if (Boolean(form.next_plan.trim()) !== Boolean(form.next_due_date)) return ElMessage.warning('下一步计划和计划完成日期须一起填写')
  saving.value = true
  try {
    const result = await api.post(`/operations/${props.project.id}/progress`, { ...form, next_due_date: form.next_due_date || null })
    formOpen.value = false; emit('saved', result); announceChange(); ElMessage.success('项目进度已保存')
  } catch {} finally { saving.value = false }
}
async function loadHistory(before = null) {
  const current = ++historyVersion
  loading.value = true; failed.value = false
  try {
    const result = await api.get(`/operations/${props.project.id}/progress`, { before_id: before }, { silent: true })
    if (current !== historyVersion) return
    history.value = before ? [...history.value, ...result.items] : result.items
    nextBefore.value = result.next_before_id
  } catch { if (current === historyVersion) failed.value = true }
  finally { if (current === historyVersion) loading.value = false }
}
watch(() => [historyOpen.value, latest.value?.id], ([open]) => {
  if (open) { history.value = []; nextBefore.value = null; loadHistory() } else historyVersion++
})
</script>

<style scoped>
.project-progress { padding: 13px 16px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); margin-bottom: 14px; }
header, .form-footer, .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; } header, .form-footer { justify-content: space-between; } .actions .el-button + .el-button { margin: 0; }
header h3 { margin: 0; color: var(--jc-ink); font-size: 16px; } header p { margin: 3px 0 0; color: var(--jc-ink-3); font-size: 12px; }
.progress-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 10px 0 0; }.progress-grid > div { min-width: 0; padding: 9px 11px; border-radius: 7px; border: 1px solid var(--jc-line-2); background: var(--jc-bg-2, #f9f8f5); }
dt { font-weight: 600; color: var(--jc-ink); font-size: 13px; } dt i { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; background: var(--jc-up); vertical-align: 2px; } dt i.issues { background: var(--jc-warn); } dt i.next { background: var(--jc-brand); }
dd { margin: 5px 0 0; color: var(--jc-ink-2); font-size: 13px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }.progress-grid dd:first-of-type { max-height: 120px; overflow: auto; overscroll-behavior: contain; }.empty dd { color: var(--jc-ink-3); }.plan-date { font-size: 12px; color: var(--jc-brand); }
.history-list { min-height: 120px; }.history-caption { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; margin: 0 0 10px; }.history-caption strong { color: var(--jc-ink); }.history-caption span { color: var(--jc-ink-3); font-size: 12px; }
@media (max-width: 620px) { .project-progress { padding: 10px; }.progress-grid:not(.empty) { grid-template-columns: minmax(0, 1fr); }.progress-grid { gap: 5px; }.progress-grid > div { padding: 6px; }.progress-grid.empty dd { display: none; }.progress-grid.empty dt { font-size: 11px; }.progress-grid.empty i { display: none; } }
</style>
