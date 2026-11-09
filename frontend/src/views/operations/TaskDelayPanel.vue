<template>
  <section v-if="task.delay_status" class="delay-panel" aria-label="延期说明">
    <header><h3>延期说明</h3><StatusPill :text="DELAY_STATUS[task.delay_status]" :tone="decisionTone(task.delay_status)" /></header>
    <p class="dates">原定 {{ task.deadline_basis }}<template v-if="task.completed_on"> · 实际完成 {{ task.completed_on }}</template></p>
    <template v-if="latest">
      <p class="reason">{{ latest.reason }}</p>
      <p class="meta">{{ latest.author_name }} · {{ datetime(latest.created_at) }}</p>
      <div v-if="latest.decision" class="review-result">
        <b>{{ DELAY_STATUS[latest.decision] }}</b> · {{ latest.reviewer_name }} · {{ datetime(latest.reviewed_at) }}
        <p v-if="latest.review_comment">{{ latest.review_comment }}</p>
      </div>
    </template>
    <p v-else class="meta">尚未登记延期原因。</p>

    <el-button v-if="task.can_explain_delay && !editing" link type="primary" :disabled="busy" @click="start">{{ latest ? '更新延期原因' : '补充延期原因' }}</el-button>
    <div v-if="task.can_explain_delay && editing" class="editor">
      <el-input v-model="reason" type="textarea" :rows="2" aria-label="延期原因" placeholder="说明为什么没能按原定日期完成" maxlength="6000" />
      <p v-if="draftStale" class="meta">任务已有更新，请核对最新说明。<el-button link type="primary" @click="reasonRevision = task.revision">按最新记录继续</el-button></p>
      <div class="buttons"><el-button size="small" @click="editing = false">取消</el-button><el-button size="small" type="primary" :disabled="busy || draftStale || !reason.trim() || reason.trim() === latest?.reason" @click="submitReason">提交延期原因</el-button></div>
    </div>
    <div v-if="task.can_review_delay && latest" class="editor review">
      <el-input v-model="comment" type="textarea" :rows="2" maxlength="6000" aria-label="延期复核意见" placeholder="可填写复核意见；不认可时请说明原因" />
      <div class="buttons"><el-button size="small" :disabled="busy" @click="decide('rejected')">不认可</el-button><el-button size="small" type="primary" :disabled="busy" @click="decide('accepted')">认可</el-button></div>
    </div>

    <details v-if="history.length > 1">
      <summary>查看以往说明（{{ history.length - 1 }}）</summary>
      <div v-for="entry in history.slice(1)" :key="entry.id" class="older">
        <p class="meta">{{ entry.author_name }} · {{ datetime(entry.created_at) }} · {{ entry.decision ? DELAY_STATUS[entry.decision] : '已更新' }}</p>
        <p class="reason">{{ entry.reason }}</p>
        <p v-if="entry.decision" class="meta">{{ entry.reviewer_name }} · {{ datetime(entry.reviewed_at) }}<template v-if="entry.review_comment"> · {{ entry.review_comment }}</template></p>
      </div>
    </details>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { datetime } from '@/utils/format'
import { DELAY_STATUS } from './shared'

const props = defineProps({ task: { type: Object, required: true }, save: { type: Function, required: true }, busy: Boolean })
const history = computed(() => props.task.delay_history || [])
const latest = computed(() => history.value[0])
const editing = ref(false), reason = ref(''), reasonRevision = ref(null), comment = ref('')
const draftStale = computed(() => reasonRevision.value !== props.task.revision)
const decisionTone = status => ({ accepted: 'up', rejected: 'down', pending: 'warn', needs_reason: 'info' }[status] || 'info')
watch([() => latest.value?.id, () => latest.value?.decision], () => { comment.value = '' })

function start() {
  reason.value = latest.value?.reason || props.task.incomplete_reason || ''
  reasonRevision.value = props.task.revision
  editing.value = true
}
async function submitReason() {
  if (await props.save('delay-reason', { reason: reason.value.trim() }, '延期原因已提交', reasonRevision.value)) editing.value = false
}
function decide(decision) {
  const note = comment.value.trim()
  if (decision === 'rejected' && !note) return ElMessage.warning('不认可时请填写意见')
  return props.save('delay-review', { reason_id: latest.value.id, decision, comment: note }, '延期复核已保存', props.task.revision)
}
</script>

<style scoped>
.delay-panel { border: 1px solid var(--jc-line); border-radius: 8px; padding: 12px 14px; }.delay-panel header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
h3 { font-size: 15px; margin: 0; color: var(--jc-ink); }.dates, .meta { margin: 4px 0; font-size: 12px; color: var(--jc-ink-3); line-height: 1.6; }
.reason, .review-result p, .older p { white-space: pre-wrap; overflow-wrap: anywhere; }.reason { margin: 8px 0 4px; line-height: 1.65; }.review-result { margin-top: 8px; padding: 7px 9px; border-radius: 6px; background: var(--jc-surface-2); font-size: 12px; color: var(--jc-ink-2); }.review-result p { margin: 4px 0 0; }
.editor { margin-top: 9px; }.buttons { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; margin-top: 8px; }.buttons .el-button + .el-button { margin: 0; }
details { margin-top: 8px; } summary { cursor: pointer; color: var(--jc-brand); font-size: 12px; }.older { margin-top: 7px; padding-top: 5px; border-top: 1px solid var(--jc-line-2); }
</style>
