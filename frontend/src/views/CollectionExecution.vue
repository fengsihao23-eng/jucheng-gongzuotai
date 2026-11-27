<template>
  <div class="jc-page collection-execution" v-loading="loading && !item">
    <PageHeader eyebrow="回款情况 · 执行情况" :title="item?.contract.name || '回款执行情况'"
      :desc="item ? [item.contract.customer, item.contract.party].filter(Boolean).join(' · ') : ''">
      <template v-if="item" #meta>
        <div class="jc-row wrap mt-6">
          <StatusPill :text="item.status_label" :tone="item.status_tone" dot />
          <span class="jc-muted jc-small">{{ item.profile_label }} · 签订日期 {{ item.contract.date || '未注明' }}</span>
        </div>
      </template>
      <el-button @click="returnToList(router, backTarget)">返回回款情况</el-button>
      <el-button v-if="item" @click="goReport">项目报告</el-button>
      <el-button v-if="item" @click="withXiaoju">用小聚办理</el-button>
      <el-button v-if="item" type="primary" plain aria-haspopup="dialog" @click="openFeedback">互动反馈</el-button>
    </PageHeader>
    <el-empty v-if="unavailable && !loading" description="无法读取该项目执行情况，请刷新后重试。">
      <el-button @click="load()">重试</el-button>
    </el-empty>
    <template v-if="item">
      <div class="jc-grid c3">
        <Kpi label="负责人" :value="item.contract.owner || '待确认'" />
        <Kpi label="合同金额" :value="item.contract.amount_note || moneyText(item.contract.amount)" :unit="item.contract.amount_note ? '' : '元'" />
        <Kpi label="未收金额" :value="moneyText(item.contract.outstanding)" unit="元" :hint="item.collection_state_name" />
      </div>
      <Panel title="回款安排" class="mt-16">
        <p v-if="item.pending_request" class="plain problem-highlight">待 {{ item.pending_request.recipient_name }} 回复 · {{ item.pending_request.content }}</p>
        <template v-if="item.latest">
          <p class="plain"><b>当前阶段：</b>{{ item.latest.stage_name }}</p>
          <p class="plain mt-6"><b>跟进进展：</b>{{ item.latest.content }}</p>
          <p v-if="item.latest.problems" class="plain problem-highlight mt-6"><b>存在问题：</b>{{ item.latest.problems }}</p>
          <p v-if="item.latest.next_step" class="plain jc-muted mt-6"><b>下一步安排：</b>{{ item.latest.next_step }}</p>
        </template>
        <p v-if="item.latest?.expected_on || item.plans?.length || (!item.latest && !item.pending_request)" class="plain mt-6">
          预计回款日 {{ item.latest?.expected_on || item.plans?.[0]?.period || '待确认' }}
        </p>
      </Panel>
      <div class="jc-stack mt-16">
        <Panel v-if="item.plans?.length" title="原表预估回款计划">
          <div v-for="plan in item.plans" :key="plan.period" class="plan-item">
            <b>{{ plan.period }} · {{ plan.title }}</b>
            <p class="plain">{{ plan.plan_shared ? '合并预估金额（按项目组计一次）' : '预估金额' }}：{{ plan.plan_text || '原表未填写' }}</p>
            <p v-if="plan.source_progress" class="plain jc-muted">原表进度备注：{{ plan.source_progress }}</p>
          </div>
        </Panel>
        <Panel v-if="item.notes?.length || item.attachments?.length" title="合同说明与附件">
          <p v-for="(note, idx) in item.notes" :key="idx" class="plain">{{ note }}</p>
          <div v-if="item.attachments?.length" class="attachments mt-6">
            <a v-for="file in item.attachments" :key="file.url" :href="file.url" target="_blank" rel="noopener noreferrer">{{ file.filename }}</a>
          </div>
        </Panel>
        <Panel v-if="item.pending_request?.can_cancel" title="催办管理">
          <p class="jc-small jc-muted mb-8">当前催办单尚未回复，发起人可按需撤回。</p>
          <el-button size="small" :loading="cancelling" @click="cancelPending">撤回待回复要求</el-button>
        </Panel>
        <CollectionReminderPanel :data="item.reminder" :sheet="sheet" :row="row" :revision="item.revision"
          :suggested-recipient="item.suggested_recipient_id" :pending-recipient="item.pending_request?.recipient_id" @saved="refreshExecution" />
        <details v-if="item.can_request" class="section request-collapse">
          <summary>定向指派 / 发送企业微信跟进提醒</summary>
          <div class="request-body">
            <el-select v-model="reqForm.recipient_id" filterable placeholder="选择在职公司账号" aria-label="指定回复人" class="w-full mb-8">
              <el-option v-for="u in item.choices" :key="u.id" :value="u.id" :label="[u.name, u.department].filter(Boolean).join(' · ')" />
            </el-select>
            <el-input v-model="reqForm.content" type="textarea" :rows="2" maxlength="1600" placeholder="填写具体跟进要求，发送企微通知提醒负责人回复" class="mb-8" />
            <el-button size="small" type="primary" plain :loading="requesting" :disabled="!reqForm.recipient_id || !reqForm.content.trim()" @click="sendRequest">发送企微提醒</el-button>
          </div>
        </details>
      </div>
    </template>
    <RecordDetailDialog v-model="visible" dialog-title="互动反馈" :heading="item?.contract.name"
      :subtitle="[item?.contract.customer, item?.contract.party].filter(Boolean).join(' · ')"
      :status="item?.status_label" :status-tone="item?.status_tone || 'brand'"
      :target="item ? { kind: 'workbook-collections', id: row, sheet, revision: item.revision } : null"
      :facts="item ? [
        { label: '负责人', value: item.contract.owner || '待确认' },
        { label: '合同金额', value: item.contract.amount_note || (moneyText(item.contract.amount) + ' 元') },
        { label: '待回款金额', value: moneyText(item.contract.outstanding) + ' 元' },
      ] : []" :history-entries="nativeHistory" :close-locked="saving" progress-hint="说明客户沟通、付款审批和预计到账情况。">
      <template v-if="item?.can_feedback && !unavailable" #progress>
        <h3>执行情况反馈</h3>
        <el-form ref="formRef" :model="form" label-position="top">
          <el-form-item label="完成情况" class="result-choice">
            <el-radio-group v-model="outcome" aria-label="完成情况">
              <el-radio-button value="pending">跟进推进中</el-radio-button>
              <el-radio-button value="done">已反馈回款</el-radio-button>
            </el-radio-group>
          </el-form-item>

          <el-form-item v-if="outcome === 'pending'" label="跟进阶段">
            <el-select v-model="form.stage" aria-label="跟进阶段" class="w-full" @change="onStageChange">
              <el-option value="contact" label="联系沟通" />
              <el-option value="approval" label="付款审批" />
              <el-option value="awaiting" label="等待付款" />
              <el-option value="blocked" label="需协调（遇到阻碍）" />
            </el-select>
          </el-form-item>

          <el-form-item
            :label="outcome === 'done' ? '回款情况说明' : '目前跟进情况'"
            prop="content"
            :rules="[{ required: true, whitespace: true, message: '请填写目前跟进情况或回款结果' }]"
          >
            <el-input
              v-model="form.content"
              type="textarea"
              :rows="3"
              maxlength="2000"
              :validate-event="false"
              placeholder="请填写当前与客户沟通进展、审批状态或回款情况…"
              @input="formRef?.clearValidate('content')"
            />
          </el-form-item>

          <el-button link type="primary" :loading="drafting" :disabled="!form.content.trim() || saving" @click="polishDraft">让 AI 整理文字</el-button>
          <div v-if="draftText" class="draft-preview mt-6 mb-8">
            <p class="plain">{{ draftText }}</p>
            <div class="mt-6">
              <el-button size="small" @click="form.content = draftText; draftText = ''">采用这段文字</el-button>
              <el-button size="small" text @click="draftText = ''">保留原文</el-button>
            </div>
          </div>

          <el-form-item v-if="outcome === 'pending'" label="当前情况" class="mt-6">
            <el-checkbox v-model="hasProblem">遇到阻碍 / 存在问题（需协调）</el-checkbox>
          </el-form-item>

          <el-form-item
            :label="needsReason ? '到期未收原因（必填）' : `存在问题及需协调事项（${hasProblem ? '必填' : '可选'}）`"
            prop="problems"
          >
            <el-input
              v-model="form.problems"
              type="textarea"
              :rows="2"
              maxlength="2000"
              placeholder="如存在验收资料缺失、甲方审批卡点、争议扣款或需公司协调事项，请在此详细说明"
            />
          </el-form-item>

          <el-form-item label="下一步安排（可选）">
            <el-input v-model="form.next_step" maxlength="2000" placeholder="下一步推进计划、责任人及时间节点" />
          </el-form-item>

          <el-form-item label="预计回款日期（可选）">
            <el-date-picker
              v-model="form.expected_on"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择预计到账日期"
              class="w-full"
            />
          </el-form-item>
        </el-form>
      </template>
      <template v-if="item?.notification_deliveries?.length" #history>
        <details class="section delivery-history">
          <summary>企业微信提醒投递情况</summary>
          <p class="jc-small jc-muted">企微已受理表示发送请求成功。请在企业微信「消息」里的「聚成工作台」会话查看。</p>
          <div v-for="n in item.notification_deliveries" :key="n.id" class="node-entry">
            <b>{{ n.recipient }} · {{ n.status }}</b>
            <p class="jc-small">{{ n.title }} · {{ n.sent_at ? datetime(n.sent_at) : '待发送/已停止' }}</p>
            <p v-if="n.error" class="jc-small jc-muted">{{ n.error }}</p>
          </div>
        </details>
      </template>
      <template v-if="item?.can_feedback && !unavailable" #attachments>
        <FeedbackAttachmentPicker v-model="attachments" :disabled="saving" />
      </template>
      <template v-if="item?.can_feedback && !unavailable" #footer>
        <el-button type="primary" :loading="saving" :disabled="drafting" @click="submit">提交执行情况</el-button>
      </template>
    </RecordDetailDialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { submitFeedback } from '@/api/feedback'
import { useAssistant } from '@/store/assistant'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import { detailBackTarget, returnToList } from '@/utils/detail-navigation'
import { datetime } from '@/utils/format'
import CollectionReminderPanel from '@/components/CollectionReminderPanel.vue'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import FeedbackAttachmentPicker from '@/components/records/FeedbackAttachmentPicker.vue'

const router = useRouter()
const route = useRoute()
const assistant = useAssistant()

const sheet = computed(() => route.params.sheet)
const row = computed(() => Number(route.params.row))
const revision = computed(() => typeof route.query.revision === 'string' ? route.query.revision : '')
const endpoint = computed(() => `/business-workbook/collections/items/${encodeURIComponent(sheet.value)}/${row.value}`)
const backTarget = computed(() => detailBackTarget(route.query.back, '/collections'))
const visible = ref(false)
const item = ref(null)
const nativeHistory = computed(() => (item.value?.updates || []).map(update => ({
  ...update, key: `collection-${update.id}`, author: update.author_name, next_due_on: update.expected_on,
})))
const loading = ref(false)
const unavailable = ref(false)
const saving = ref(false)
const attachments = ref([])
const drafting = ref(false)
const draftText = ref('')
const requesting = ref(false)
const cancelling = ref(false)
const formRef = ref()

const form = reactive({
  stage: 'contact',
  content: '',
  problems: '',
  next_step: '',
  expected_on: null,
})
const reqForm = reactive({
  recipient_id: null,
  content: '',
})

let lastNonDoneStage = 'contact'
const outcome = computed({
  get: () => form.stage === 'reported_paid' ? 'done' : 'pending',
  set: val => {
    if (val === 'done' && form.stage !== 'reported_paid') {
      lastNonDoneStage = form.stage
      form.stage = 'reported_paid'
    } else if (val === 'pending' && form.stage === 'reported_paid') {
      form.stage = lastNonDoneStage || 'contact'
    }
  },
})

const hasProblem = computed({
  get: () => form.stage === 'blocked',
  set: val => {
    if (val) form.stage = 'blocked'
    else if (form.stage === 'blocked') form.stage = 'contact'
  },
})

function onStageChange(val) {
  if (val !== 'reported_paid') lastNonDoneStage = val
}

const needsReason = computed(() => item.value?.reminder?.payment_due && outcome.value !== 'done')
const moneyText = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })

let reqVersion = 0

function resetForm(data) {
  const latest = data.latest
  const defaultStage = latest?.stage && latest.stage !== 'reported_paid' ? latest.stage : 'contact'
  lastNonDoneStage = defaultStage === 'reported_paid' ? 'contact' : defaultStage
  Object.assign(form, {
    stage: defaultStage,
    content: '',
    problems: latest?.problems || '',
    next_step: latest?.next_step || '',
    expected_on: latest?.expected_on || null,
  })
  draftText.value = ''
  attachments.value = []
  formRef.value?.clearValidate()
}

async function load(quiet = false) {
  if (!sheet.value || !row.value) { unavailable.value = true; return }
  const ver = ++reqVersion
  if (!quiet) loading.value = true
  try {
    const res = await api.get(
      endpoint.value + '/execution',
      { revision: revision.value },
      { silent: true },
    )
    if (ver !== reqVersion) return
    item.value = res
    unavailable.value = false
    if (!quiet) reqForm.recipient_id = res.suggested_recipient_id || null
  } catch {
    if (ver === reqVersion) {
      item.value = null
      unavailable.value = true
      visible.value = false
    }
  } finally {
    if (ver === reqVersion) loading.value = false
  }
}

watch([sheet, row, revision], () => {
  reqVersion++
  item.value = null
  unavailable.value = false
  visible.value = false
  reqForm.content = ''
  draftText.value = ''
  attachments.value = []
  load()
}, { immediate: true })
onBeforeUnmount(() => { reqVersion++ })

function openFeedback() {
  if (!item.value) return
  resetForm(item.value)
  visible.value = true
}

async function refreshExecution() {
  await load(true)
  announceBusinessChange()
}

function goReport() {
  if (!item.value) return
  router.push({
    path: item.value.report_path,
    query: { revision: item.value.revision, back: backTarget.value },
  })
}

function withXiaoju() {
  if (!item.value) return
  const refName = `「${item.value.contract.name}」`
  assistant.open(`请汇报${refName}的回款进展`)
}

async function polishDraft() {
  if (!item.value || !form.content.trim() || drafting.value) return
  drafting.value = true
  const original = form.content
  try {
    const res = await api.post(
      endpoint.value + '/draft',
      { text: original },
      { timeout: 30000 },
    )
    if (!visible.value || original !== form.content) return
    if (res.assisted) draftText.value = res.text
    else ElMessage.info('内容已较为规范，可直接提交或微调')
  } catch {} finally {
    drafting.value = false
  }
}

async function submit() {
  if (!item.value || saving.value) return
  if (!await formRef.value?.validate().catch(() => false)) return
  if (needsReason.value && !form.problems.trim()) return ElMessage.warning('回款已到期，请填写未收原因')
  if (hasProblem.value && !form.problems.trim()) {
    return ElMessage.warning('勾选“遇到阻碍 / 存在问题”时，请填写具体存在的问题')
  }
  saving.value = true
  try {
    const res = await submitFeedback(
      endpoint.value + '/execution',
      {
        revision: item.value.revision,
        stage: form.stage,
        content: form.content.trim(),
        has_problem: hasProblem.value || Boolean(form.problems.trim() && form.stage === 'blocked'),
        problems: form.problems.trim() || null,
        next_step: form.next_step.trim() || null,
        expected_on: form.expected_on || null,
        expected_progress_id: item.value.latest_progress_id,
      },
      attachments.value,
    )
    item.value = res
    resetForm(res)
    ElMessage.success('执行情况已保存')
    announceBusinessChange()
  } catch {
    load(true)
  } finally {
    saving.value = false
  }
}

async function sendRequest() {
  if (!item.value || requesting.value || !reqForm.recipient_id || !reqForm.content.trim()) return
  requesting.value = true
  try {
    await api.post(
      endpoint.value + '/followups',
      {
        revision: item.value.revision,
        recipient_id: reqForm.recipient_id,
        content: reqForm.content.trim(),
      },
    )
    ElMessage.success('已发送跟进提醒通知')
    reqForm.content = ''
    await refreshExecution()
  } catch {} finally {
    requesting.value = false
  }
}

async function cancelPending() {
  if (!item.value?.pending_request || cancelling.value) return
  cancelling.value = true
  try {
    await api.post(`/business-workbook/collection-followups/${item.value.pending_request.id}/cancel`)
    ElMessage.success('已撤回跟进要求')
    await refreshExecution()
  } catch {} finally {
    cancelling.value = false
  }
}
</script>

<style scoped>
.mt-6 { margin-top: 6px; }
.mb-8 { margin-bottom: 8px; }
.w-full { width: 100%; }
.section { border: 1px solid var(--jc-line); border-radius: 8px; padding: 14px; }
.collection-execution p.plain { margin-bottom: 0; }
.plain { white-space: pre-wrap; line-height: 1.7; overflow-wrap: anywhere; }
.problem-highlight { color: var(--jc-down, #c23b2b); }
.plan-item + .plan-item { border-top: 1px solid var(--jc-line-2); padding-top: 8px; margin-top: 8px; }
.attachments a { display: block; font-size: 13px; margin-top: 4px; }
.request-collapse summary { cursor: pointer; font-weight: 600; font-size: 14px; color: var(--jc-ink); }
.request-body { margin-top: 10px; }
.draft-preview { padding: 9px; background: var(--jc-bg); border: 1px solid var(--jc-line); border-radius: 6px; }
.result-choice :deep(.el-radio-group) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
.result-choice :deep(.el-radio-button__inner) { width: 100%; }
.delivery-history { margin-top: 14px; }
.delivery-history summary { cursor: pointer; }
.delivery-history > p { margin-top: 8px; }
.node-entry { border-top: 1px solid var(--jc-line); padding-top: 10px; margin-top: 10px; }
</style>
