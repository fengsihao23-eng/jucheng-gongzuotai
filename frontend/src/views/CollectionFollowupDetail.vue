<template>
  <RecordDetailDialog :model-value="true" @update:model-value="emit('update:modelValue', $event)"
    dialog-title="回款跟进" :heading="item?.contract_name" :status="item ? states[item.state] : ''"
    :status-tone="item?.state === 'pending' ? 'warn' : 'up'"
    :target="item ? { kind: 'collection-thread', id: route.params.id } : null"
    :facts="item ? [
      { label: '负责人', value: item.recipient_name || '—' },
      { label: '待回款金额', value: number(item.contract?.outstanding) + ' 元' },
      { label: '约定回款日期', value: item.payment_due_on || '待确认' },
    ] : []" :history-entries="nativeHistory">
    <template #actions>
      <el-button v-if="item?.contract_link" @click="router.push({ path: item.contract_link.path, query: { revision: item.contract_link.revision } })">查看合同</el-button>
      <el-button v-if="removal" type="danger" plain @click="deleteRecord(removal)">删除跟进</el-button>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-skeleton v-else-if="!item && loading" :rows="5" animated />
    <div v-if="item" class="jc-stack">
      <h3>跟进要求</h3>
        <div class="jc-row wrap">
          <b>{{ item.author_name || '历史成员' }} → {{ item.recipient_name || '负责人' }}</b>
          <StatusPill :text="states[item.state]" :tone="item.state === 'pending' ? 'warn' : item.state === 'replied' ? 'up' : ''" />
          <span class="jc-small jc-muted">{{ datetime(item.created_at) }}</span>
        </div>
        <p class="message">{{ item.content }}</p>
        <p v-if="item.request_delivery" class="jc-small jc-muted">企业微信通知：{{ delivery[item.request_delivery.status] }}</p>
        <el-button v-if="item.can_cancel" link type="danger" :loading="saving" @click="withdraw">撤回要求</el-button>
    </div>
    <template #progress>
  <h3>执行情况反馈</h3>
    <el-alert v-if="item.payment_due_on" :title="`约定或预计回款日 ${item.payment_due_on}，仍有未收余额，请填写跟进情况和未收原因。`" type="warning" :closable="false" />
    <p v-if="item.stage" class="jc-small">当前流程：{{ item.stages.find(s => s.value === item.stage)?.label }}</p>
    <p class="jc-small jc-muted">联系沟通 → 付款审批 → 等待付款 → 回款反馈；遇到问题可记录为“需协调”。</p>
    <template v-if="item.reply">
      <div class="jc-row wrap"><b>{{ item.recipient_name || '负责人' }}</b><span class="jc-small jc-muted">{{ datetime(item.replied_at) }}</span></div>
      <p class="message">{{ item.reply }}</p>
      <p v-if="item.expected_on" class="jc-small jc-muted">预计回款日：{{ item.expected_on }}</p>
    </template>
    <el-form v-if="item.can_update" label-position="top" @submit.prevent="reply">
      <el-form-item label="当前阶段">
        <el-select v-model="stage" aria-label="当前阶段">
          <el-option v-for="option in item.stages" :key="option.value" :value="option.value" :label="option.label" />
        </el-select>
      </el-form-item>
      <el-form-item label="跟进情况">
        <el-input v-model="content" aria-label="回复跟进情况" type="textarea" :rows="4" maxlength="1600" show-word-limit placeholder="例如：已与客户确认付款流程，目前在走付款审批" />
      </el-form-item>
      <el-form-item :label="needsReason ? '到期未收原因（必填）' : '存在问题及需协调事项（可选）'">
        <el-input v-model="problems" aria-label="存在问题" type="textarea" :rows="2" maxlength="2000" placeholder="若遇到审批阻碍、资料缺失等需要协调解决的问题，请在此注明" />
      </el-form-item>
      <el-form-item label="预计回款日（可选）">
        <el-date-picker v-model="expectedOn" aria-label="预计回款日" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" />
      </el-form-item>
      <el-form-item label="下一步安排"><el-input v-model="nextStep" aria-label="下一步安排" maxlength="1600" placeholder="下一步联系谁，安排什么" /></el-form-item>
    </el-form>
    <el-empty v-else-if="!item.reply" :description="item.state === 'cancelled' ? '此要求已撤回' : '等待负责人回复'" :image-size="55" />
    </template>
    <template v-if="item?.can_update" #footer>
      <el-button type="primary" :disabled="saving || !content.trim() || (needsReason && !problems.trim())" :loading="saving" @click="reply">
        {{ item.can_reply ? '回复跟进情况' : '提交跟进记录' }}
      </el-button>
    </template>
  </RecordDetailDialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import { datetime } from '@/utils/format'
import { deleteRecord, recordRemoval } from '@/composables/useRecordDeletion'
import { useAuth } from '@/store/auth'

const emit = defineEmits(['update:modelValue'])
const route = useRoute(), router = useRouter()
const auth = useAuth()
const item = ref(null), error = ref(''), content = ref(''), expectedOn = ref(''), loading = ref(false), saving = ref(false)
const nativeHistory = computed(() => (item.value?.reports || []).map(entry => ({ ...entry, key: `collection-progress-${entry.id}`,
  author: entry.author_name, action: 'progress', title: entry.stage_name, next_due_on: entry.expected_on })))
const removal = computed(() => item.value &&
  (item.value.author_id === auth.profile?.id || auth.canDelete('collections:request', 'contracts:write', 'receivables:correct')) &&
  recordRemoval('collection-thread', route.params.id, item.value.contract_name, () => emit('update:modelValue', false),
    { title: '删除回款跟进' }))
const stage = ref('contact')
const problems = ref('')
const needsReason = computed(() => Boolean(item.value?.payment_due_on) && stage.value !== 'reported_paid')
const nextStep = ref('')
const number = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
const states = { pending: '待回复', replied: '已回复', cancelled: '已撤回' }
const delivery = { 0: '等待发送', 1: '企微已受理', 2: '尚未送达' }
const endpoint = computed(() => '/business-workbook/collection-followups/' + encodeURIComponent(route.params.id))
let generation = 0, timer
async function load(quiet = false) {
  const id = ++generation
  loading.value = true
  try {
    const next = await api.get(endpoint.value, {}, { silent: true })
    if (id === generation) { item.value = next; error.value = '' }
  } catch (e) { if (id === generation && !quiet) error.value = e.message || '无法读取回款跟进' }
  finally { if (id === generation) loading.value = false }
}
async function submit(action, body) {
  if (saving.value) return
  const id = generation
  saving.value = true
  try {
    const next = await api.post(endpoint.value + '/' + action, body)
    if (id !== generation) return
    item.value = next
    announceBusinessChange()
    content.value = ''
    problems.value = ''
    expectedOn.value = ''
    nextStep.value = ''
    ElMessage.success(action === 'reply' ? '已提交回复' : action === 'progress' ? '已保存跟进记录' : '已撤回要求')
  } catch { /* API displays the error */ }
  finally { saving.value = false }
}
function reply() { if (content.value.trim()) return submit(item.value.can_reply ? 'reply' : 'progress',
  { content: content.value.trim(), problems: problems.value.trim() || null, expected_on: expectedOn.value || null, stage: stage.value,
    expected_report_id: item.value.reports?.at(-1)?.id || null, next_step: nextStep.value.trim() || null }) }
async function withdraw() {
  try { await ElMessageBox.confirm('撤回后，负责人将无法继续回复这条要求。', '撤回跟进要求', { type: 'warning', confirmButtonText: '撤回' }) }
  catch { return }
  return submit('cancel', {})
}
watch(() => route.params.id, id => { if (id) { item.value = null; content.value = ''; problems.value = ''; expectedOn.value = ''; nextStep.value = ''; stage.value = 'contact'; load() } }, { immediate: true })
onMounted(() => { timer = setInterval(() => { if (!document.hidden && !loading.value && !saving.value && item.value) load(true) }, 15000) })
onBeforeUnmount(() => { generation++; clearInterval(timer) })
</script>

<style scoped>
.message { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
</style>
