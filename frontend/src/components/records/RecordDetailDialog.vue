<template>
  <el-dialog v-model="visible" :title="dialogTitle || data?.record.label || title || '记录详情'"
    class="record-detail-dialog" width="min(1050px, calc(100vw - 40px))"
    align-center append-to-body destroy-on-close :close-on-click-modal="false"
    :close-on-press-escape="!closeLocked" :show-close="!closeLocked" @close="emit('close')" @closed="emit('closed')">
    <div class="record-page">
      <template v-if="recordTarget">
        <header class="record-head">
          <div class="record-identity">
            <div class="record-tags">
              <StatusPill v-if="status || latest" :text="status || progressLabels[latest.status]"
                :tone="status ? statusTone : progressTones[latest.status]" dot />
              <span>{{ data?.record.module }}</span>
            </div>
            <h2>{{ heading || data?.record.title || title }}</h2>
            <p v-if="subtitle" class="record-subtitle">{{ subtitle }}</p>
          </div>
          <div class="record-actions">
            <slot name="actions" />
            <el-button v-if="data" plain @click="historyOpen = true">过程记录</el-button>
          </div>
        </header>
        <dl v-if="summary.length" class="record-facts">
          <div v-for="fact in summary" :key="fact.label">
            <dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd><small v-if="fact.hint">{{ fact.hint }}</small>
          </div>
        </dl>
        <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon class="record-error">
          <el-button link type="primary" @click="load()">重试</el-button>
        </el-alert>
        <div class="record-grid">
          <div class="record-stack">
            <section v-if="($slots.default && !fullWidthContent) || data?.record.context || (data?.record.path && linked)" class="record-section">
              <slot v-if="!fullWidthContent && $slots.default" />
              <template v-else-if="data?.record.context"><h3>{{ contextTitle || '业务内容' }}</h3><p class="record-text">{{ data.record.context }}</p></template>
              <el-button v-if="linked && data?.record.path" link type="primary" @click="router.push(data.record.path)">打开业务页面</el-button>
            </section>
            <section v-if="data" class="record-section" aria-label="要求与留言">
              <h3>要求与留言</h3>
              <p class="jc-small jc-muted">留言和回复可在「过程记录」查看。</p>
              <article v-for="item in data.my_requests" :key="item.id" class="record-request">
                <div class="jc-row between"><b>{{ item.author }}提出要求</b><StatusPill text="待回复" tone="warn" /></div>
                <p class="record-text">{{ item.content }}</p>
                <el-button link type="primary" @click="beginReply(item)">回复这条要求</el-button>
              </article>
              <el-form label-position="top" class="record-discussion" @submit.prevent="sendDiscussion">
                <div v-if="replyTo" class="record-request"><b>回复 {{ replyTo.author }}</b><p class="record-text">{{ replyTo.content }}</p><el-button link @click="replyTo = null">取消回复</el-button></div>
                <template v-else>
                  <el-form-item label="讨论类型">
                    <el-radio-group v-model="discussion.kind" aria-label="讨论类型">
                      <el-radio-button value="note">留言</el-radio-button>
                      <el-radio-button v-if="data.can_request" value="request">提出要求</el-radio-button>
                    </el-radio-group>
                  </el-form-item>
                  <el-form-item :label="discussion.kind === 'request' ? '指定回复人' : '提醒成员（可选）'">
                    <el-select v-model="discussion.recipient" filterable remote clearable reserve-keyword
                      class="record-recipient" :remote-method="loadRecipients" :loading="finding" aria-label="讨论接收人"
                      placeholder="请选择" @visible-change="openRecipients">
                      <el-option v-for="user in people" :key="user.id" :value="user.id" :label="user.name" />
                    </el-select>
                  </el-form-item>
                </template>
                <el-form-item :label="replyTo ? '回复内容' : '要求或留言'">
                  <el-input v-model="discussion.content" type="textarea" :rows="3" maxlength="6000"
                    :aria-label="replyTo ? '回复内容' : '要求或留言'" />
                </el-form-item>
                <el-button type="primary" :loading="saving === 'discussion'" :disabled="!discussionValid || !!saving" native-type="submit">
                  {{ replyTo ? '提交回复' : '发送讨论' }}
                </el-button>
              </el-form>
            </section>
          </div>
          <div class="record-stack">
            <section v-if="$slots.progress" class="record-section"><slot name="progress" /></section>
            <section v-else-if="data" class="record-section" aria-label="进展反馈">
              <h3>{{ feedbackTitle || '进展反馈' }}</h3>
              <p v-if="archiveRequested && !data.archive && !data.can_archive" class="jc-small jc-muted">
                {{ !data.can_feedback ? '当前账号没有归档权限。' : 'can_archive' in data ? '提交完成结果后即可选择归档类别。' : '请先核对原表中的项目标识。' }}
              </p>
              <el-form v-if="data.can_feedback" id="record-progress-form" label-position="top" @submit.prevent="sendProgress">
                <el-form-item label="完成情况" class="record-outcome">
                  <el-radio-group v-model="outcome" aria-label="完成情况">
                    <el-radio-button value="done">已完成</el-radio-button>
                    <el-radio-button value="pending">未完成</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <el-form-item :label="outcome === 'done' ? '完成结果' : '目前进度情况'" required>
                  <el-input v-model="progress.content" type="textarea" :rows="3" maxlength="6000" aria-label="目前进度情况"
                    :placeholder="progressHint || '写几句话说明目前进展或完成结果。'" />
                </el-form-item>
                <el-form-item v-if="outcome === 'pending'" label="当前情况"><el-checkbox v-model="blocked">遇到阻碍</el-checkbox></el-form-item>
                <el-form-item label="下一步（可选）"><el-input v-model="progress.next_step" aria-label="下一步安排" maxlength="6000" /></el-form-item>
                <el-form-item v-if="outcome === 'pending'" label="下次跟进（可选）">
                  <el-date-picker v-model="progress.next_due_on" type="date" value-format="YYYY-MM-DD" aria-label="下次跟进日期" />
                </el-form-item>
                <el-form-item v-if="outcome === 'pending'" :label="blocked ? '未完成原因（必填）' : '未完成原因（可选）'" :required="blocked">
                  <el-input v-model="progress.problems" type="textarea" :rows="2" maxlength="6000" aria-label="未完成原因" />
                </el-form-item>
              </el-form>
              <template v-else-if="latest">
                <p class="record-text">{{ latest.content }}</p>
                <p v-if="latest.problems" class="record-text jc-down">阻碍 · {{ latest.problems }}</p>
                <p v-if="latest.next_step" class="record-text">下一步 · {{ latest.next_step }}</p>
              </template>
              <p v-else class="jc-muted jc-small">暂无进展反馈。</p>
              <WorkbookProjectArchive v-if="data.archive || data.can_archive || (data.can_feedback && 'can_archive' in data && outcome === 'done')" ref="archiveAction"
                :target="data.target" :title="data.record.title" :archive="data.archive" :feedback-id="data.archive_feedback_id"
                :disabled="!!progress.content.trim() || !!saving"
                @archived="updateArchivedRecord" @stale="load()" />
            </section>
          </div>
        </div>
        <details v-if="$slots.workspace || fullWidthContent" class="record-section record-workspace" open>
          <summary>业务详情</summary>
          <slot v-if="fullWidthContent" /><slot v-else name="workspace" />
        </details>
      </template>
      <slot v-else />
    </div>
    <template #footer>
      <div class="record-actions record-footer">
        <el-button :disabled="closeLocked" @click="visible = false">关闭</el-button>
        <slot name="footer" />
        <el-button v-if="data?.can_feedback && !$slots.progress" type="primary" :loading="saving === 'progress'"
          :disabled="!progressValid || !!saving" @click="sendProgress">提交进展</el-button>
      </div>
    </template>
  </el-dialog>
  <el-dialog v-model="historyOpen" title="过程记录" class="record-history-dialog" width="min(720px, calc(100vw - 24px))"
    align-center append-to-body destroy-on-close>
    <el-timeline v-if="history.length" class="record-timeline">
      <el-timeline-item v-for="item in history" :key="item.key" :timestamp="[datetime(item.created_at), item.author].filter(Boolean).join(' · ')" placement="top">
        <strong>{{ item.title || actionLabels[item.action] || item.action }}</strong>
        <span v-if="item.recipient" class="jc-small jc-muted"> · 给 {{ item.recipient }}</span>
        <StatusPill v-if="item.action === 'request'" :text="item.replied ? '已回复' : '待回复'" :tone="item.replied ? 'up' : 'warn'" />
        <StatusPill v-else-if="item.status" :text="progressLabels[item.status]" :tone="progressTones[item.status]" />
        <p class="record-text">{{ item.content }}</p>
        <p v-if="item.problems" class="record-text jc-down">阻碍 · {{ item.problems }}</p>
        <p v-if="item.next_step" class="record-text">下一步 · {{ item.next_step }} {{ item.next_due_on || '' }}</p>
        <el-button v-if="item.can_reply" link type="primary" @click="beginReply(item); historyOpen = false">回复这条要求</el-button>
      </el-timeline-item>
    </el-timeline>
    <slot name="history" />
    <el-empty v-if="!history.length && !$slots.history" description="暂无过程记录" :image-size="60" />
    <el-button v-if="before" :loading="moreLoading" @click="older">加载更早记录</el-button>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange, useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { datetime } from '@/utils/format'
import WorkbookProjectArchive from '@/components/WorkbookProjectArchive.vue'

const props = defineProps({ modelValue: Boolean, title: String, dialogTitle: String, heading: String, subtitle: String,
  status: String, statusTone: { type: String, default: 'brand' }, facts: Array, historyEntries: Array,
  target: Object, initial: Object, linked: Boolean, progressHint: String, feedbackTitle: String, contextTitle: String,
  closeLocked: Boolean, fullWidthContent: Boolean, archiveOnOpen: Boolean })
const emit = defineEmits(['update:modelValue', 'close', 'closed', 'saved'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const router = useRouter()
const recordTarget = computed(() => props.target?.id ? props.target : props.initial?.target)
const endpoint = computed(() => recordTarget.value && `/record-followups/${recordTarget.value.kind}/${encodeURIComponent(recordTarget.value.id)}`)
const params = computed(() => ({ sheet: recordTarget.value?.sheet || undefined, revision: recordTarget.value?.revision || undefined }))
const identity = computed(() => [endpoint.value, params.value.sheet, params.value.revision].join('|'))
const data = ref(null), error = ref(''), saving = ref(''), historyOpen = ref(false), moreLoading = ref(false)
const previous = ref([]), previousBefore = ref(null), finding = ref(false), people = ref([]), replyTo = ref(null)
const discussion = reactive({ kind: 'note', recipient: null, content: '' })
const progress = reactive({ content: '', problems: '', next_step: '', next_due_on: '' })
const outcome = ref('pending'), blocked = ref(false)
const archiveRequested = ref(false), archiveAction = ref(null)
const progressLabels = { doing: '推进中', blocked: '遇到阻碍', done: '已完成' }
const progressTones = { doing: 'brand', blocked: 'down', done: 'up' }
const actionLabels = { note: '留言', request: '办理要求', reply: '回复', progress: '进展反馈' }
const latest = computed(() => data.value?.latest)
const summary = computed(() => {
  const fields = props.facts || data.value?.record.facts || []
  if (!data.value) return fields.slice(0, 4)
  const tracking = { label: data.value.pending ? '跟进情况' : '最新反馈',
    value: (data.value.pending ? `${data.value.pending} 项待回复` : progressLabels[latest.value?.status]) || '待反馈',
    hint: latest.value?.next_due_on ? `下次跟进 ${latest.value.next_due_on}` : undefined }
  return [...fields.slice(0, 3), tracking]
})
const history = computed(() => {
  const entries = [...(data.value?.items || []), ...previous.value, ...(data.value?.legacy || []), ...(props.historyEntries || [])]
  return [...new Map(entries.map(item => [item.key, item])).values()]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || (b.id ?? 0) - (a.id ?? 0))
})
const before = computed(() => previous.value.length ? previousBefore.value : data.value?.before)
const discussionValid = computed(() => !!discussion.content.trim() && (replyTo.value || discussion.kind !== 'request' || discussion.recipient))
const progressValid = computed(() => !!progress.content.trim() && (outcome.value === 'done' || ((!blocked.value || !!progress.problems.trim()) && (!progress.next_due_on || !!progress.next_step.trim()))))
let version = 0, peopleVersion = 0
const retry = { discussion: null, progress: null }
let draftRevision = null
watch(() => progress.content, content => {
  if (content.trim() && draftRevision === null) draftRevision = data.value?.progress_revision ?? null
  if (!content.trim()) draftRevision = null
})

function reset() {
  version++
  peopleVersion++
  historyOpen.value = false
  data.value = props.initial || null
  error.value = ''
  previous.value = []
  replyTo.value = null
  people.value = []
  Object.assign(discussion, { kind: 'note', recipient: null, content: '' })
  Object.assign(progress, { content: '', problems: '', next_step: '', next_due_on: '' })
  archiveRequested.value = visible.value && props.archiveOnOpen
  outcome.value = archiveRequested.value ? 'done' : 'pending'
  blocked.value = false
  retry.discussion = retry.progress = null
  draftRevision = null
}
async function load(quiet = false) {
  if (!visible.value || !endpoint.value || saving.value) return
  const current = ++version
  try {
    const result = await api.get(endpoint.value, params.value, { silent: true })
    if (current === version) {
      data.value = result
      error.value = ''
    }
  } catch (e) {
    if (current === version && (!quiet || !data.value || [40301, 40401, 40901].includes(e.code))) {
      data.value = null
      previous.value = []
      historyOpen.value = false
      error.value = e.message
    }
  }
}
watch(() => [visible.value, identity.value], () => {
  reset()
  load()
}, { immediate: true })
watch([archiveRequested, archiveAction, () => data.value?.can_archive, saving, () => progress.content], () => {
  if (!archiveRequested.value || !archiveAction.value || !data.value?.can_archive || saving.value || progress.content.trim()) return
  archiveRequested.value = false
  archiveAction.value.openDialog()
}, { flush: 'post' })
useBusinessRefresh(() => load(true), { interval: 20000, enabled: () => visible.value && !!endpoint.value })

async function older() {
  if (!before.value || moreLoading.value) return
  const key = identity.value
  moreLoading.value = true
  try {
    const result = await api.get(endpoint.value, { ...params.value, before: before.value })
    if (key === identity.value && visible.value) {
      previous.value.push(...result.items)
      previousBefore.value = result.before
    }
  } catch {} finally { moreLoading.value = false }
}
async function loadRecipients(keyword = '') {
  if (!endpoint.value || !data.value) return
  const current = ++peopleVersion
  finding.value = true
  try {
    const results = await api.get(endpoint.value + '/recipients', { ...params.value, keyword }, { silent: true })
    if (current === peopleVersion) {
      const selected = people.value.find(person => person.id === discussion.recipient)
      people.value = selected && !results.some(person => person.id === selected.id) ? [selected, ...results] : results
    }
  } catch {} finally { if (current === peopleVersion) finding.value = false }
}
function openRecipients(open) { if (open) loadRecipients() }
function beginReply(item) { replyTo.value = item; discussion.content = '' }
function uuidFor(form, payload) {
  const body = JSON.stringify(payload)
  if (retry[form]?.body !== body) retry[form] = { body, id: crypto.randomUUID() }
  return retry[form].id
}
async function save(form, body, message) {
  if (saving.value || !endpoint.value || !data.value) return false
  const key = identity.value
  const query = new URLSearchParams(Object.entries(params.value).filter(([, value]) => value != null)).toString()
  saving.value = form
  version++
  try {
    const result = await api.post(endpoint.value + (query ? '?' + query : ''), { ...body, client_id: uuidFor(form, body) })
    if (key !== identity.value || !visible.value) return false
    data.value = result
    retry[form] = null
    ElMessage.success(message)
    emit('saved', result)
    announceBusinessChange()
    return true
  } catch (e) {
    if (key === identity.value && form === 'progress' && e.code === 40901) {
      // Keep the draft. The next submit uses the refreshed version after the history is opened.
      draftRevision = null
      saving.value = ''
      await load()
      historyOpen.value = !!data.value
    }
    return false
  } finally { saving.value = '' }
}
async function sendDiscussion() {
  if (!discussionValid.value) return
  const action = replyTo.value ? 'reply' : discussion.kind
  const body = { action, content: discussion.content, reply_to_id: replyTo.value?.id || null,
    recipient_id: replyTo.value ? null : discussion.recipient || null }
  if (await save('discussion', body, action === 'reply' ? '回复已提交' : '讨论已保存')) {
    discussion.content = ''
    discussion.recipient = null
    replyTo.value = null
  }
}
async function sendProgress() {
  if (!progressValid.value) return
  const completed = outcome.value === 'done'
  const body = { action: 'progress', content: progress.content, status: completed ? 'done' : blocked.value ? 'blocked' : 'doing',
    problems: completed ? '' : progress.problems, next_step: progress.next_step, next_due_on: completed ? null : progress.next_due_on || null,
    expected_progress: draftRevision ?? data.value.progress_revision }
  if (await save('progress', body, '进展已提交')) {
    Object.assign(progress, { content: '', problems: '', next_step: '', next_due_on: '' })
    outcome.value = 'pending'
    blocked.value = false
  }
}

function updateArchivedRecord(result) {
  version++
  data.value = result
  emit('saved', result)
}

defineExpose({ reload: load })
</script>

<style scoped>
:global(.record-detail-dialog.el-dialog) { display: flex; flex-direction: column; padding: 0; overflow: hidden; border-radius: 13px; max-height: calc(100dvh - 16px); }
:global(.record-detail-dialog > .el-dialog__header) { padding: 15px 23px 13px; border-bottom: 1px solid var(--jc-line); margin: 0; }
:global(.record-detail-dialog > .el-dialog__header .el-dialog__title) { font-size: 14px; font-weight: 600; }
:global(.record-detail-dialog > .el-dialog__header .el-dialog__headerbtn) { top: 1px; right: 4px; }
:global(.record-detail-dialog > .el-dialog__body) { padding: 0; flex: 1; min-height: 0; max-height: min(76vh, calc(100dvh - 124px)); overflow: auto; overscroll-behavior: contain; }
:global(.record-detail-dialog > .el-dialog__footer) { border-top: 1px solid var(--jc-line); padding: 9px 23px; }
:global(.record-history-dialog > .el-dialog__body) { max-height: 70dvh; overflow: auto; overscroll-behavior: contain; }
.record-page { padding: 20px 23px 24px; min-width: 0; min-height: 90px; }
.record-head { display: flex; gap: 14px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; }
.record-identity { min-width: 0; flex: 1 1 220px; }
.record-tags, .record-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.record-tags, .record-subtitle { color: var(--jc-ink-3); font-size: 13px; }
.record-identity h2 { font: 600 clamp(21px, 3vw, 28px)/1.4 var(--jc-font-serif); margin: 8px 0 4px; color: var(--jc-ink); overflow-wrap: anywhere; }
.record-subtitle, .record-section p { margin: 0; }
.record-facts { display: grid; grid-template-columns: 1fr 1fr 1fr 1.3fr; gap: 10px; margin: 17px 0; }
.record-facts > div { min-width: 0; padding: 10px 12px; border: 1px solid var(--jc-line); border-radius: 7px; background: var(--jc-bg-2, #f9f8f5); }
.record-facts dt, .record-facts small { font-size: 12px; color: var(--jc-ink-3); }
.record-facts dd { margin: 4px 0 0; color: var(--jc-ink); font-weight: 600; overflow-wrap: anywhere; }
.record-facts small { display: block; margin-top: 3px; }
.record-grid { display: grid; grid-template-columns: minmax(0, .95fr) minmax(0, 1fr); gap: 20px; margin-top: 14px; align-items: start; }
.record-stack { display: grid; gap: 10px; min-width: 0; }
.record-section { min-width: 0; padding: 14px; border: 1px solid var(--jc-line); border-radius: 8px; }
.record-section h3 { margin: 0 0 12px; font-size: 16px; color: var(--jc-ink); }
.record-discussion { margin-top: 18px; }
.record-recipient { width: 100%; }
.record-request { border: 1px solid var(--jc-line); border-radius: 6px; padding: 10px; margin-top: 10px; }
.record-request .jc-row { gap: 6px; flex-wrap: wrap; }
.record-error, .record-workspace { margin-top: 14px; }
.record-workspace > summary { cursor: pointer; font-size: 15px; font-weight: 600; }
.record-workspace > :deep(.jc-page) { margin-top: 10px; }
.record-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.record-outcome :deep(.el-radio-group) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
.record-outcome :deep(.el-radio-button__inner) { width: 100%; }
.record-actions :deep(.el-button + .el-button) { margin-left: 0; }
.record-footer { justify-content: flex-end; }
.record-timeline { padding-left: 4px; }
.record-timeline :deep(.jc-pill) { margin-left: 8px; }
.record-timeline p { margin: 5px 0; }
@media (max-width: 740px) {
  .record-grid { grid-template-columns: minmax(0, 1fr); gap: 12px; }
  .record-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  :global(.record-detail-dialog.el-dialog) { width: 100vw; height: 100dvh; max-height: 100dvh; border-radius: 0; }
  :global(.record-detail-dialog > .el-dialog__body) { max-height: none; }
  .record-page { padding: 14px; }
  .record-section { padding: 12px; }
}
</style>
