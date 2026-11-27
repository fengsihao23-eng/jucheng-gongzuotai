<template>
  <section class="school-followup" aria-label="跟进反馈">
    <div class="jc-row between">
      <h4>跟进反馈</h4>
      <el-button v-if="project.can_record_visits" type="primary" @click="start">填写跟进反馈</el-button>
    </div>
    <el-dialog v-if="project.can_record_visits" v-model="open" title="跟进反馈" class="ops-task-dialog"
      width="min(680px, calc(100vw - 40px))" align-center append-to-body destroy-on-close
      :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
      <div class="visit-form">
        <h3>{{ school.name }}</h3>
        <TaskFeedbackForm ref="formRef" v-model:drafting="drafting" :form="form" :saving="saving"
          :draft-endpoint="endpoint + '/draft'">
          <template #before>
            <el-form-item label="实际跟进日期" prop="occurred_on" :rules="[{ required: true, message: '选择实际跟进日期' }]">
              <el-date-picker v-model="form.occurred_on" type="date" value-format="YYYY-MM-DD" aria-label="实际跟进日期" />
            </el-form-item>
          </template>
          <el-form-item label="下次跟进日期（可选）">
            <el-date-picker v-model="form.next_contact_date" type="date" value-format="YYYY-MM-DD" aria-label="下次跟进日期" />
          </el-form-item>
          <el-form-item v-if="canTransition && form.status === 'done'" label="转入目标客户（可选）">
            <el-select v-model="form.target_stage" aria-label="转入目标客户" placeholder="保持当前阶段" clearable>
              <el-option label="已签约" value="won" />
              <el-option label="运营中" value="operating" />
            </el-select>
          </el-form-item>
        </TaskFeedbackForm>
      </div>
      <template #footer>
        <div class="visit-actions">
          <FeedbackAttachmentPicker v-model="attachments" :disabled="saving" />
          <el-button :disabled="saving" @click="open = false">关闭</el-button>
          <el-button type="primary" :loading="saving" :disabled="drafting" @click="save">提交进展</el-button>
        </div>
      </template>
    </el-dialog>
    <el-timeline v-if="items.length" class="visit-timeline">
      <el-timeline-item v-for="visit in items" :key="visit.id" v-record-delete="visitRemoval(visit)"
        :timestamp="`${visit.occurred_on} · ${visit.author}`" placement="top">
        <StatusPill v-if="visit.status" :text="TASK_STATUS[visit.status]" :tone="tone(visit.status)" />
        <p class="plain">{{ visit.content }}</p>
        <p v-if="visit.incomplete_reason" class="plain"><b>未完成原因</b> {{ visit.incomplete_reason }}</p>
        <p v-if="visit.next_action" class="plain"><b>下一步</b> {{ visit.next_action }}</p>
        <p v-if="visit.next_contact_date" class="jc-small jc-muted">下次跟进 {{ visit.next_contact_date }}</p>
        <FeedbackAttachmentList :files="visit.attachments" />
        <small class="jc-muted">录入时间 {{ datetime(visit.created_at) }}</small>
      </el-timeline-item>
    </el-timeline>
    <p v-else class="jc-small jc-muted">暂无跟进反馈。</p>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { submitFeedback } from '@/api/feedback'
import FeedbackAttachmentList from '@/components/records/FeedbackAttachmentList.vue'
import FeedbackAttachmentPicker from '@/components/records/FeedbackAttachmentPicker.vue'
import { useAuth } from '@/store/auth'
import { recordRemoval } from '@/composables/useRecordDeletion'
import { datetime } from '@/utils/format'
import TaskFeedbackForm from './TaskFeedbackForm.vue'
import { TASK_STATUS, announceChange, tone } from './shared'

const props = defineProps({ project: Object, school: Object })
const emit = defineEmits(['saved'])
const auth = useAuth()
const items = ref([]), open = ref(false), saving = ref(false), drafting = ref(false), formRef = ref()
const form = reactive({}), stageAtOpen = ref(null)
const attachments = ref([])
const endpoint = computed(() => `/operations/${props.project.id}/schools/${props.school.id}/visits`)
const canTransition = computed(() => props.project.can_manage && !['won', 'operating'].includes(stageAtOpen.value))
let request = 0, submission
watch(attachments, () => { submission = null })

const visitRemoval = row => (props.project.can_manage || auth.hasDeleteGrant) &&
  recordRemoval('operation-visit', row.id, row.occurred_on, async () => { await load(); emit('saved') },
    { title: '删除学校跟进记录' })

function start() {
  Object.assign(form, {
    occurred_on: props.project.today, status: 'doing', content: '', next_step: '', incomplete_reason: '',
    next_contact_date: null, target_stage: null,
  })
  stageAtOpen.value = props.school.stage
  attachments.value = []
  submission = null
  open.value = true
}

async function load() {
  const version = ++request
  try {
    const { items: rows } = await api.get(endpoint.value)
    if (version === request) items.value = rows
  } catch { /* API client displays the read error. */ }
}
watch(endpoint, () => { open.value = false; items.value = []; load() }, { immediate: true })

async function save() {
  if (saving.value || drafting.value || !formRef.value) return
  saving.value = true
  const url = endpoint.value
  try {
    if (!await formRef.value.validate().catch(() => false)) return
    if (form.status === 'blocked' && !form.incomplete_reason.trim()) return ElMessage.warning('请填写未完成原因')
    const transition = canTransition.value && form.status === 'done' && form.target_stage
      ? { stage: form.target_stage, expected_stage: stageAtOpen.value } : null
    const payload = {
      occurred_on: form.occurred_on, status: form.status, content: form.content,
      incomplete_reason: form.status === 'done' ? '' : form.incomplete_reason,
      next_action: form.next_step, next_contact_date: form.next_contact_date, transition,
    }
    const key = JSON.stringify(payload)
    if (submission?.key !== key) submission = { key, id: crypto.randomUUID() }
    await submitFeedback(url, { ...payload, client_id: submission.id }, attachments.value)
    announceChange()
    if (url !== endpoint.value) return
    attachments.value = []
    open.value = false
    await load()
    emit('saved')
    ElMessage.success(transition ? '进展已反馈，已转入目标客户' : '进展已反馈')
  } catch { /* Keep the draft for correction or retry. */ }
  finally { saving.value = false }
}
</script>

<style scoped>
.school-followup .between { flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.school-followup h4 { margin: 0; }
.visit-form { padding: 16px clamp(14px, 4vw, 23px) 22px; }
.visit-form h3 { margin: 0 0 12px; font: 600 22px/1.4 var(--jc-font-serif); overflow-wrap: anywhere; }
.visit-form :deep(.el-select) { width: 100%; }
.visit-timeline { padding: 4px 0 0; }
.visit-timeline p { margin: 6px 0; }
.visit-actions { display: flex; justify-content: flex-end; align-items: center; flex-wrap: wrap; gap: 8px; }
.visit-actions .el-button + .el-button { margin-left: 0; }
.plain { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
