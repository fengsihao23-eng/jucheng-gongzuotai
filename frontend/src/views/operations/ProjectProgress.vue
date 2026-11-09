<template>
  <section class="project-progress" aria-label="项目进度">
    <header class="progress-header">
      <div><h2>项目进度</h2><p v-if="latest">{{ datetime(latest.created_at) }} · {{ latest.author_name }}</p></div>
      <el-button v-if="project.can_manage" link type="primary" @click="start">填写项目进度</el-button>
    </header>
    <dl v-if="latest" :id="reportId" class="progress-rows">
      <div v-for="row in rows" :key="row.key" class="progress-row">
        <dt :class="{ issue: row.key === 'problems' && row.full }">{{ row.label }}</dt>
        <dd>
          <span v-if="row.key === 'next' && latest.next_due_date" class="plan-date">计划完成 <strong>{{ latest.next_due_date }}</strong></span>
          <p :class="{ missing: !row.full }">{{ (expanded ? row.full : row.preview) || '未填写' }}</p>
        </dd>
      </div>
    </dl>
    <p v-else class="empty">尚未填写项目进度</p>
    <el-button v-if="canExpand" link type="primary" class="expand" :aria-expanded="expanded" :aria-controls="reportId" @click="expanded = !expanded">{{ expanded ? '收起全文' : '展开全文' }}</el-button>
  </section>
  <el-dialog v-model="formOpen" title="填写项目进度" width="min(660px, calc(100vw - 24px))" align-center append-to-body destroy-on-close :close-on-click-modal="false">
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="进度情况" required><el-input v-model="form.progress" type="textarea" :rows="3" maxlength="6000" placeholder="写清楚目前做到哪一步、完成了什么" /></el-form-item>
      <el-form-item label="遇到的问题"><el-input v-model="form.problems" type="textarea" :rows="2" maxlength="6000" placeholder="有哪些问题需要协调？没有问题可留空" /></el-form-item>
      <el-form-item label="下一步计划"><el-input v-model="form.next_plan" type="textarea" :rows="2" maxlength="6000" placeholder="下一步要做什么" /></el-form-item>
      <el-form-item label="计划完成时间" :required="Boolean(form.next_plan.trim())"><el-date-picker v-model="form.next_due_date" type="date" value-format="YYYY-MM-DD" placeholder="选择计划完成日期" /></el-form-item>
    </el-form>
    <template #footer><div class="form-footer"><el-button link type="primary" @click="withXiaoju">用小聚填写</el-button><div class="actions"><el-button @click="formOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存项目进度</el-button></div></div></template>
  </el-dialog>
  <el-dialog v-model="historyOpen" title="项目进度记录" class="ops-progress-history" width="min(720px, calc(100vw - 24px))" align-center append-to-body destroy-on-close>
    <div v-loading="loading" class="history-list">
      <article v-for="entry in history" :key="entry.id">
        <h4>{{ entry.author_name }} <span>{{ datetime(entry.created_at) }}</span></h4>
        <dl class="entry"><div><dt>进度情况</dt><dd>{{ entry.progress }}</dd></div><div v-if="entry.problems"><dt>遇到的问题</dt><dd>{{ entry.problems }}</dd></div><div v-if="entry.next_plan"><dt>下一步计划</dt><dd>{{ entry.next_plan }}<small>计划完成时间 {{ entry.next_due_date }}</small></dd></div></dl>
      </article>
      <el-empty v-if="!history.length && !loading" :description="failed ? '暂时无法读取记录' : '暂无进度记录'" :image-size="64" />
    </div>
    <template #footer><el-button v-if="failed || nextBefore" :loading="loading" @click="loadHistory(nextBefore)">{{ failed ? '重试' : '查看更多' }}</el-button><el-button @click="historyOpen = false">关闭</el-button></template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, useId, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { datetime } from '@/utils/format'
import { announceChange } from './shared'

const props = defineProps({ project: { type: Object, required: true } })
const emit = defineEmits(['saved'])
const assistant = useAssistant(), latest = computed(() => props.project.latest_progress)
const formOpen = ref(false), historyOpen = ref(false), saving = ref(false), loading = ref(false), failed = ref(false)
const history = ref([]), nextBefore = ref(null)
const form = reactive({ progress: '', problems: '', next_plan: '', next_due_date: null })
const reportId = useId(), expanded = ref(false)
const rows = computed(() => [
  ['progress', '进度情况', latest.value?.progress], ['problems', '遇到的问题', latest.value?.problems], ['next', '下一步计划', latest.value?.next_plan],
].map(([key, label, text]) => {
  const full = text?.trim() || ''
  const line = full.replace(/\s+/g, ' '), chars = Array.from(line)
  return { key, label, full, preview: chars.length > 90 ? chars.slice(0, 90).join('').trimEnd() + '…' : line }
}))
const canExpand = computed(() => rows.value.some(row => row.full !== row.preview))
let historyVersion = 0

defineExpose({ openHistory: () => { historyOpen.value = true } })

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
.project-progress { padding: 20px 22px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); }
.progress-header, .form-footer, .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; } .progress-header, .form-footer { justify-content: space-between; } .actions .el-button + .el-button { margin: 0; }
.progress-header h2 { margin: 0; color: var(--jc-ink); font-size: 18px; } .progress-header p, .empty { margin: 4px 0 0; color: var(--jc-ink-3); font-size: 13px; }
.empty { margin-top: 17px; }.progress-rows { margin: 14px 0 0; }
.progress-row { display: grid; grid-template-columns: 86px minmax(0, 1fr); gap: 12px; padding: 12px 0; border-top: 1px solid var(--jc-line-2); }
dt { font-weight: 600; color: var(--jc-ink); font-size: 14px; line-height: 1.7; }.issue { color: var(--jc-warn); }
dd { margin: 0; min-width: 0; color: var(--jc-ink-2); font-size: 14px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
dd p { margin: 0; }.missing { color: var(--jc-ink-3); }.plan-date { display: inline-block; margin-bottom: 3px; color: var(--jc-brand); font-size: 13px; }
.expand { margin-top: 2px; }
:global(.ops-progress-history > .el-dialog__body) { max-height: 66dvh; overflow: auto; }.history-list { min-height: 120px; }.history-list article { padding: 12px; border: 1px solid var(--jc-line); border-radius: 7px; margin-bottom: 8px; }.history-list h4 { margin: 0 0 8px; font-size: 14px; color: var(--jc-ink); }.history-list h4 span { margin-left: 6px; font-weight: 400; font-size: 12px; color: var(--jc-ink-3); }.entry { margin: 0; }.entry > div + div { margin-top: 7px; }.entry small { display: block; color: var(--jc-brand); }
@media (max-width: 620px) { .project-progress { padding: 15px; }.progress-row { grid-template-columns: minmax(0, 1fr); gap: 3px; padding: 9px 0; } }
</style>
