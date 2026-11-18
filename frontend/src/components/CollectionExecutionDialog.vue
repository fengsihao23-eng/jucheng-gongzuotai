<template>
  <el-dialog
    v-model="visible"
    class="ops-task-dialog collection-exec-dialog"
    title="执行情况"
    width="min(1050px, calc(100vw - 40px))"
    align-center
    append-to-body
    destroy-on-close
    :close-on-click-modal="false"
  >
    <div v-loading="loading" class="task-page">
      <el-empty v-if="unavailable && !loading" description="无法读取该项目执行情况，可能原表版本已更新或无访问权限。">
        <el-button @click="load()">重试</el-button>
      </el-empty>

      <template v-if="item && !unavailable">
        <header class="task-header">
          <div class="task-identity">
            <div class="jc-row tags">
              <StatusPill :text="item.status_label" :tone="item.status_tone" dot />
              <span>{{ item.profile_label }} · 签订日期 {{ item.contract.date || '未注明' }}</span>
            </div>
            <h2>{{ item.contract.name }}</h2>
            <p class="context">{{ [item.contract.customer, item.contract.party].filter(Boolean).join(' · ') || '—' }}</p>
          </div>
          <div class="actions">
            <el-button plain @click="withXiaoju">用小聚办理</el-button>
            <el-button plain @click="goReport">项目报告</el-button>
            <el-button v-if="hasHistory" plain @click="historyOpen = true">过程记录</el-button>
          </div>
        </header>

        <dl class="facts">
          <div>
            <dt>负责人</dt>
            <dd>{{ item.contract.owner || '待确认' }}</dd>
          </div>
          <div>
            <dt>合同金额</dt>
            <dd>{{ item.contract.amount_note || (moneyText(item.contract.amount) + ' 元') }}</dd>
          </div>
          <div>
            <dt>未收金额</dt>
            <dd>{{ item.contract.outstanding != null ? moneyText(item.contract.outstanding) + ' 元' : '待核对' }}</dd>
            <small>{{ item.collection_state_name }}</small>
          </div>
          <div>
            <dt>预计回款安排</dt>
            <dd>{{ item.latest?.expected_on ? '预计 ' + item.latest.expected_on : (primaryPlan ? primaryPlan.period + ' 预估' : '待反馈日期') }}</dd>
            <small v-if="primaryPlan">{{ primaryPlan.title }}：{{ primaryPlan.plan_shared ? '合并预估' : (primaryPlan.plan_text || '—') }}</small>
          </div>
        </dl>

        <el-alert
          v-if="item.pending_request"
          :title="`${item.pending_request.author_name} 发起跟进要求（待 ${item.pending_request.recipient_name} 回复）：${item.pending_request.content}`"
          type="warning"
          :closable="false"
          show-icon
          class="notice"
        />
        <el-alert
          v-if="item.has_problem && item.latest?.problems"
          :title="`当前反映存在问题：${item.latest.problems}`"
          type="error"
          :closable="false"
          show-icon
          class="notice"
        />

        <div class="body-grid" :class="{ 'one-column': !item.can_feedback }">
          <div class="details">
            <section v-if="item.latest" class="section">
              <h3>最新执行情况 <small>{{ datetime(item.latest.created_at) }} · {{ item.latest.author_name }}</small></h3>
              <p class="plain"><b>当前阶段：</b>{{ item.latest.stage_name }}</p>
              <p class="plain mt-6"><b>跟进进展：</b>{{ item.latest.content }}</p>
              <p v-if="item.latest.problems" class="plain problem-highlight mt-6"><b>存在问题：</b>{{ item.latest.problems }}</p>
              <p v-if="item.latest.next_step" class="plain jc-muted mt-6"><b>下一步安排：</b>{{ item.latest.next_step }}</p>
            </section>

            <section v-if="item.plans?.length" class="section">
              <h3>原表预估回款计划</h3>
              <div v-for="plan in item.plans" :key="plan.period" class="plan-item">
                <b>{{ plan.period }} · {{ plan.title }}</b>
                <p class="plain">{{ plan.plan_shared ? '合并预估金额（按项目组计一次）' : '预估金额' }}：{{ plan.plan_text || '原表未填写' }}</p>
                <p v-if="plan.source_progress" class="plain jc-muted">原表进度备注：{{ plan.source_progress }}</p>
              </div>
            </section>

            <section v-if="item.notes?.length || item.attachments?.length" class="section">
              <h3>合同说明与附件</h3>
              <p v-for="(note, idx) in item.notes" :key="idx" class="plain">{{ note }}</p>
              <div v-if="item.attachments?.length" class="attachments mt-6">
                <a v-for="file in item.attachments" :key="file.url" :href="file.url" target="_blank" rel="noopener noreferrer">{{ file.filename }}</a>
              </div>
            </section>

            <section v-if="item.pending_request?.can_cancel" class="section">
              <h3>催办管理</h3>
              <p class="jc-small jc-muted mb-8">当前催办单尚未回复，发起人可按需撤回。</p>
              <el-button size="small" :loading="cancelling" @click="cancelPending">撤回待回复要求</el-button>
            </section>

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

          <div v-if="item.can_feedback" class="work">
            <section class="section feedback-box">
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
                  :label="`存在问题及需协调事项（${hasProblem ? '必填' : '可选'}）`"
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
            </section>
          </div>
        </div>
      </template>
    </div>

    <el-dialog
      v-model="historyOpen"
      title="过程记录"
      width="min(720px, calc(100vw - 24px))"
      align-center
      append-to-body
      destroy-on-close
      class="task-history-dialog"
    >
      <h3 v-if="item" class="history-count">过程记录 <small>{{ item.updates.length }} 条</small></h3>
      <el-timeline v-if="item?.updates?.length" class="history-timeline">
        <el-timeline-item
          v-for="u in item.updates"
          :key="u.id"
          :timestamp="`${datetime(u.created_at)} · ${u.author_name}`"
          placement="top"
        >
          <b>{{ u.title }}</b>
          <p class="plain">{{ u.content }}</p>
          <p v-if="u.problems" class="plain problem-highlight jc-small">存在问题：{{ u.problems }}</p>
          <p v-if="u.next_step" class="plain jc-small jc-muted">下一步：{{ u.next_step }}</p>
          <p v-if="u.expected_on" class="plain jc-small jc-muted">预计回款日：{{ u.expected_on }}</p>
        </el-timeline-item>
      </el-timeline>
      <p v-else class="jc-muted">暂无过程记录</p>

      <details v-if="item?.notification_deliveries?.length" class="section delivery-history">
        <summary>企业微信提醒投递情况</summary>
        <p class="jc-small jc-muted">企微已受理表示发送请求成功。请在企业微信「消息」里的「聚成工作台」会话查看。</p>
        <div v-for="n in item.notification_deliveries" :key="n.id" class="node-entry">
          <b>{{ n.recipient }} · {{ n.status }}</b>
          <p class="jc-small">{{ n.title }} · {{ n.sent_at ? datetime(n.sent_at) : '待发送/已停止' }}</p>
          <p v-if="n.error" class="jc-small jc-muted">{{ n.error }}</p>
        </div>
      </details>
    </el-dialog>

    <template v-if="!unavailable && item" #footer>
      <div class="task-footer">
        <el-button @click="visible = false">关闭</el-button>
        <el-button
          v-if="item.can_feedback"
          type="primary"
          :loading="saving"
          :disabled="drafting"
          @click="submit"
        >提交执行情况</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { datetime } from '@/utils/format'

const props = defineProps({
  modelValue: Boolean,
  sheet: { type: String, default: '' },
  row: { type: Number, default: 0 },
  revision: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const router = useRouter()
const route = useRoute()
const assistant = useAssistant()

const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const item = ref(null)
const loading = ref(false)
const unavailable = ref(false)
const saving = ref(false)
const drafting = ref(false)
const draftText = ref('')
const requesting = ref(false)
const cancelling = ref(false)
const historyOpen = ref(false)
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

const hasHistory = computed(() => !!(item.value?.updates?.length || item.value?.notification_deliveries?.length))
const primaryPlan = computed(() => item.value?.plans?.[0] || null)
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
  Object.assign(reqForm, {
    recipient_id: data.suggested_recipient_id || null,
    content: '',
  })
  draftText.value = ''
  formRef.value?.clearValidate()
}

async function load(quiet = false) {
  if (!visible.value || !props.sheet || !props.row) return
  const ver = ++reqVersion
  if (!quiet) loading.value = true
  try {
    const res = await api.get(
      `/business-workbook/collections/items/${encodeURIComponent(props.sheet)}/${props.row}/execution`,
      { revision: props.revision || '' },
      { silent: true },
    )
    if (ver !== reqVersion || !visible.value) return
    item.value = res
    unavailable.value = false
    if (!quiet) resetForm(res)
  } catch {
    if (ver === reqVersion) {
      item.value = null
      unavailable.value = true
    }
  } finally {
    if (ver === reqVersion) loading.value = false
  }
}

watch(() => [visible.value, props.sheet, props.row], () => {
  reqVersion++
  historyOpen.value = false
  item.value = null
  unavailable.value = false
  draftText.value = ''
  if (visible.value && props.sheet && props.row) load()
}, { immediate: true })

function goReport() {
  if (!item.value) return
  visible.value = false
  router.push({
    path: item.value.report_path,
    query: { revision: item.value.revision, back: route.fullPath },
  })
}

function withXiaoju() {
  if (!item.value) return
  const refName = `「${item.value.contract.name}」（负责人：${item.value.contract.owner || '待确认'}）`
  visible.value = false
  assistant.openDraft(`帮我查看并梳理回款项目${refName}的执行情况、存在问题及下一步催收建议。`)
}

async function polishDraft() {
  if (!item.value || !form.content.trim() || drafting.value) return
  drafting.value = true
  const original = form.content
  try {
    const res = await api.post(
      `/business-workbook/collections/items/${encodeURIComponent(props.sheet)}/${props.row}/draft`,
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
  if (hasProblem.value && !form.problems.trim()) {
    return ElMessage.warning('勾选“遇到阻碍 / 存在问题”时，请填写具体存在的问题')
  }
  saving.value = true
  try {
    const res = await api.post(
      `/business-workbook/collections/items/${encodeURIComponent(props.sheet)}/${props.row}/execution`,
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
    )
    item.value = res
    resetForm(res)
    ElMessage.success('执行情况已保存')
    emit('saved')
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
      `/business-workbook/collections/items/${encodeURIComponent(props.sheet)}/${props.row}/followups`,
      {
        revision: item.value.revision,
        recipient_id: reqForm.recipient_id,
        content: reqForm.content.trim(),
      },
    )
    ElMessage.success('已发送跟进提醒通知')
    reqForm.content = ''
    await load(true)
    emit('saved')
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
    await load(true)
    emit('saved')
  } catch {} finally {
    cancelling.value = false
  }
}
</script>

<style scoped>
:global(.collection-exec-dialog.el-dialog) { display: flex; flex-direction: column; padding: 0; overflow: hidden; border-radius: 13px; }
:global(.collection-exec-dialog > .el-dialog__header) { padding: 15px 23px 13px; border-bottom: 1px solid var(--jc-line); margin: 0; }
:global(.collection-exec-dialog > .el-dialog__header .el-dialog__title) { font-size: 14px; font-weight: 600; }
:global(.collection-exec-dialog > .el-dialog__header .el-dialog__headerbtn) { top: 1px; right: 4px; }
:global(.collection-exec-dialog > .el-dialog__body) { flex: 1; min-height: 0; max-height: min(76vh, calc(100dvh - 124px)); overflow: auto; overscroll-behavior: contain; padding: 0; }
:global(.collection-exec-dialog > .el-dialog__footer) { border-top: 1px solid var(--jc-line); padding: 9px 23px; }
:global(.task-history-dialog > .el-dialog__body) { max-height: 68dvh; overflow: auto; overscroll-behavior: contain; }
.mt-6 { margin-top: 6px; }
.mb-8 { margin-bottom: 8px; }
.w-full { width: 100%; }
.task-footer { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.task-footer .el-button + .el-button { margin-left: 0; }
.task-page { min-height: 140px; padding: 20px 23px 24px; }
.task-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; flex-wrap: wrap; }
.task-identity { flex: 1 1 220px; min-width: 0; }
.tags { flex-wrap: wrap; gap: 8px; color: var(--jc-ink-3); font-size: 12px; }
h2 { font: 600 clamp(20px, 2.8vw, 26px)/1.35 var(--jc-font-serif); color: var(--jc-ink); margin: 8px 0 4px; overflow-wrap: anywhere; }
.context { color: var(--jc-ink-3); margin: 0; font-size: 13px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.actions .el-button + .el-button { margin: 0; }
.facts { display: grid; grid-template-columns: 1fr 1fr 1fr 1.3fr; gap: 10px; margin: 17px 0; }
.facts > div { min-width: 0; padding: 9px 11px; background: var(--jc-bg-2, #f9f8f5); border: 1px solid var(--jc-line); border-radius: 7px; }
.facts dt { font-size: 12px; color: var(--jc-ink-3); }
.facts dd { margin: 4px 0 0; color: var(--jc-ink); font-weight: 600; overflow-wrap: anywhere; }
.facts small { display: block; color: var(--jc-ink-3); margin-top: 3px; }
.notice { margin-bottom: 12px; }
.body-grid { display: grid; grid-template-columns: minmax(0, .95fr) minmax(0, 1fr); gap: 20px; align-items: start; }
.body-grid.one-column { grid-template-columns: minmax(0, 1fr); }
.details, .work { min-width: 0; }
.section { border: 1px solid var(--jc-line); border-radius: 8px; padding: 14px; }
.section + .section { margin-top: 10px; }
.section h3 { font-size: 15px; margin: 0 0 10px; color: var(--jc-ink); }
.section h3 small { font-size: 11px; margin-left: 6px; font-weight: 400; color: var(--jc-ink-3); }
.section p { margin: 0; }
.plain { white-space: pre-wrap; line-height: 1.7; overflow-wrap: anywhere; }
.problem-highlight { color: var(--jc-down, #c23b2b); }
.plan-item + .plan-item { border-top: 1px solid var(--jc-line-2); padding-top: 8px; margin-top: 8px; }
.attachments a { display: block; font-size: 13px; margin-top: 4px; }
.request-collapse summary { cursor: pointer; font-weight: 600; font-size: 14px; color: var(--jc-ink); }
.request-body { margin-top: 10px; }
.feedback-box :deep(.el-form-item) { margin-bottom: 13px; }
.feedback-box :deep(.el-checkbox) { height: auto; min-height: 25px; white-space: normal; }
.draft-preview { padding: 9px; background: var(--jc-bg); border: 1px solid var(--jc-line); border-radius: 6px; }
.result-choice :deep(.el-radio-group) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
.result-choice :deep(.el-radio-button__inner) { width: 100%; }
.history-count { font-size: 15px; margin: 0 0 14px; color: var(--jc-ink); }
.history-count small { font-size: 11px; margin-left: 6px; font-weight: 400; color: var(--jc-ink-3); }
.history-timeline { padding: 0 4px 0 2px; }
.history-timeline :deep(.el-timeline-item:last-child) { padding-bottom: 0; }
.delivery-history { margin-top: 14px; }
.delivery-history summary { cursor: pointer; }
.delivery-history > p { margin-top: 8px; }
.node-entry { border-top: 1px solid var(--jc-line); padding-top: 10px; margin-top: 10px; }
@media (max-width: 740px) {
  .body-grid { grid-template-columns: minmax(0, 1fr); gap: 12px; }
  .work { grid-row: 1; }
  .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  :global(.collection-exec-dialog.el-dialog) { width: 100vw !important; height: 100dvh; border-radius: 0; }
  :global(.collection-exec-dialog > .el-dialog__body) { max-height: none; }
  .task-page { padding: 14px; }
  .section { padding: 12px; }
}
</style>
