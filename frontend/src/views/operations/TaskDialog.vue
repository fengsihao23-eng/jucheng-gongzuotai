<template>
  <el-dialog v-model="visible" class="ops-task-dialog" title="任务详情" width="min(930px, calc(100vw - 40px))" align-center append-to-body destroy-on-close :close-on-click-modal="false">
    <div v-loading="loading" class="task-page">
      <el-empty v-if="unavailable && !loading" description="无法读取这项任务，请检查访问权限或重试。"><el-button @click="load()">重试</el-button></el-empty>
      <template v-if="task && !unavailable">
        <header class="task-header">
          <div class="task-identity">
            <div class="jc-row tags"><StatusPill :text="statusLabel(task)" :tone="task.approval_status === 'approved' ? tone(task.status) : 'warn'" dot /><span>{{ TASK_SOURCES[task.source] }} · {{ task.plan_month }}</span></div>
            <h2>{{ task.title }}</h2>
            <p class="context">{{ [task.project_name, task.school_name].filter(Boolean).join(' · ') }}</p>
          </div>
          <div class="actions"><el-button link type="primary" @click="withXiaoju">用小聚办理</el-button><el-button v-if="task.can_edit" link type="primary" @click="emit('edit', task)">调整安排</el-button></div>
        </header>
        <div class="facts">
          <span>负责人 <strong>{{ task.owner_name }}</strong></span><span>截止 <strong>{{ task.due_date }}</strong></span><span>{{ task.cadence === 'daily' ? '每天反馈' : '按节点反馈' }}</span>
          <el-progress v-if="task.approval_status === 'approved'" aria-label="任务进度" :percentage="task.progress" :stroke-width="5" :color="task.status === 'blocked' ? 'var(--jc-down)' : task.status === 'done' ? 'var(--jc-up)' : 'var(--jc-brand)'" />
        </div>
        <el-alert v-if="task.overdue" title="任务或节点已逾期，请反馈进展与下一步安排。" type="warning" :closable="false" class="notice" />
        <div class="body-grid" :class="{ 'one-column': !task.can_feedback && !task.can_review }">
          <div class="details">
            <section v-if="task.description" class="section"><h3>完成要求</h3><p class="plain">{{ task.description }}</p></section>
            <section v-if="task.source === 'self'" class="section"><h3>审核流程</h3><p>部门审核：{{ task.department_reviewer_name }}<br>公司审核：{{ task.company_reviewer_name }}</p></section>
            <section v-if="task.nodes.length" class="section">
              <h3>阶段节点 <small>{{ task.nodes.filter(n => n.completed_at).length }} / {{ task.nodes.length }} 已完成</small></h3>
              <ol class="nodes"><li v-for="(node, index) in task.nodes" :key="node.id" :class="{ complete: node.completed_at, overdue: node.overdue && !node.completed_at }"><i aria-hidden="true">{{ index + 1 }}</i><span>{{ node.name }}</span><small>{{ node.completed_at ? '已完成' : node.overdue ? `逾期 · ${node.due_date}` : node.due_date }}</small></li></ol>
            </section>
            <section v-if="task.updates.length" class="section history">
              <h3>最新记录</h3>
              <article class="update"><div class="jc-small jc-muted">{{ datetime(task.updates[0].created_at) }} · {{ task.updates[0].author_name }}</div>
                <b>{{ updateTitle(task.updates[0]) }}</b><p class="plain">{{ task.updates[0].content }}</p><p v-if="task.updates[0].next_step" class="plain jc-small jc-muted">下一步：{{ task.updates[0].next_step }}</p>
              </article>
              <details v-if="task.updates.length > 1" class="past-records">
                <summary>查看历史记录（{{ task.updates.length - 1 }}）</summary>
                <article v-for="u in task.updates.slice(1)" :key="u.id" class="update past">
                  <div class="jc-small jc-muted">{{ datetime(u.created_at) }} · {{ u.author_name }}</div><b>{{ updateTitle(u) }}</b>
                  <p class="plain">{{ u.content }}</p><p v-if="u.next_step" class="plain jc-small jc-muted">下一步：{{ u.next_step }}</p>
                </article>
              </details>
            </section>
          </div>
          <div v-if="task.can_feedback || task.can_review" class="work">
            <el-alert v-if="draftStale" title="任务已有新进展。请先核对最新记录，再提交当前内容。" type="warning" :closable="false" class="notice"><el-button link type="primary" @click="acceptLatest">按最新记录继续</el-button></el-alert>
            <section v-if="task.can_review" class="section">
              <h3>{{ task.review_stage === 'department' ? '部门审核' : '公司审核' }}</h3>
              <el-input v-model="reviewComment" type="textarea" :rows="3" maxlength="6000" aria-label="审核意见" placeholder="填写审核意见或退回原因" />
            </section>
            <section v-if="task.can_feedback" class="section feedback-box">
              <h3>任务反馈</h3>
              <el-form ref="formRef" :model="form" label-position="top">
                <el-form-item label="完成情况" class="result-choice">
                  <el-radio-group v-model="outcome" aria-label="完成情况"><el-radio-button value="done">已完成</el-radio-button><el-radio-button value="pending">未完成</el-radio-button></el-radio-group>
                </el-form-item>
                <el-form-item :label="outcome === 'done' ? '完成结果' : '目前进度情况'" prop="content" :rules="[{ required: true, whitespace: true, message: '写几句话说明结果或当前进展' }]">
                  <el-input v-model="form.content" type="textarea" :rows="3" maxlength="6000" placeholder="写几句话即可，例如：已和校方确认需求，明天下午提交方案。" />
                </el-form-item>
                <div class="feedback-tools">
                  <el-checkbox v-if="outcome === 'pending'" v-model="blocked">遇到阻碍</el-checkbox>
                  <el-button link type="primary" :loading="drafting" :disabled="!form.content.trim() || saving" @click="draft">让 AI 整理文字</el-button>
                </div>
                <div v-if="draftText" class="draft-preview"><p class="plain">{{ draftText }}</p><el-button size="small" @click="form.content = draftText; draftText = ''">采用这段文字</el-button><el-button size="small" text @click="draftText = ''">保留原文</el-button></div>
                <el-form-item v-if="remainingNodes.length" label="本次完成的节点"><el-checkbox-group v-model="form.completed_node_ids"><el-checkbox v-for="node in remainingNodes" :key="node.id" :value="node.id">{{ node.name }}</el-checkbox></el-checkbox-group></el-form-item>
                <el-form-item v-if="outcome === 'pending' && (requiresReason || extras)" :label="requiresReason ? '未完成原因（必填）' : '未完成原因（可选）'"><el-input v-model="form.incomplete_reason" type="textarea" :rows="2" maxlength="6000" placeholder="例如：等待校方提供资料，预计明天补齐" /></el-form-item>
                <el-button link type="primary" :aria-expanded="extras" :aria-controls="extrasId" @click="extras = !extras">{{ extras ? '收起补充信息' : '补充进度或下一步' }}</el-button>
                <div v-show="extras" :id="extrasId" class="extra-fields">
                  <el-form-item v-if="outcome === 'pending'" label="完成进度（%）"><el-input-number v-model="form.progress" :min="0" :max="99" :step="10" /></el-form-item>
                  <el-form-item label="下一步（可选）"><el-input v-model="form.next_step" maxlength="6000" placeholder="下一步要做什么，需要谁协调" /></el-form-item>
                </div>
              </el-form>
            </section>
          </div>
        </div>
      </template>
    </div>
    <template v-if="!unavailable && (task?.can_feedback || task?.can_review)" #footer>
      <div class="task-footer"><el-button @click="visible = false">关闭</el-button>
        <el-button v-if="task.can_review" type="danger" plain :disabled="saving || draftStale" @click="review('reject')">退回修改</el-button>
        <el-button v-if="task.can_review" type="primary" :loading="saving" :disabled="draftStale" @click="review('approve')">审核通过</el-button>
        <el-button v-else type="primary" :loading="saving" :disabled="drafting || draftStale" @click="submit">提交进展</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, useId, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { datetime } from '@/utils/format'
import { TASK_SOURCES, TASK_STATUS, statusLabel, tone, announceChange } from './shared'

const props = defineProps({ modelValue: Boolean, taskId: Number, projectId: Number })
const emit = defineEmits(['update:modelValue', 'edit'])
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const assistant = useAssistant(), task = ref(null), loading = ref(false), unavailable = ref(false), saving = ref(false)
const drafting = ref(false), draftText = ref(''), formRef = ref(), reviewComment = ref(''), formRevision = ref(null)
const extras = ref(false), extrasId = useId()
const form = reactive({ content: '', next_step: '', status: 'doing', progress: 0, completed_node_ids: [], incomplete_reason: '' })
const blocked = computed({ get: () => form.status === 'blocked', set: value => { form.status = value ? 'blocked' : 'doing' } })
let beforeComplete = { status: 'doing', progress: 0 }
const outcome = computed({
  get: () => form.status === 'done' ? 'done' : 'pending',
  set: value => {
    if (value === 'done' && form.status !== 'done') {
      beforeComplete = { status: form.status, progress: form.progress }
      form.status = 'done'; form.progress = 100
    } else if (value === 'pending' && form.status === 'done') {
      form.status = beforeComplete.status; form.progress = Math.min(99, beforeComplete.progress || 0)
    }
  },
})
const remainingNodes = computed(() => task.value?.nodes.filter(n => !n.completed_at) || [])
const requiresReason = computed(() => form.status === 'blocked' || (task.value && (task.value.due_date < task.value.today || remainingNodes.value.some(node => node.overdue && !form.completed_node_ids.includes(node.id)))))
const draftStale = computed(() => task.value && formRevision.value !== task.value.revision)
let version = 0, baseline = ''

function resetForm(row) {
  Object.assign(form, { content: '', next_step: row.next_step || '', status: row.status === 'todo' ? 'doing' : row.status,
    progress: row.progress, completed_node_ids: [], incomplete_reason: row.incomplete_reason || '' })
  formRevision.value = row.revision; baseline = JSON.stringify(form); draftText.value = ''; reviewComment.value = ''
}
async function load(background = false) {
  if (!visible.value || !props.taskId || (background && saving.value)) return
  const current = ++version, id = props.taskId
  if (!background) loading.value = true
  try {
    const result = await api.get(`/operations/tasks/${id}`, null, { silent: true })
    if (current !== version || !visible.value) return
    if (props.projectId && result.project_id !== props.projectId) { task.value = null; unavailable.value = true; return }
    if (task.value?.revision > result.revision) return
    const keepDraft = task.value && (JSON.stringify(form) !== baseline || reviewComment.value.trim())
    task.value = result; unavailable.value = false
    if (!keepDraft) resetForm(result)
  } catch (err) {
    if (current === version && (!background || err.code === 40301 || err.code === 40401 || !task.value)) { task.value = null; unavailable.value = true }
  } finally { if (current === version) loading.value = false }
}
watch(() => [visible.value, props.taskId], () => { version++; task.value = null; unavailable.value = false; draftText.value = ''; extras.value = false; load() }, { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => visible.value })

function updateTitle(update) {
  return ({ assigned: '任务安排', changed: '调整安排', review: '审核记录' })[update.kind] || `${TASK_STATUS[update.status]} · ${update.progress}%`
}
function acceptLatest() {
  formRevision.value = task.value.revision
  form.completed_node_ids = form.completed_node_ids.filter(id => remainingNodes.value.some(node => node.id === id))
}
function withXiaoju() {
  const reference = `「${task.value.title}」(任务编号 ${task.value.id})`
  assistant.openDraft(task.value.can_feedback ? `帮我反馈任务${reference}的进展：` : task.value.can_review ? `帮我查看并处理任务${reference}的审核：` : `汇总任务${reference}的最新进展和下一步。`)
}
async function draft() {
  drafting.value = true
  const id = task.value.id, original = form.content
  try {
    const result = await api.post(`/operations/tasks/${id}/draft`, { text: original }, { timeout: 90000 })
    if (!visible.value || id !== task.value?.id || original !== form.content) return
    if (result.assisted) draftText.value = result.text
    else ElMessage.info('暂时无法整理，可直接修改原文并提交')
  } catch {} finally { drafting.value = false }
}
async function save(kind, body, message) {
  if (saving.value || draftStale.value) return
  const id = task.value.id
  saving.value = true
  try {
    const result = await api.post(`/operations/tasks/${id}/${kind}`, { ...body, expected_revision: formRevision.value })
    if (visible.value && task.value?.id === id) { task.value = result; resetForm(result) }
    ElMessage.success(message); announceChange()
  } catch { if (visible.value && task.value?.id === id) load() }
  finally { saving.value = false }
}
async function submit() {
  if (!await formRef.value.validate().catch(() => false)) return
  if (form.status === 'done' && remainingNodes.value.some(n => !form.completed_node_ids.includes(n.id))) return ElMessage.warning('请确认所有阶段节点已完成')
  if (form.status !== 'done' && requiresReason.value && !form.incomplete_reason.trim()) return ElMessage.warning('请填写未完成原因')
  await save('feedback', { ...form, progress: Number.isInteger(form.progress) ? form.progress : task.value.progress }, '进展已反馈')
}
function review(decision) {
  if (!reviewComment.value.trim()) return ElMessage.warning('请填写审核意见或退回原因')
  return save('review', { decision, comment: reviewComment.value }, decision === 'approve' ? '本级审核已通过' : '任务已退回')
}
</script>

<style scoped>
:global(.ops-task-dialog.el-dialog) { display: flex; flex-direction: column; padding: 0; overflow: hidden; border-radius: 13px; }
:global(.ops-task-dialog > .el-dialog__header) { padding: 15px 23px 13px; border-bottom: 1px solid var(--jc-line); margin: 0; }
:global(.ops-task-dialog > .el-dialog__header .el-dialog__title) { font-size: 14px; font-weight: 600; }
:global(.ops-task-dialog > .el-dialog__header .el-dialog__headerbtn) { top: 1px; right: 4px; }
:global(.ops-task-dialog > .el-dialog__body) { flex: 1; min-height: 0; max-height: min(76vh, calc(100dvh - 124px)); overflow: auto; overscroll-behavior: contain; }
:global(.ops-task-dialog > .el-dialog__footer) { border-top: 1px solid var(--jc-line); padding: 9px 23px; }
.task-footer { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }.task-footer .el-button + .el-button { margin-left: 0; }
.task-page { min-height: 140px; padding: 20px 23px 24px; }
.task-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; flex-wrap: wrap; }
.task-identity { flex: 1 1 220px; min-width: 0; }.tags { flex-wrap: wrap; gap: 8px; color: var(--jc-ink-3); font-size: 12px; }
h2 { font: 600 clamp(21px, 3vw, 28px)/1.35 var(--jc-font-serif); color: var(--jc-ink); margin: 8px 0 4px; overflow-wrap: anywhere; }
.context { color: var(--jc-ink-3); margin: 0; font-size: 13px; }.actions { display: flex; gap: 8px; flex-wrap: wrap; }.actions .el-button + .el-button { margin: 0; }
.facts { display: flex; align-items: center; flex-wrap: wrap; gap: 7px 15px; margin: 13px 0; padding-bottom: 12px; border-bottom: 1px solid var(--jc-line-2); color: var(--jc-ink-3); font-size: 13px; }.facts strong { color: var(--jc-ink); font-weight: 600; }.facts .el-progress { width: 132px; }
.notice { margin-bottom: 12px; }.body-grid { display: grid; grid-template-columns: minmax(0, .95fr) minmax(0, 1fr); gap: 20px; align-items: start; }.body-grid.one-column { grid-template-columns: minmax(0, 1fr); }.details, .work { min-width: 0; }
.work { border-left: 1px solid var(--jc-line-2); padding-left: 19px; }.section + .section { margin-top: 13px; padding-top: 11px; border-top: 1px solid var(--jc-line-2); }.section h3 { font-size: 15px; margin: 0 0 8px; color: var(--jc-ink); }.section h3 small { font-size: 11px; margin-left: 6px; font-weight: 400; color: var(--jc-ink-3); }.section p { margin: 0; }.plain { white-space: pre-wrap; line-height: 1.65; overflow-wrap: anywhere; }
.nodes { list-style: none; margin: 0; padding: 0; }.nodes li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px; align-items: baseline; padding: 6px 0; overflow-wrap: anywhere; }.nodes li + li { border-top: 1px solid var(--jc-line-2); }.nodes li i { background: var(--jc-bg); border: 1px solid var(--jc-line); color: var(--jc-ink-2); border-radius: 50%; display: inline-grid; width: 19px; height: 19px; place-items: center; font-style: normal; font-size: 10px; }.nodes li.complete i { border-color: var(--jc-up); color: var(--jc-up); }.nodes li small { color: var(--jc-ink-3); }.nodes li.complete small { color: var(--jc-up); }.nodes li.overdue small { color: var(--jc-down); }
.update b { display: block; margin: 4px 0; }.past-records { margin-top: 10px; }.past-records summary { color: var(--jc-brand); cursor: pointer; font-size: 13px; }.update.past { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--jc-line-2); }
.feedback-tools { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 0 0 8px; }.feedback-tools .el-checkbox { margin-right: 0; }.extra-fields { margin-top: 8px; }
.feedback-box :deep(.el-form-item) { margin-bottom: 13px; }.feedback-box :deep(.el-checkbox) { height: auto; min-height: 25px; white-space: normal; }.draft-preview { padding: 9px; background: var(--jc-surface-2); border: 1px solid var(--jc-line); border-radius: 6px; }
.result-choice :deep(.el-radio-group) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }.result-choice :deep(.el-radio-button__inner) { width: 100%; }
@media (max-width: 740px) { .body-grid { grid-template-columns: minmax(0, 1fr); gap: 13px; }.work { grid-row: 1; border: 0; padding: 0 0 10px; border-bottom: 1px solid var(--jc-line-2); } }
@media (max-width: 600px) { :global(.ops-task-dialog.el-dialog) { width: 100vw; height: 100dvh; border-radius: 0; }:global(.ops-task-dialog > .el-dialog__body) { max-height: none; }.task-page { padding: 14px; } }
</style>
