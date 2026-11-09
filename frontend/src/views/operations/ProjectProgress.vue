<template>
  <section class="project-progress" aria-label="项目进度">
    <header>
      <div><h3>项目进度</h3><p v-if="latest">最近填写：{{ latest.author_name }} · {{ datetime(latest.created_at) }}</p></div>
      <div v-if="!readOnly" class="actions">
        <slot name="actions" />
        <el-button v-if="latest || $slots['history-actions']" size="small" @click="historyOpen = true">进度记录</el-button>
        <el-button v-if="project.can_manage" type="primary" size="small" @click="start">填写项目进度</el-button>
      </div>
    </header>
    <details v-if="latest && !latest.task_id" :open="readOnly || undefined" class="note">
      <summary><strong>最近一次项目说明</strong><span>{{ latest.progress }}</span></summary>
      <dl class="progress-grid">
        <div><dt>进度情况</dt><dd>{{ latest.progress }}</dd></div>
        <div v-if="latest.problems"><dt>遇到的问题</dt><dd>{{ latest.problems }}</dd></div>
        <div v-if="latest.next_plan"><dt>下一步计划</dt><dd>{{ latest.next_plan }}</dd><dd class="plan-date">计划完成时间 <strong>{{ latest.next_due_date }}</strong></dd></div>
      </dl>
    </details>
  </section>
  <el-dialog v-if="!readOnly" v-model="formOpen" title="填写项目进度" class="ops-progress-entry-dialog" width="min(700px, calc(100vw - 24px))" align-center append-to-body destroy-on-close :close-on-click-modal="false">
    <p class="form-hint">{{ active ? '填了下一步和日期，就会生成一条负责人计划。已有事项请打开原记录反馈。' : '项目恢复进行中后可添加新的计划事项。' }}</p>
    <el-form label-position="top" :disabled="saving" @submit.prevent="save">
      <fieldset v-for="(entry, index) in entries" :key="entry.key" class="entry" :class="{ multiple: entries.length > 1 }">
        <legend v-if="entries.length > 1">第 {{ index + 1 }} 条</legend>
        <el-form-item label="进度情况" required><el-input v-model="entry.progress" type="textarea" :rows="2" maxlength="6000" placeholder="目前做到哪一步、完成了什么？" /></el-form-item>
        <el-form-item label="遇到的问题"><el-input v-model="entry.problems" type="textarea" :rows="2" maxlength="6000" placeholder="没有问题可留空" /></el-form-item>
        <template v-if="active">
          <el-form-item label="下一步计划"><el-input v-model="entry.next_plan" type="textarea" :rows="2" maxlength="6000" placeholder="下一步要做什么？" /></el-form-item>
          <el-form-item label="计划完成时间" :required="Boolean(entry.next_plan.trim())"><el-date-picker v-model="entry.next_due_date" type="date" value-format="YYYY-MM-DD" placeholder="选择计划完成日期" /></el-form-item>
        </template>
        <el-button v-if="entries.length > 1" link @click="entries.splice(index, 1)" :aria-label="`移除第 ${index + 1} 条`">移除本条</el-button>
      </fieldset>
      <el-button :disabled="entries.length >= 20 || saving" plain @click="addEntry">＋ 再填一条</el-button>
    </el-form>
    <template #footer><div class="form-footer"><el-button link type="primary" @click="withXiaoju">用小聚填写</el-button><div class="actions"><el-button @click="formOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存项目进度</el-button></div></div></template>
  </el-dialog>
  <el-dialog v-if="!readOnly" v-model="historyOpen" title="项目进度记录" class="ops-progress-history" width="min(1140px, calc(100vw - 24px))" align-center append-to-body destroy-on-close @closed="historyClosed">
    <div v-loading="loading" class="history-list">
      <div v-if="history.length" class="history-caption"><strong>{{ project.name }}</strong><span>最近填写的记录在前</span></div>
      <ProgressHistoryTable v-if="history.length" :entries="history" @open-task="taskId => closeHistory(() => emit('open-task', { id: taskId }))" />
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
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { datetime } from '@/utils/format'
import { announceChange } from './shared'
import ProgressHistoryTable from './ProgressHistoryTable.vue'

const props = defineProps({ project: { type: Object, required: true }, readOnly: Boolean })
const emit = defineEmits(['saved', 'open-task'])
const assistant = useAssistant(), latest = computed(() => props.project.latest_progress)
const active = computed(() => props.project.status === 'active')
const formOpen = ref(false), historyOpen = ref(false), saving = ref(false), loading = ref(false), failed = ref(false)
const history = ref([]), nextBefore = ref(null)
const entries = ref([])
let historyVersion = 0, entryKey = 0, afterHistoryClose
const newEntry = () => ({ key: ++entryKey, progress: '', problems: '', next_plan: '', next_due_date: null })

function closeHistory(after) { afterHistoryClose = after; historyOpen.value = false }
function historyClosed() { const after = afterHistoryClose; afterHistoryClose = undefined; after?.() }
function start() {
  entries.value = [newEntry()]
  formOpen.value = true
}
async function addEntry(event) {
  const anchor = event.currentTarget
  entries.value.push(newEntry())
  await nextTick()
  anchor?.previousElementSibling?.querySelector('textarea')?.focus()
}
function withXiaoju() {
  const values = entries.value.map((entry, index) => `第 ${index + 1} 条\n进度情况：${entry.progress}\n遇到的问题：${entry.problems}${active.value ? `\n下一步工作：${entry.next_plan}\n计划完成日期：${entry.next_due_date || ''}` : ''}`).join('\n\n')
  assistant.openDraft(`帮我填写运营项目「${props.project.name}」（ID：${props.project.id}）的项目进度。按以下事项分别登记；有下一步和日期的加入负责人计划。\n${values}`)
}
async function save() {
  if (saving.value) return
  const payload = entries.value.map(({ progress, problems, next_plan, next_due_date }) => ({
    progress: progress.trim(), problems: problems.trim(), next_plan: next_plan.trim(), next_due_date: next_due_date || null,
  }))
  for (const [index, entry] of payload.entries()) {
    const item = payload.length > 1 ? `第 ${index + 1} 条：` : ''
    if (!entry.progress) return ElMessage.warning(`${item}请填写进度情况`)
    if (Boolean(entry.next_plan) !== Boolean(entry.next_due_date)) return ElMessage.warning(`${item}下一步计划和计划完成日期须一起填写`)
  }
  saving.value = true
  try {
    const result = await api.post(`/operations/${props.project.id}/progress/entries`, { entries: payload, expected_owner_id: props.project.owner_id })
    formOpen.value = false; emit('saved', result.items.at(-1)); announceChange()
    ElMessage.success(result.created_task_ids.length ? `已保存进度，新增 ${result.created_task_ids.length} 条负责人计划` : '项目进度已保存')
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
.project-progress { margin: 0 0 20px; padding: 0 0 15px; border-bottom: 1px solid var(--jc-line); }
header, .form-footer, .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; } header, .form-footer { justify-content: space-between; } .actions .el-button + .el-button { margin: 0; }
header h3 { margin: 0; color: var(--jc-ink); font-size: 16px; } header p { margin: 3px 0 0; color: var(--jc-ink-3); font-size: 12px; }
.note { margin-top: 10px; background: var(--jc-surface-2); border-radius: 7px; padding: 8px 11px; font-size: 13px; color: var(--jc-ink-2); }
.note summary { cursor: pointer; }.note summary strong { font-size: 12px; }.note summary span { display: inline-block; max-width: calc(100% - 120px); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; vertical-align: bottom; margin-left: 8px; }.note[open] summary span { display: none; }
.progress-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); gap: 10px; margin: 10px 0 0; }
.progress-grid > div { min-width: 0; }.progress-grid dt { font-weight: 600; color: var(--jc-ink); }
.progress-grid dd { margin: 4px 0 0; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }.progress-grid dd:first-of-type { max-height: 140px; overflow: auto; }.plan-date { font-size: 12px; color: var(--jc-brand); }
.form-hint { margin: 0 0 14px; color: var(--jc-ink-2); font-size: 13px; }
.entry { min-width: 0; padding: 0; margin: 0 0 12px; border: 0; }.entry.multiple { border: 1px solid var(--jc-line); border-radius: 8px; padding: 8px 13px 12px; }.entry legend { color: var(--jc-brand); font-weight: 600; }.entry :deep(.el-form-item) { margin-bottom: 12px; }
:global(.ops-progress-entry-dialog > .el-dialog__body) { max-height: 65dvh; overflow: auto; overscroll-behavior: contain; }
.history-list { min-height: 120px; }.history-caption { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; margin: 0 0 10px; }.history-caption strong { color: var(--jc-ink); }.history-caption span { color: var(--jc-ink-3); font-size: 12px; }
@media (max-width: 620px) { .progress-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
