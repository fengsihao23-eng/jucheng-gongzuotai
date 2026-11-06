<template>
  <el-drawer v-model="visible" title="任务进展" size="min(640px, 100vw)" destroy-on-close>
    <div v-loading="loading">
      <template v-if="task">
        <div class="jc-row between"><StatusPill :text="task.approval_status === 'approved' ? TASK_STATUS[task.status] : APPROVAL_STATUS[task.approval_status]" :tone="task.approval_status === 'approved' ? tone(task.status) : 'info'" /><el-button v-if="task.can_edit" link type="primary" @click="emit('edit', task)">调整安排</el-button></div>
        <h2>{{ task.title }}</h2>
        <div class="jc-small jc-muted">{{ task.owner_name }} 负责 · 截止 {{ task.due_date }} · {{ task.cadence === 'daily' ? '每天反馈' : '按节点反馈' }}</div>
        <p class="jc-small jc-muted">{{ TASK_SOURCES[task.source] }} · 计划月份 {{ task.plan_month }}</p>
        <template v-if="task.source === 'self'">
          <p class="jc-small">部门审核：{{ task.department_reviewer_name }} · 公司审核：{{ task.company_reviewer_name }}</p>
          <el-alert v-if="task.approval_status !== 'approved'" :title="`${APPROVAL_STATUS[task.approval_status]}，审核通过后进入执行。`" type="info" :closable="false" />
          <div v-if="task.can_review" class="section">
            <h3>{{ task.review_stage === 'department' ? '部门审核' : '公司审核' }}</h3>
            <el-input v-model="reviewComment" type="textarea" :rows="2" maxlength="6000" aria-label="审核意见" placeholder="填写审核意见或退回原因" />
            <div class="jc-row mt-16"><el-button type="primary" :loading="reviewing" @click="review('approve')">审核通过</el-button><el-button type="danger" plain :disabled="reviewing" @click="review('reject')">退回修改</el-button></div>
          </div>
          <div v-if="task.reviews?.length" class="section"><h3>审核记录</h3><p v-for="r in task.reviews" :key="r.id" class="plain jc-small">{{ datetime(r.created_at) }} · {{ r.reviewer_name }} · {{ r.stage === 'department' ? '部门' : '公司' }}{{ r.decision === 'approve' ? '通过' : '退回' }}（版本 {{ r.task_revision }}）：{{ r.comment }}</p></div>
        </template>
        <p v-if="task.description" class="plain">{{ task.description }}</p>
        <el-progress :percentage="task.progress" :color="task.status === 'blocked' ? '#A24A41' : '#1B3275'" class="mt-16" />
        <el-alert v-if="task.overdue" title="任务或节点已逾期，请及时反馈进展与下一步安排" type="warning" :closable="false" class="mt-16" />
        <div v-if="task.nodes.length" class="section">
          <h3>阶段节点</h3>
          <div v-for="n in task.nodes" :key="n.id" class="node-row"><span>{{ n.name }}</span><span :class="n.overdue ? 'jc-down' : 'jc-muted'">{{ n.completed_at ? '已完成' : n.due_date }}</span></div>
        </div>
        <div v-if="task.can_feedback" class="section feedback-box">
          <h3>反馈进展</h3>
          <el-form ref="formRef" :model="form" label-position="top">
            <el-form-item label="今天完成了什么 / 遇到什么问题" prop="content" :rules="[{ required: true, whitespace: true, message: '写几句话说明进展' }]">
              <el-input v-model="form.content" type="textarea" :rows="3" maxlength="6000" placeholder="写几句话即可，例如：已和校方确认需求，明天下午提交方案。" />
            </el-form-item>
            <div class="jc-row between"><el-button link type="primary" :loading="drafting" :disabled="!form.content.trim() || saving" @click="draft">让 AI 整理文字</el-button><span class="jc-muted jc-small">整理后可修改，再提交</span></div>
            <div v-if="draftText" class="draft-preview"><p class="plain">{{ draftText }}</p><el-button size="small" @click="form.content = draftText; draftText = ''">采用这段文字</el-button><el-button size="small" text @click="draftText = ''">保留原文</el-button></div>
            <div class="two mt-16">
              <el-form-item label="当前状态"><el-select v-model="form.status" @change="v => { if (v === 'done') form.progress = 100; else if (form.progress === 100) form.progress = 90 }"><el-option v-for="(v, k) in TASK_STATUS" :key="k" :value="k" :label="v" /></el-select></el-form-item>
              <el-form-item label="完成进度（%）"><el-input-number v-model="form.progress" :min="0" :max="100" :step="10" :disabled="form.status === 'done'" /></el-form-item>
            </div>
            <el-form-item v-if="remainingNodes.length" label="本次完成的节点"><el-checkbox-group v-model="form.completed_node_ids"><el-checkbox v-for="n in remainingNodes" :key="n.id" :value="n.id">{{ n.name }}</el-checkbox></el-checkbox-group></el-form-item>
            <el-form-item label="下一步（可选）"><el-input v-model="form.next_step" maxlength="6000" placeholder="下一步要做什么，需要谁协调" /></el-form-item>
            <el-form-item v-if="form.status !== 'done'" label="未完成原因（受阻或逾期必填）"><el-input v-model="form.incomplete_reason" type="textarea" :rows="2" maxlength="6000" placeholder="例如：等待校方提供资料，预计明天补齐" /></el-form-item>
            <el-button type="primary" :loading="saving" :disabled="drafting" @click="submit">提交进展</el-button>
          </el-form>
        </div>
        <div class="section">
          <h3>过程记录</h3>
          <el-timeline>
            <el-timeline-item v-for="u in task.updates" :key="u.id" :timestamp="`${datetime(u.created_at)} · ${u.author_name}`" placement="top">
              <b>{{ u.kind === 'assigned' ? '任务安排' : u.kind === 'changed' ? '调整安排' : `${TASK_STATUS[u.status]} · ${u.progress}%` }}</b>
              <p class="plain">{{ u.content }}</p><p v-if="u.next_step" class="jc-small">下一步：{{ u.next_step }}</p>
            </el-timeline-item>
          </el-timeline>
        </div>
      </template>
    </div>
  </el-drawer>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'
import { APPROVAL_STATUS, TASK_SOURCES, TASK_STATUS, tone, announceChange } from './shared'
const props = defineProps({ modelValue: Boolean, taskId: Number, canManage: Boolean })
const emit = defineEmits(['update:modelValue', 'saved', 'edit'])
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const task = ref(null), loading = ref(false), saving = ref(false), drafting = ref(false), draftText = ref(''), formRef = ref()
const form = reactive({ content: '', next_step: '', status: 'doing', progress: 0, completed_node_ids: [], incomplete_reason:'' })
const reviewComment=ref(''), reviewing=ref(false)
const remainingNodes = computed(() => task.value?.nodes.filter(n => !n.completed_at) || [])
let requestVersion = 0
watch([visible, () => props.taskId], async ([v, id]) => {
  const version = ++requestVersion
  if (!v || !id) return
  task.value = null; draftText.value = ''; reviewComment.value=''; loading.value = true
  try {
    const result = await api.get(`/operations/tasks/${id}`)
    if (version !== requestVersion) return
    task.value = result
    Object.assign(form, { content: '', next_step: result.next_step || '', status: result.status === 'todo' ? 'doing' : result.status, progress: result.progress, completed_node_ids: [], incomplete_reason:result.incomplete_reason || '' })
  } catch {} finally { if (version === requestVersion) loading.value = false }
})
async function draft() {
  drafting.value = true
  const id = task.value.id, version = requestVersion
  try {
    const r = await api.post(`/operations/tasks/${id}/draft`, { text: form.content }, { timeout: 90000 })
    if (version !== requestVersion) return
    if (r.assisted) draftText.value = r.text
    else ElMessage.info('暂时无法整理，可直接修改原文并提交')
  } catch {} finally { drafting.value = false }
}
async function submit() {
  if (!await formRef.value.validate().catch(() => false)) return
  if (form.status === 'done' && remainingNodes.value.some(n => !form.completed_node_ids.includes(n.id))) return ElMessage.warning('请确认所有阶段节点已完成')
  saving.value = true
  const id = task.value.id, version = requestVersion
  try {
    const result = await api.post(`/operations/tasks/${id}/feedback`, form)
    if (version === requestVersion) { task.value = result; form.content = ''; form.completed_node_ids = []; draftText.value = '' }
    ElMessage.success('进展已反馈'); emit('saved'); announceChange()
  } catch {} finally { saving.value = false }
}
async function review(decision) {
  if(!reviewComment.value.trim())return ElMessage.warning('请填写审核意见或退回原因')
  reviewing.value=true
  const id=task.value.id, version=requestVersion
  try {const result=await api.post(`/operations/tasks/${id}/review`,{decision,comment:reviewComment.value,expected_revision:task.value.revision});
    if(version===requestVersion){task.value=result;reviewComment.value=''}
    ElMessage.success(decision==='approve'?'本级审核已通过':'任务已退回');emit('saved');announceChange()
  } catch {} finally {reviewing.value=false}
}
</script>
<style scoped>
h2 { font-family: var(--jc-font-serif); line-height: 1.5; margin: 16px 0 10px; } h3 { font-size: 15px; margin: 0 0 16px; }.plain { white-space: pre-wrap; line-height: 1.8; word-break: break-word; }
.section { border-top: 1px solid var(--jc-line); padding-top: 22px; margin-top: 24px; }.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.node-row { display: flex; justify-content: space-between; gap: 16px; padding: 10px 0; font-size: 13px; border-bottom: 1px solid var(--jc-line-2); }
.draft-preview { padding: 12px; background: var(--jc-bg); margin-top: 10px; border: 1px solid var(--jc-line); }
</style>
