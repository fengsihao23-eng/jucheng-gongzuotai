<template>
  <div class="jc-page followup-page">
    <PageHeader eyebrow="回款跟进" :title="item?.contract_name || '回款跟进'" desc="填写当前阶段，保留每次跟进记录，发起人会收到更新通知。">
      <el-button @click="router.push('/notifications')">返回消息</el-button>
      <el-button v-if="item?.contract_link" @click="router.push({ path: item.contract_link.path, query: { revision: item.contract_link.revision } })">查看合同</el-button>
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <template v-if="item">
      <Panel v-if="item.contract" title="对应合同" class="mb-16">
        <el-descriptions :column="1">
          <el-descriptions-item label="合同名称">{{ item.contract.name }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ item.contract.customer || '—' }}</el-descriptions-item>
          <el-descriptions-item label="合同金额">{{ item.contract.amount_note || (number(item.contract.amount) + ' 元') }}</el-descriptions-item>
          <el-descriptions-item label="未收金额">{{ number(item.contract.outstanding) }} 元</el-descriptions-item>
          <el-descriptions-item label="签约主体">{{ item.contract.party || '—' }}</el-descriptions-item>
          <el-descriptions-item label="签订日期">{{ item.contract.date || '—' }}</el-descriptions-item>
        </el-descriptions>
        <a v-for="file in item.contract.attachments" :key="file.url" :href="file.url" target="_blank" rel="noopener noreferrer">{{ file.filename }}</a>
      </Panel>
      <Panel title="跟进要求">
        <div class="jc-row wrap">
          <b>{{ item.author_name || '历史成员' }} → {{ item.recipient_name || '负责人' }}</b>
          <StatusPill :text="states[item.state]" :tone="item.state === 'pending' ? 'warn' : item.state === 'replied' ? 'up' : ''" />
          <span class="jc-small jc-muted">{{ datetime(item.created_at) }}</span>
        </div>
        <p class="message">{{ item.content }}</p>
        <p v-if="item.request_delivery" class="jc-small jc-muted">企业微信通知：{{ delivery[item.request_delivery.status] }}</p>
        <el-button v-if="item.can_cancel" link type="danger" :loading="saving" @click="withdraw">撤回要求</el-button>
      </Panel>
      <Panel title="跟进情况" class="mt-16">
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
          <el-button type="primary" native-type="submit" :disabled="saving || !content.trim() || (needsReason && !problems.trim())" :loading="saving">{{ item.can_reply ? '回复跟进情况' : '提交跟进记录' }}</el-button>
        </el-form>
        <el-empty v-else-if="!item.reply" :description="item.state === 'cancelled' ? '此要求已撤回' : '等待负责人回复'" :image-size="55" />
      </Panel>
      <Panel v-if="item.reports?.length" title="跟进过程记录" class="mt-16">
        <el-timeline>
          <el-timeline-item v-for="entry in item.reports" :key="entry.id" :timestamp="datetime(entry.created_at)" placement="top">
            <b>{{ entry.stage_name }} · {{ entry.author_name }}</b>
            <p class="message">{{ entry.content }}</p>
            <p v-if="entry.problems" class="jc-small problem-note">存在问题：{{ entry.problems }}</p>
            <p v-if="entry.expected_on" class="jc-small jc-muted">预计回款日：{{ entry.expected_on }}</p>
            <p v-if="entry.next_step" class="jc-small jc-muted">下一步：{{ entry.next_step }}</p>
          </el-timeline-item>
        </el-timeline>
        <p class="jc-small jc-muted">跟进人的回款反馈需与原表核对，不会自动修改合同金额或登记到账。</p>
      </Panel>
    </template>
    <el-skeleton v-else-if="loading" :rows="5" animated />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const route = useRoute(), router = useRouter()
const item = ref(null), error = ref(''), content = ref(''), expectedOn = ref(''), loading = ref(false), saving = ref(false)
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
.followup-page { max-width: 1000px; }.message { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }.mb-16 { margin-bottom: 16px; }
.problem-note { color: var(--jc-down, #c23b2b); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
