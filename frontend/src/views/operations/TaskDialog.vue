<template>
  <el-dialog v-model="visible" class="ops-task-dialog" title="任务详情" width="min(1050px, calc(100vw - 40px))" align-center append-to-body destroy-on-close :close-on-click-modal="false">
    <div v-loading="loading" class="task-page">
      <el-empty v-if="unavailable && !loading" description="无法读取这项任务，请检查访问权限或重试。"><el-button @click="load()">重试</el-button></el-empty>
      <template v-if="task && !unavailable">
        <header class="task-header">
          <div class="task-identity">
            <div class="jc-row tags"><StatusPill :text="task.status === 'done' ? completionLabel(task) : statusLabel(task)" :tone="task.status === 'done' ? deadlineTone(task) : task.approval_status === 'approved' ? tone(task.status) : 'warn'" dot /><span>{{ TASK_SOURCES[task.source] }} · {{ task.plan_month }}</span></div>
            <h2>{{ task.title }}</h2>
            <p class="context">{{ [task.project_name, task.school_name].filter(Boolean).join(' · ') }}</p>
          </div>
          <div v-if="!archived || hasHistory" class="actions">
            <el-button v-if="!archived" plain @click="withXiaoju">用小聚办理</el-button>
            <el-button v-if="!archived && task.can_edit" plain @click="emit('edit', task)">调整安排</el-button>
            <el-button v-if="hasHistory" plain @click="historyOpen = true">过程记录</el-button>
          </div>
        </header>
        <dl class="facts">
          <div><dt>负责人</dt><dd>{{ task.owner_name }}</dd></div><div><dt>截止日期</dt><dd>{{ task.due_date }}</dd><small v-if="task.deadline_basis && task.deadline_basis !== task.due_date">原定 {{ task.deadline_basis }}</small></div>
          <div><dt>反馈频率</dt><dd>{{ task.cadence_label }}</dd>
            <small v-if="task.feedback_deadline_pending" :class="{ 'jc-down': task.feedback_status === 'overdue' }">反馈截止 {{ feedbackTime(task.feedback_due_at) }}</small>
            <small v-else-if="task.can_feedback">下次反馈 {{ task.next_feedback_on }}</small>
          </div>
          <div v-if="task.approval_status === 'approved'"><dt>任务进度</dt><dd aria-live="polite"><el-progress v-if="progress.percent !== null" aria-label="任务进度" :percentage="progress.percent" :color="task.status === 'blocked' ? 'var(--jc-down)' : task.status === 'done' ? 'var(--jc-up)' : 'var(--jc-brand)'" /><span v-else>{{ progress.label }}</span></dd></div>
          <div v-else><dt>任务审核</dt><dd>{{ statusLabel(task) }}</dd></div>
        </dl>
        <el-alert v-if="archived" :title="'已作废：' + task.archive_reason" type="info" :closable="false" class="notice" />
        <el-alert v-if="task.overdue" title="任务或节点已逾期，请反馈进展与下一步安排。" type="warning" :closable="false" class="notice" />
        <div class="body-grid" :class="{ 'one-column': !task.can_feedback && !task.can_review }">
          <div class="details">
            <section v-if="task.description" class="section"><h3>完成要求</h3><p class="plain">{{ task.description }}</p></section>
            <section v-if="task.plan_context" class="section">
              <h3>登记时的项目进度</h3>
              <p class="plain">{{ task.plan_context.progress }}</p>
              <p v-if="task.plan_context.problems" class="plain jc-muted">遇到的问题：{{ task.plan_context.problems }}</p>
            </section>
            <section v-if="task.source === 'self'" class="section"><h3>审核流程</h3><p>部门审核：{{ task.department_reviewer_name }}<br>公司审核：{{ task.company_reviewer_name }}</p></section>
            <TaskDelayPanel v-if="!archived && task.delay_status" class="section" :task="task" :save="save" :busy="saving" />
            <section v-if="task.nodes.length" class="section">
              <h3>阶段节点 <small>{{ task.nodes.filter(n => n.completed_at).length }} / {{ task.nodes.length }} 已完成</small></h3>
              <details v-for="(node, index) in task.nodes" :key="node.id" class="node-detail">
                <summary>{{ index + 1 }}. {{ node.name }} <small>{{ node.completed_at ? '已完成 · ' + datetime(node.completed_at) : (node.overdue ? '逾期 · ' : '') + node.due_date }}</small></summary>
                <div v-for="u in task.updates.filter(u => u.node_id === node.id)" :key="u.id" class="node-entry"><small>{{ datetime(u.created_at) }} · {{ u.author_name }}</small><p class="plain">{{ u.content }}</p></div>
                <p v-if="!task.updates.some(u => u.node_id === node.id)" class="jc-muted jc-small">暂无单独登记的节点进展；原有记录仍保留在任务过程记录中。</p>
                <el-button v-if="task.can_feedback" link type="primary" @click="form.node_id = node.id">反馈此节点</el-button>
              </details>
            </section>
            <TaskDiscussion :task="task" @saved="load(true)" />
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
                <el-form-item v-if="task.nodes.length" label="本次反馈位置"><el-select v-model="form.node_id" clearable placeholder="整个任务" aria-label="本次反馈位置"><el-option v-for="n in task.nodes" :key="n.id" :label="n.name" :value="n.id" /></el-select></el-form-item>
                <el-form-item label="完成情况" class="result-choice">
                  <el-radio-group v-model="outcome" aria-label="完成情况"><el-radio-button value="done">已完成</el-radio-button><el-radio-button value="pending">未完成</el-radio-button></el-radio-group>
                </el-form-item>
                <el-form-item :label="outcome === 'done' ? '完成结果' : '目前进度情况'" prop="content" :rules="[{ required: true, whitespace: true, message: '写几句话说明结果或当前进展' }]">
                  <el-input v-model="form.content" type="textarea" :rows="3" maxlength="6000" :validate-event="false" placeholder="写几句话即可，例如：已和校方确认需求，明天下午提交方案。" @input="formRef?.clearValidate('content')" />
                </el-form-item>
                <el-button link type="primary" :loading="drafting" :disabled="!form.content.trim() || saving" @click="draft">让 AI 整理文字</el-button>
                <div v-if="draftText" class="draft-preview"><p class="plain">{{ draftText }}</p><el-button size="small" @click="form.content = draftText; draftText = ''">采用这段文字</el-button><el-button size="small" text @click="draftText = ''">保留原文</el-button></div>
                <el-form-item v-if="outcome === 'pending'" label="当前情况"><el-checkbox v-model="blocked">遇到阻碍</el-checkbox></el-form-item>
                <el-form-item v-if="remainingNodes.length" label="本次完成的节点">
                  <el-checkbox-group v-model="form.completed_node_ids"><el-checkbox v-for="node in remainingNodes" :key="node.id" :value="node.id">{{ node.name }}</el-checkbox></el-checkbox-group>
                  <span class="node-hint">勾选已完成的节点，提交后自动更新进度。</span>
                </el-form-item>
                <el-form-item label="下一步（可选）"><el-input v-model="form.next_step" maxlength="6000" placeholder="下一步要做什么，需要谁协调" /></el-form-item>
                <el-form-item v-if="outcome === 'pending' || captureDelayReason" :label="reasonLabel"><el-input v-model="form.incomplete_reason" type="textarea" :rows="2" maxlength="6000" placeholder="例如：等待校方提供资料，预计明天补齐" /></el-form-item>
              </el-form>
            </section>
          </div>
        </div>
      </template>
    </div>
    <el-dialog v-model="historyOpen" title="过程记录" width="min(720px, calc(100vw - 24px))" align-center append-to-body destroy-on-close class="task-history-dialog">
      <h3 v-if="task" class="history-count">过程记录 <small>{{ timeline.length }} 条</small></h3>
      <el-timeline v-if="timeline.length" class="history-timeline">
        <el-timeline-item v-for="u in timeline" :key="u.key" :timestamp="`${datetime(u.created_at)} · ${u.author_name}`" placement="top">
          <b>{{ u.title }}{{ u.node_id ? ' · ' + (task.nodes.find(n => n.id === u.node_id)?.name || '历史节点') : '' }}</b>
          <span v-if="u.recipient_name" class="jc-small jc-muted"> · 给 {{ u.recipient_name }}</span>
          <p class="plain">{{ u.content }}</p>
          <p v-if="u.reply_to_id" class="jc-small jc-muted">回复要求 #{{ u.reply_to_id }}</p>
          <p v-if="u.next_step" class="plain jc-small jc-muted">下一步：{{ u.next_step }}</p>
        </el-timeline-item>
      </el-timeline>
      <p v-else class="jc-muted">暂无过程记录</p>
      <WecomDeliveryStatus :rows="task?.notification_deliveries" />
    </el-dialog>
    <template v-if="!archived && !unavailable && (task?.can_feedback || task?.can_review || task?.delay_status)" #footer>
      <div class="task-footer"><el-button @click="visible = false">关闭</el-button>
        <el-button v-if="task.can_review" type="danger" plain :disabled="saving || draftStale" @click="review('reject')">退回修改</el-button>
        <el-button v-if="task.can_review" type="primary" :loading="saving" :disabled="draftStale" @click="review('approve')">审核通过</el-button>
        <el-button v-else-if="task.can_feedback" type="primary" :loading="saving" :disabled="drafting || draftStale" @click="submit">提交进展</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import WecomDeliveryStatus from '@/components/WecomDeliveryStatus.vue'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { datetime } from '@/utils/format'
import { feedbackTime } from '@/utils/taskFeedback'
import { TASK_SOURCES, TASK_STATUS, completionLabel, deadlineTone, statusLabel, taskProgress, tone, announceChange } from './shared'
import TaskDelayPanel from './TaskDelayPanel.vue'
import TaskDiscussion from './TaskDiscussion.vue'

const props = defineProps({ modelValue: Boolean, taskId: Number, projectId: Number, archived: Boolean })
const emit = defineEmits(['update:modelValue', 'edit'])
const historyOpen = ref(false)
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const assistant = useAssistant(), task = ref(null), loading = ref(false), unavailable = ref(false), saving = ref(false)
const timeline = computed(() => {
  if (!task.value) return []
  const labels = { note: '留言', request: '办理要求', reply: '回复' }
  return [...task.value.updates.map(u => ({ ...u, key: `update-${u.id}`, title: updateTitle(u) })),
    ...(task.value.discussions || []).map(d => ({ ...d, key: `discussion-${d.id}`, title: labels[d.kind] }))]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id)
})
const hasHistory = computed(() => !!(timeline.value.length || task.value?.notification_deliveries?.length))
const drafting = ref(false), draftText = ref(''), formRef = ref(), reviewComment = ref(''), formRevision = ref(null)
const form = reactive({ content: '', next_step: '', status: 'doing', completed_node_ids: [], incomplete_reason: '', node_id: null })
const blocked = computed({ get: () => form.status === 'blocked', set: value => { form.status = value ? 'blocked' : 'doing' } })
let beforeComplete = 'doing'
const outcome = computed({
  get: () => form.status === 'done' ? 'done' : 'pending',
  set: value => {
    if (value === 'done' && form.status !== 'done') {
      beforeComplete = form.status
      form.status = 'done'
    } else if (value === 'pending' && form.status === 'done') {
      form.status = beforeComplete
    }
  },
})
const remainingNodes = computed(() => task.value?.nodes.filter(n => !n.completed_at) || [])
const progress = computed(() => task.value && taskProgress(task.value))
const requiresReason = computed(() => form.status === 'blocked' || (task.value && (task.value.due_date < task.value.today || remainingNodes.value.some(node => node.overdue && !form.completed_node_ids.includes(node.id)))))
const captureDelayReason = computed(() => task.value?.delay_status === 'needs_reason')
const reasonLabel = computed(() => `${captureDelayReason.value ? '延期原因' : '未完成原因'}（${outcome.value === 'pending' && requiresReason.value ? '必填' : '可选'}）`)
const draftStale = computed(() => task.value && formRevision.value !== task.value.revision)
let version = 0, baseline = ''

function resetForm(row) {
  Object.assign(form, { content: '', next_step: row.next_step || '', status: row.status === 'todo' ? 'doing' : row.status,
    completed_node_ids: [], incomplete_reason: row.incomplete_reason || '', node_id: null })
  formRevision.value = row.revision; baseline = JSON.stringify(form); draftText.value = ''; reviewComment.value = ''
  formRef.value?.clearValidate()
}
async function load(background = false) {
  if (!visible.value || !props.taskId || (background && saving.value)) return
  const current = ++version, id = props.taskId
  if (!background) loading.value = true
  try {
    const result = await api.get(`/operations/tasks/${id}`, props.archived ? { archived: true } : null, { silent: true })
    if (current !== version || !visible.value) return
    if (props.projectId && result.project_id !== props.projectId) { historyOpen.value = false; task.value = null; unavailable.value = true; return }
    if (task.value?.revision > result.revision) return
    const keepDraft = task.value && (JSON.stringify(form) !== baseline || reviewComment.value.trim())
    task.value = result; unavailable.value = false
    if (!keepDraft) resetForm(result)
  } catch (err) {
    if (current === version && (!background || err.code === 40301 || err.code === 40401 || !task.value)) { historyOpen.value = false; task.value = null; unavailable.value = true }
  } finally { if (current === version) loading.value = false }
}
watch(() => [visible.value, props.taskId], () => { version++; historyOpen.value = false; task.value = null; unavailable.value = false; draftText.value = ''; load() }, { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => visible.value })

function updateTitle(update) {
  return ({ assigned: '任务安排', planned: '登记负责人计划', changed: '调整安排', review: '审核记录' })[update.kind] ||
    TASK_STATUS[update.status] + (update.progress > 0 ? ` · ${update.progress}%` : '')
}
function acceptLatest() {
  formRevision.value = task.value.revision
  form.completed_node_ids = form.completed_node_ids.filter(id => remainingNodes.value.some(node => node.id === id))
}
function withXiaoju() {
  const reference = `「${task.value.title}」(任务编号 ${task.value.id})`
  if (task.value.can_review_delay) return assistant.openDraft(`查看任务${reference}的延期原因。我的复核决定和意见是：`)
  if (task.value.can_explain_delay && !task.value.can_feedback) return assistant.openDraft(`帮我为任务${reference}补充延期原因：`)
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
async function save(kind, body, message, revision = formRevision.value) {
  const delayAction = kind === 'delay-reason' || kind === 'delay-review'
  if (saving.value || (!delayAction && draftStale.value)) return false
  const id = task.value.id
  const feedbackWasCurrent = !draftStale.value
  saving.value = true
  try {
    const result = await api.post(`/operations/tasks/${id}/${kind}`, { ...body, expected_revision: revision })
    if (visible.value && task.value?.id === id) {
      task.value = result
      if (!delayAction) resetForm(result)
      else if (feedbackWasCurrent) formRevision.value = result.revision
    }
    ElMessage.success(message); announceChange()
    return true
  } catch { if (visible.value && task.value?.id === id) load(); return false }
  finally { saving.value = false }
}
async function submit() {
  if (!await formRef.value.validate().catch(() => false)) return
  if (form.status === 'done' && remainingNodes.value.some(n => !form.completed_node_ids.includes(n.id))) return ElMessage.warning('请确认所有阶段节点已完成')
  if (form.status !== 'done' && requiresReason.value && !form.incomplete_reason.trim()) return ElMessage.warning('请填写未完成原因')
  await save('feedback', { ...form, incomplete_reason: outcome.value === 'pending' || captureDelayReason.value ? form.incomplete_reason : '' }, '进展已反馈')
}
function review(decision) {
  if (!reviewComment.value.trim()) return ElMessage.warning('请填写审核意见或退回原因')
  return save('review', { decision, comment: reviewComment.value }, decision === 'approve' ? '本级审核已通过' : '任务已退回')
}
</script>

<style scoped>
.node-detail { border: 1px solid var(--jc-line); padding: 12px; margin: 8px 0; border-radius: 6px; }.node-detail summary { cursor: pointer; line-height: 1.8; }.node-detail summary small { display: block; color: var(--jc-ink-3); }.node-entry { border-top: 1px solid var(--jc-line); padding-top: 10px; margin-top: 10px; }
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
.facts { display: grid; grid-template-columns: 1fr 1fr 1fr 1.3fr; gap: 10px; margin: 17px 0; }.facts > div { min-width: 0; padding: 9px 11px; background: var(--jc-bg-2, #f9f8f5); border: 1px solid var(--jc-line); border-radius: 7px; }.facts dt { font-size: 12px; color: var(--jc-ink-3); }.facts dd { margin: 4px 0 0; color: var(--jc-ink); font-weight: 600; overflow-wrap: anywhere; }
.notice { margin-bottom: 12px; }.body-grid { display: grid; grid-template-columns: minmax(0, .95fr) minmax(0, 1fr); gap: 20px; align-items: start; }.body-grid.one-column { grid-template-columns: minmax(0, 1fr); }.details, .work { min-width: 0; }
.facts small { display: block; color: var(--jc-ink-3); margin-top: 3px; }
.section { border: 1px solid var(--jc-line); border-radius: 8px; padding: 14px; }.section + .section { margin-top: 10px; }.section h3 { font-size: 15px; margin: 0 0 10px; color: var(--jc-ink); }.section h3 small { font-size: 11px; margin-left: 6px; font-weight: 400; color: var(--jc-ink-3); }.section p { margin: 0; }.plain { white-space: pre-wrap; line-height: 1.7; overflow-wrap: anywhere; }
.nodes { list-style: none; margin: 0; padding: 0; }.nodes li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px; align-items: baseline; padding: 6px 0; overflow-wrap: anywhere; }.nodes li + li { border-top: 1px solid var(--jc-line-2); }.nodes li i { background: var(--jc-bg); border: 1px solid var(--jc-line); color: var(--jc-ink-2); border-radius: 50%; display: inline-grid; width: 19px; height: 19px; place-items: center; font-style: normal; font-size: 10px; }.nodes li.complete i { border-color: var(--jc-up); color: var(--jc-up); }.nodes li small { color: var(--jc-ink-3); }.nodes li.complete small { color: var(--jc-up); }.nodes li.overdue small { color: var(--jc-down); }
.history-count { font-size: 15px; margin: 0 0 14px; color: var(--jc-ink); }.history-count small { font-size: 11px; margin-left: 6px; font-weight: 400; color: var(--jc-ink-3); }
:global(.task-history-dialog > .el-dialog__body) { max-height: 68dvh; overflow: auto; overscroll-behavior: contain; }
.history-timeline { padding: 0 4px 0 2px; }.history-timeline :deep(.el-timeline-item:last-child) { padding-bottom: 0; }
.feedback-box :deep(.el-form-item) { margin-bottom: 13px; }.feedback-box :deep(.el-checkbox) { height: auto; min-height: 25px; white-space: normal; }.draft-preview { padding: 9px; background: var(--jc-bg); border: 1px solid var(--jc-line); border-radius: 6px; }
.result-choice :deep(.el-radio-group) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }.result-choice :deep(.el-radio-button__inner) { width: 100%; }
.node-hint { flex: 0 0 100%; color: var(--jc-ink-3); font-size: 12px; line-height: 1.5; }
@media (max-width: 740px) { .body-grid { grid-template-columns: minmax(0, 1fr); gap: 12px; }.work { grid-row: 1; }.facts { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { :global(.ops-task-dialog.el-dialog) { width: 100vw; height: 100dvh; border-radius: 0; }:global(.ops-task-dialog > .el-dialog__body) { max-height: none; }.task-page { padding: 14px; }.section { padding: 12px; } }
</style>
