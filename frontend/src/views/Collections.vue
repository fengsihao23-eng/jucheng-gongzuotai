<template>
  <div v-if="selection" class="jc-page" v-loading="detailLoading">
    <PageHeader eyebrow="回款情况 · 回款跟进" :title="detail ? `${detail.customer} · ${detail.label}` : '回款跟进'"
      :desc="detail ? `${detail.contract_no} · 合同负责人 ${detail.contract_owner || '待指定'}` : ''">
      <el-button @click="clearDetail">返回回款情况</el-button>
      <el-button v-if="detail && auth.hasMenu('contracts')" @click="router.push('/contracts/' + detail.contract_id)">查看合同</el-button>
      <RecordFeedbackButton v-if="detail" :target="{ kind: 'legacy-collection', id: `${selection.contract_id}:${selection.plan_key}` }"
        progress-hint="说明催收、付款审批或预计到账的情况。" />
    </PageHeader>
    <Panel v-if="detail" title="回款跟进记录">
      <p v-if="detail.requested_at">指定回复人：{{ detail.reply_to_name || '—' }} · {{ detail.pending_reply ? '待回复' : '已回复' }}</p>
      <el-form v-if="detail.can_note || detail.can_reply || (detail.can_request && detail.active)" label-position="top">
        <el-form-item label="跟进方式">
          <el-radio-group v-model="form.kind">
            <el-radio-button v-if="detail.can_note" value="note">记录进展</el-radio-button>
            <el-radio-button v-if="detail.can_request && detail.active" value="request">催办</el-radio-button>
            <el-radio-button v-if="detail.can_reply" value="reply">回复催办</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.kind === 'request'" label="指定回复人">
          <el-select v-model="form.recipient_id" filterable placeholder="选择获授权人员" style="width: 100%">
            <el-option v-for="u in detail.candidates" :key="u.id" :value="u.id" :label="[u.name, u.department].filter(Boolean).join(' · ')" />
          </el-select>
        </el-form-item>
        <el-form-item label="预计回款日期（可选）"><el-date-picker v-model="form.expected_on" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item :label="form.kind === 'request' ? '需要反馈什么' : '反馈内容'">
          <el-input v-model="form.content" type="textarea" :rows="3" maxlength="1600" show-word-limit />
        </el-form-item>
        <el-button type="primary" :disabled="!form.content.trim() || (form.kind === 'request' && !form.recipient_id)" :loading="saving" @click="save">保存</el-button>
      </el-form>
      <h4 class="mt-16">跟进记录</h4>
      <el-timeline v-if="detail.entries.length">
        <el-timeline-item v-for="entry in detail.entries" :key="entry.id" :timestamp="datetime(entry.at)">
          <b>{{ entry.author }} · {{ actions[entry.kind] }}{{ entry.recipient ? ' → ' + entry.recipient : '' }}</b>
          <p class="plain">{{ entry.content }}</p><p v-if="entry.expected_on" class="jc-small jc-muted">预计回款 {{ entry.expected_on }}</p>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无跟进记录" :image-size="55" />
    </Panel>
  </div>
  <div v-else class="jc-page">
    <PageHeader eyebrow="经营 · 回款" title="回款情况" desc="从应收余额查到合同分期，记录预计回款、催办和负责人回复。">
      <CollectionHeaderActions />
      <el-button v-if="auth.hasMenu('receivables')" @click="router.push('/receivables')">开票与到账</el-button>
    </PageHeader>
    <el-radio-group v-model="view" class="mb-16" aria-label="回款视图">
      <el-radio-button value="month">当月回款</el-radio-button><el-radio-button value="unpaid">未收跟进</el-radio-button>
    </el-radio-group>
    <Panel v-if="view === 'month'" title="当月回款清单" :sub="monthly ? `${monthly.period} · ${monthly.count} 笔，合计 ${money(monthly.amount)} 元` : ''" :padding="false">
      <el-table :data="monthly?.items || []" empty-text="本月暂无已登记的实际到账">
        <el-table-column prop="date" label="到账日期" width="140" /><el-table-column prop="contract_name" label="合同名称" min-width="200" />
        <el-table-column prop="customer" label="客户" min-width="160" /><el-table-column label="实际回款金额（元）" align="right" width="170"><template #default="{row}">{{ money(row.amount) }}</template></el-table-column>
      </el-table>
      <el-pagination v-if="monthly?.count > 30" v-model:current-page="monthPage" :page-size="30" :total="monthly.count" layout="prev, pager, next" @current-change="loadMonth" />
    </Panel>
    <template v-else>
    <div class="jc-grid c5 buckets" v-loading="loading && !board.totals.length">
      <button v-for="entry in board.totals" :key="entry.key" type="button" :aria-pressed="filters.bucket === entry.key"
        @click="filters.bucket = filters.bucket === entry.key || entry.key === 'all' ? '' : entry.key; refresh()">
        <span>{{ labels[entry.key] }}</span><b class="jc-num">{{ money(entry.amount) }}</b><small>{{ entry.count }} 项待回款</small>
      </button>
    </div>
    <Panel class="mt-16" :padding="false" :loading="loading" title="应收与跟进">
      <template #extra>
        <div class="filters">
          <el-input v-model="filters.keyword" clearable placeholder="单位 / 合同号" style="width: 160px" @change="refresh" />
          <el-select v-model="filters.owner_id" clearable filterable placeholder="负责人" style="width: 125px" @change="refresh">
            <el-option v-for="u in board.owners" :key="u.id" :label="u.name" :value="u.id" />
          </el-select>
          <el-select v-model="filters.state" style="width: 132px" aria-label="跟进状态" @change="refresh">
            <el-option v-for="(label, value) in states" :key="value" :label="label" :value="value" />
          </el-select>
          <el-select v-model="filters.basis" style="width: 132px" aria-label="归年口径" @change="refresh">
            <el-option label="按到期年" value="due" /><el-option label="按签订年" value="sign" />
          </el-select>
          <el-date-picker v-model="filters.year" type="year" value-format="YYYY" :clearable="false" aria-label="分组年份" style="width: 100px" @change="refresh" />
        </div>
      </template>
      <p class="jc-muted jc-small notes">{{ board.note }}</p>
      <el-table :data="board.items" :row-class-name="({ row }) => row.overdue ? 'is-alert' : ''" @row-click="open">
        <el-table-column label="单位 / 合同" min-width="198">
          <template #default="{ row }"><b>{{ row.customer }}</b><div class="jc-muted jc-small">{{ row.contract_no }} · {{ row.contract_name }}</div></template>
        </el-table-column>
        <el-table-column label="分期 / 到期日" min-width="135">
          <template #default="{ row }">{{ row.label }}<div class="jc-muted jc-small">{{ row.due_on || '待明确到期日' }}</div></template>
        </el-table-column>
        <el-table-column label="应收余额" min-width="126" align="right">
          <template #default="{ row }"><b class="jc-num">{{ money(row.outstanding) }}</b><div class="jc-muted jc-small">已收 {{ money(row.received) }}</div></template>
        </el-table-column>
        <el-table-column label="负责人 / 预计回款" min-width="155">
          <template #default="{ row }">{{ row.owner_name || '待指定' }}<div class="jc-muted jc-small">{{ row.expected_on || '待反馈' }}</div></template>
        </el-table-column>
        <el-table-column label="最新跟进" min-width="175" show-overflow-tooltip>
          <template #default="{ row }"><StatusPill v-if="row.pending_reply" text="待回复" tone="warn" /><span v-else-if="row.replied_this_week" class="jc-up jc-small">本周已反馈</span><div>{{ row.last_note || '暂无记录' }}</div></template>
        </el-table-column>
        <el-table-column width="86"><template #default="{ row }"><el-button link type="primary" @click.stop="open(row)">查看详情</el-button></template></el-table-column>
      </el-table>
    </Panel>
    <el-pagination v-model:current-page="page" :page-size="30" :total="board.total" layout="total, prev, pager, next" @current-change="load" />
    </template>
  </div>
</template>

<script setup>
import RecordFeedbackButton from '@/components/records/RecordFeedbackButton.vue'
import CollectionHeaderActions from '@/components/CollectionHeaderActions.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime, money } from '@/utils/format'

const auth = useAuth(), route = useRoute(), router = useRouter()
const view = ref(route.query.contract || route.query.basis === 'unpaid' ? 'unpaid' : 'month'), monthly = ref(null), monthPage = ref(1)
let monthRequest = 0
async function loadMonth() {
  const current = ++monthRequest
  const result = await api.get('/collections/monthly-receipts', { page: monthPage.value })
  if (current === monthRequest) monthly.value = result
}
const filters = reactive({ keyword: '', owner_id: undefined, basis: 'due', bucket: '', state: 'open', year: undefined })
const states = { open: '待回款', pending: '待回复', unreported: '本周未反馈', settled: '已结清', all: '全部' }
const actions = { note: '记录进展', request: '催办', reply: '回复催办' }
const board = ref({ totals: [], owners: [], items: [], total: 0 })
const labels = computed(() => ({ all: '应收余额', before: filters.year + ' 年以前', current: filters.year + ' 年', future: filters.year + ' 年以后', undated: '未定日期' }))
const page = ref(1), loading = ref(false)
const detail = ref(null), detailLoading = ref(false), saving = ref(false)
const selection = ref(null), form = reactive({ kind: 'note', content: '', recipient_id: null, expected_on: null })
let loadId = 0, detailId = 0
function refresh() { page.value = 1; return load() }
async function load() {
  const current = ++loadId
  loading.value = true
  try {
    const result = await api.get('/collections', { ...filters, page: page.value, size: 30 })
    if (current !== loadId) return
    board.value = result
    filters.year = String(result.year)
  } catch {} finally { if (current === loadId) loading.value = false }
}
function endpoint(row) { return '/collections/' + row.contract_id + '/' + row.plan_key }
function resetForm(row) {
  Object.assign(form, { kind: row.can_reply ? 'reply' : row.can_note ? 'note' : 'request', content: '', recipient_id: row.reply_to_id, expected_on: row.expected_on })
}
async function open(row) {
  const current = ++detailId
  selection.value = row; detail.value = null; detailLoading.value = true
  window.scrollTo({ top: 0 })
  try {
    const result = await api.get(endpoint(row))
    if (current === detailId) { detail.value = result; resetForm(result) }
  } catch { if (current === detailId) clearDetail() } finally { if (current === detailId) detailLoading.value = false }
}
function clearDetail() {
  detailId++; detail.value = null; selection.value = null; detailLoading.value = false
  if (route.query.contract) router.replace({ query: {} })
}
async function save() {
  saving.value = true
  const current = detailId, selected = selection.value
  try {
    const result = await api.post(endpoint(selected), { kind: form.kind, content: form.content, expected_on: form.expected_on || null,
      recipient_id: form.kind === 'request' ? form.recipient_id : null })
    if (current === detailId) { detail.value = result; resetForm(result) }
    ElMessage.success('跟进已保存'); load()
  } catch {} finally { saving.value = false }
}
onMounted(() => {
  loadMonth()
  load()
  if (/^[1-9][0-9]*$/.test(String(route.query.contract)) && /^[0-9]+$/.test(String(route.query.plan))) {
    open({ contract_id: route.query.contract, plan_key: route.query.plan })
  }
})
</script>

<style scoped>
.buckets button { display: grid; gap: 5px; text-align: left; padding: 13px 15px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); color: var(--jc-ink); background: var(--jc-surface); cursor: pointer; }
.buckets button:is([aria-pressed="true"], :hover) { border-color: var(--jc-brand); }
.buckets b { font-size: 20px; }.buckets small { color: var(--jc-ink-3); }
.filters { display: flex; gap: 8px; flex-wrap: wrap; }.notes { margin: 12px 16px; }.plain { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
