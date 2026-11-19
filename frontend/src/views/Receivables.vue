<template>
  <div class="jc-page">
    <PageHeader eyebrow="财务台账" title="开票回款" desc="按实际开票与到账登记，保留审批、凭证和核销记录。">
      <el-button @click="refresh" :loading="loading">刷新</el-button>
      <el-button v-if="tab === 'invoices' && can('invoice')" type="primary" @click="open('invoice')">登记已开票</el-button>
      <el-button v-if="tab === 'receipts' && can('receipt')" type="primary" @click="open('receipt')">确认到账</el-button>
    </PageHeader>

    <div class="jc-grid c4">
      <Kpi :label="periodLabel ? `${periodLabel} 开票` : '当月开票'" :value="money(summary.net_issued)" unit="元" hint="已确认开票金额，扣除红冲" />
      <Kpi :label="periodLabel ? `${periodLabel} 到账` : '当月到账'" :value="money(summary.total_received)" unit="元"
           :hint="`合同 ${money(summary.contract_received)} / 其他及待匹配 ${money(summary.other_received)}`" />
      <Kpi label="当前已开票未收" :value="money(summary.invoice_outstanding_now)" unit="元" />
      <Kpi label="当前应收计划逾期" :value="money(summary.plan_overdue_now)" unit="元" />
    </div>

    <Panel class="mt-16" :loading="loading">
      <el-tabs v-model="tab" @tab-change="switchTab">
        <el-tab-pane label="开票台账" name="invoices" />
        <el-tab-pane label="到账台账" name="receipts" />
        <el-tab-pane v-if="can('source')" label="来源核对" name="sources" />
      </el-tabs>
      <div class="ledger-filters" v-if="tab !== 'sources'">
        <el-input v-model="filter.keyword" clearable placeholder="搜索票号、凭证号、合同、单位" @keyup.enter="search" @clear="search" />
        <el-date-picker v-model="filter.period" type="month" value-format="YYYY-MM" placeholder="全部月份" @change="search" />
        <el-select v-if="tab === 'invoices'" v-model="filter.state" clearable placeholder="全部开票状态" @change="search">
          <el-option v-for="(label, value) in invoiceFilters" :key="value" :label="label" :value="value" />
        </el-select>
        <el-select v-else v-model="filter.kind" clearable placeholder="全部收款类型" @change="search">
          <el-option v-for="(label, value) in { ...kinds, non_contract: '其他及待匹配' }" :key="value" :label="label" :value="value" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>
      <div class="ledger-filters" v-else>
        <el-select v-model="filter.sourceState" clearable placeholder="全部来源状态" @change="search">
          <el-option v-for="(label, value) in sourceStates" :key="value" :label="label" :value="value" />
        </el-select>
        <el-select v-model="sourceKind" aria-label="历史资料类型">
          <el-option label="历史开票" value="invoice" /><el-option label="历史到账" value="receipt" />
        </el-select>
        <el-button @click="downloadTemplate">下载 CSV 模板</el-button>
        <el-button @click="filePicker?.click()" :loading="busy">上传并预览</el-button>
        <input ref="filePicker" type="file" accept=".csv,text/csv" hidden @change="upload" />
        <el-button type="primary" :disabled="!selected.length" :loading="busy" @click="applyRows(selected)">确认所选（{{ selected.length }}）</el-button>
      </div>
      <p v-if="tab === 'sources'" class="jc-muted jc-small">先核对源行，再入正式台账。已经入账的来源变更或移除会保留提示，请在台账内更正。
        <span v-if="syncStatus">最近定时同步：{{ datetime(syncStatus.run_at) }} · {{ syncStates[syncStatus.status] || syncStatus.status }}</span>
      </p>
      <p v-if="tab === 'receipts'" class="jc-muted jc-small">旧合同回款仍可在合同台账查看。关联已有回款 ID 只补来源和核销信息，不再新增合同回款。</p>

      <h3 v-if="tab === 'invoices'">开票清单</h3>
      <el-table v-if="tab === 'invoices'" :data="rows" row-key="id" empty-text="没有符合条件的开票记录">
        <el-table-column label="客户 / 发票号码" min-width="220"><template #default="{ row }">
          <div>{{ row.customer }}</div><small class="jc-muted">{{ row.invoice_no || '待开票' }}</small>
        </template></el-table-column>
        <el-table-column prop="issued_on" label="开票日期" width="112" />
        <el-table-column prop="contract_no" label="合同编号" min-width="115" />
        <el-table-column label="申请 / 有效开票" width="148" align="right"><template #default="{ row }">{{ money(row.state === 'pending' ? row.requested_amount : row.net) }}</template></el-table-column>
        <el-table-column label="发票未收" width="128" align="right"><template #default="{ row }">{{ row.state === 'pending' ? '—' : money(row.outstanding) }}</template></el-table-column>
        <el-table-column label="状态" width="105"><template #default="{ row }"><StatusPill :text="invoiceFilters[row.state]" :tone="row.state === 'pending' ? 'warn' : 'info'" /></template></el-table-column>
        <el-table-column label="操作" width="185"><template #default="{ row }">
          <el-button link @click="detail('invoice', row)">明细</el-button>
          <el-button v-if="row.state === 'pending' && can('invoice')" link type="primary" @click="open('issue', row)">确认开票</el-button>
          <el-button v-if="row.state === 'issued' && can('correct')" link @click="open('correct', row)">红冲／作废</el-button>
        </template></el-table-column>
      </el-table>

      <el-table v-else-if="tab === 'receipts'" :data="rows" row-key="id" empty-text="没有符合条件的到账记录">
        <el-table-column label="付款方 / 到账凭证号" min-width="220"><template #default="{ row }">
          <div>{{ row.payer }}</div><small class="jc-muted">{{ row.reference }}</small>
        </template></el-table-column>
        <el-table-column prop="received_on" label="到账日期" width="112" />
        <el-table-column label="类型 / 合同" min-width="130"><template #default="{ row }">{{ kinds[row.kind] }}<div class="jc-muted jc-small">{{ row.contract_no }}</div></template></el-table-column>
        <el-table-column label="实收" width="130" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
        <el-table-column label="未分配至发票" width="136" align="right"><template #default="{ row }">{{ money(row.unallocated) }}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.state === 'void' ? '已撤销' : '已确认' }}</template></el-table-column>
        <el-table-column label="操作" width="208"><template #default="{ row }">
          <el-button link @click="detail('receipt', row)">明细</el-button>
          <el-button v-if="row.state === 'confirmed' && row.kind === 'unmatched' && can('receipt')" link type="primary" @click="open('assign', row)">关联合同</el-button>
          <el-button v-if="row.state === 'confirmed' && row.kind !== 'unmatched' && can('receipt')" link type="primary" @click="openAllocation(row)">核销发票</el-button>
          <el-button v-if="row.state === 'confirmed' && can('correct')" link type="danger" @click="open('void', row)">撤销</el-button>
        </template></el-table-column>
      </el-table>

      <el-table v-else :data="rows" row-key="id" empty-text="没有符合条件的来源记录" @selection-change="selected = $event">
        <el-table-column type="selection" width="46" :selectable="isApplicable" />
        <el-table-column prop="source_key" label="来源" min-width="130" />
        <el-table-column prop="record_key" label="源记录标识" min-width="170" />
        <el-table-column label="状态" width="100"><template #default="{ row }">{{ sourceStates[row.state] }}</template></el-table-column>
        <el-table-column prop="error" label="核对提示" min-width="260" />
        <el-table-column label="操作" width="155"><template #default="{ row }">
          <el-button link @click="detail('source', row)">查看字段</el-button>
          <el-button v-if="isApplicable(row)" link type="primary" :disabled="busy" @click="applyRows([row])">确认入账</el-button>
        </template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="30" :total="total" layout="total, prev, pager, next" @current-change="loadRows" />
    </Panel>

    <el-dialog v-model="dialog" :title="titles[action]" width="min(600px, 94vw)" :close-on-click-modal="false" @closed="action = ''">
      <el-form v-if="action !== 'allocate'" label-position="top" @submit.prevent="save">
        <el-form-item v-for="field in visibleFields" :key="field" :label="labels[field] || field">
          <el-select v-if="field === 'kind'" v-model="form.kind" style="width: 100%"><el-option v-for="(label, value) in kinds" :key="value" :label="label" :value="value" /></el-select>
          <el-date-picker v-else-if="['issued_on', 'received_on'].includes(field)" v-model="form[field]" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          <el-input v-else v-model="form[field]" :type="['note', 'reason'].includes(field) ? 'textarea' : 'text'" :placeholder="hints[field] || ''" />
        </el-form-item>
      </el-form>
      <template v-else>
        <p class="jc-muted jc-small">实际到账 {{ money(target.amount) }} 元。填写本笔到账分配给各发票的金额；清空某项可解除该笔核销。</p>
        <div v-for="(item, index) in allocations" :key="index" class="allocation">
          <el-select v-model="item.invoice_id" filterable placeholder="选择发票" aria-label="核销发票">
            <el-option v-for="inv in invoiceOptions" :key="inv.id" :label="`${inv.invoice_no} · 未收 ${money(inv.outstanding)}`" :value="inv.id" />
          </el-select>
          <el-input v-model="item.amount" placeholder="核销金额（元）" aria-label="核销金额" />
          <el-button link type="danger" @click="allocations.splice(index, 1)">移除</el-button>
        </div>
        <el-pagination v-if="invoiceCount > 100" v-model:current-page="invoicePage" :page-size="100" :total="invoiceCount" layout="prev, pager, next" @current-change="loadInvoices" />
        <el-button @click="allocations.push({ invoice_id: null, amount: '' })">增加发票</el-button>
      </template>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="busy" @click="save">确认</el-button></template>
    </el-dialog>

    <el-dialog v-model="detailOpen" title="记录明细" width="min(640px, 94vw)">
      <el-descriptions :column="1" border>
        <el-descriptions-item v-for="(value, key) in detailFields" :key="key" :label="labels[key] || key">{{ value === null || value === '' ? '—' : value }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="detailType === 'receipt' && detailAllocations.length" class="mt-16">
        <el-table :data="detailAllocations"><el-table-column prop="invoice_no" label="核销发票" /><el-table-column prop="amount" label="核销金额（元）" /></el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime, money } from '@/utils/format'
import dayjs from 'dayjs'

const auth = useAuth(), route = useRoute()
const can = (key) => auth.can(`receivables:${key}`)
const prefix = '/receivables'
const kinds = { contract: '合同回款', unmatched: '待匹配收款', other: '其他收款' }
const invoiceFilters = { pending: '待确认开票', issued: '已开票', outstanding: '已开票未收', void: '已作废', cancelled: '申请已关闭' }
const sourceStates = { pending: '待确认', error: '待核对', applied: '已入账', changed: '来源已变化', missing: '来源已移除' }
const syncStates = { success: '成功', failed: '失败，请查看系统管理的任务日志', running: '执行中' }
const labels = { id: '记录 ID', contract_id: '合同 ID', contract_no: '合同编号', approval_no: '来源审批单号', invoice_no: '发票号码', issued_on: '开票日期', received_on: '实际到账日', amount: '金额（元）', customer: '客户', payer: '付款方', account: '收款账户简称', reference: '到账凭证号', kind: '收款类型', payment_id: '已有回款 ID（选填）', note: '备注（选填）', requested_amount: '申请金额', issued_amount: '原开票金额', reduced_amount: '累计红冲金额', reduced_total: '更正后累计红冲金额（元）', reason: '处理原因', net: '有效开票金额', allocated: '已核销金额', outstanding: '发票未收金额', unallocated: '未分配至发票', updated_at: '更新时间', confirmed_by: '确认人账号 ID', state: '状态' }
const hints = { reference: '同一笔到账始终使用同一个凭证号', payment_id: '旧回款已入合同台账时，填其记录 ID；新到账留空', reduced_total: '填写原票至今累计红冲金额；全额即作废', approval_no: '有待开票申请时可填审批单号' }
const titles = { invoice: '登记实际开票', issue: '确认实际开票', receipt: '确认实际到账', assign: '关联合同', allocate: '分配到账至发票', correct: '红冲或作废', void: '撤销到账记录' }
const formFields = { invoice: ['customer', 'contract_no', 'approval_no', 'invoice_no', 'issued_on', 'amount', 'note'], issue: ['invoice_no', 'issued_on', 'amount'], receipt: ['kind', 'payer', 'account', 'reference', 'received_on', 'amount', 'contract_no', 'payment_id', 'note'], assign: ['contract_no'], correct: ['reduced_total', 'reason'], void: ['reason'] }
const tab = ref('invoices'), summary = ref({}), rows = ref([]), total = ref(0), page = ref(1)
const filter = reactive({ keyword: '', period: '', state: '', kind: '', sourceState: '' })
const periodLabel = computed(() => filter.period && filter.period !== dayjs().format('YYYY-MM') ? filter.period : '')
const loading = ref(false), busy = ref(false), sourceKind = ref('invoice'), filePicker = ref(null), selected = ref([])
const syncStatus = ref(null)
const dialog = ref(false), action = ref(''), target = ref(null), form = ref({})
const visibleFields = computed(() => (formFields[action.value] || []).filter((field) => action.value !== 'receipt' || form.value.kind === 'contract' || !['contract_no', 'payment_id'].includes(field)))
const allocations = ref([]), invoiceOptions = ref([]), invoicePage = ref(1), invoiceCount = ref(0)
const detailOpen = ref(false), detailFields = ref({}), detailType = ref(''), detailAllocations = ref([])
const isApplicable = (row) => ['pending', 'error'].includes(row.state)
let requestNo = 0

function fromRoute() {
  tab.value = route.query.tab === 'receipts' ? 'receipts' : 'invoices'
  filter.period = typeof route.query.period === 'string' ? route.query.period : ''
  filter.state = route.query.state === 'outstanding' ? 'outstanding' : ''
  filter.kind = route.query.kind === 'non_contract' ? 'non_contract' : ''
  filter.keyword = typeof route.query.keyword === 'string' ? route.query.keyword : ''
  search()
}
watch(() => route.query, fromRoute)
onMounted(fromRoute)

async function loadRows() {
  const seq = ++requestNo, current = tab.value
  const params = current === 'sources' ? { state: filter.sourceState } : {
    keyword: filter.keyword, period: filter.period || undefined, ...(current === 'invoices' ? { state: filter.state } : { kind: filter.kind }),
  }
  loading.value = true
  try {
    const r = await api.get(`${prefix}/${current}`, { ...params, page: page.value })
    if (seq === requestNo) { rows.value = r.items; total.value = r.total }
  } catch { /* API 客户端提示错误 */ } finally { if (seq === requestNo) loading.value = false }
}
async function loadSummary() {
  const period = filter.period
  try { const result = await api.get(`${prefix}/summary`, { period: period || undefined }); if (filter.period === period) summary.value = result } catch { /* 保留上次结果 */ }
}
function refresh() {
  if (tab.value === 'sources') api.get(`${prefix}/sources/status`).then((value) => { syncStatus.value = value }).catch(() => {})
  return Promise.all([loadRows(), loadSummary()])
}
function search() { page.value = 1; return refresh() }
function switchTab() { rows.value = []; total.value = 0; selected.value = []; search() }
function open(name, row = null) {
  action.value = name; target.value = row
  const contractNo = typeof route.query.contract_no === 'string' ? route.query.contract_no : undefined
  form.value = name === 'receipt' ? { kind: 'contract', contract_no: contractNo }
    : name === 'invoice' ? { contract_no: contractNo } : name === 'issue' ? { amount: row.requested_amount } : {}
  dialog.value = true
}
async function openAllocation(row) {
  try {
    target.value = row; invoicePage.value = 1; invoiceOptions.value = []
    const existing = await api.get(`${prefix}/receipts/${row.id}/allocations`)
    allocations.value = existing.map(({ invoice_id, amount }) => ({ invoice_id, amount }))
    invoiceOptions.value = existing.map((item) => ({ id: item.invoice_id, invoice_no: item.invoice_no, outstanding: null }))
    await loadInvoices(); action.value = 'allocate'; dialog.value = true
  } catch { /* API 客户端提示错误 */ }
}
async function loadInvoices() {
  const related = target.value.contract_id ? { contract_id: target.value.contract_id }
    : { without_contract: true, customer: target.value.payer }
  const result = await api.get(`${prefix}/invoices`, { ...related, state: 'issued', size: 100, page: invoicePage.value })
  invoiceCount.value = result.total
  invoiceOptions.value = [...new Map([...invoiceOptions.value, ...result.items].map((i) => [i.id, i])).values()]
}
async function detail(type, row) {
  detailType.value = type; detailAllocations.value = []
  const values = type === 'source' ? row.payload : { ...row }
  if (type === 'invoice') values.state = invoiceFilters[row.state]
  if (type === 'receipt') { values.kind = kinds[row.kind]; values.state = row.state === 'void' ? '已撤销' : '已确认' }
  detailFields.value = values; detailOpen.value = true
  if (type === 'receipt') {
    try { detailAllocations.value = await api.get(`${prefix}/receipts/${row.id}/allocations`) } catch { /* API 客户端提示错误 */ }
  }
}
function notifyChanged() {
  window.dispatchEvent(new Event('business-data-changed'))
  try { localStorage.setItem('jc-business-revision', String(Date.now())) } catch { /* 同页仍会刷新 */ }
}
async function save() {
  busy.value = true
  try {
    const id = target.value?.id
    const payload = Object.fromEntries(visibleFields.value.filter((key) => form.value[key] !== '' && form.value[key] != null).map((key) => [key, form.value[key]]))
    const paths = { invoice: '/invoices', issue: `/invoices/${id}/issue`, correct: `/invoices/${id}/correct`, receipt: '/receipts', assign: `/receipts/${id}/assign`, void: `/receipts/${id}/void` }
    if (action.value === 'allocate') await api.put(`${prefix}/receipts/${id}/allocations`, { items: allocations.value })
    else await api.post(prefix + paths[action.value], payload)
    dialog.value = false; ElMessage.success('已更新台账'); notifyChanged(); await refresh()
  } catch { /* API 客户端提示错误 */ } finally { busy.value = false }
}
function downloadTemplate() { api.download(`${prefix}/sources/template/${sourceKind.value}`).catch(() => {}) }
async function upload(event) {
  const file = event.target.files[0]; event.target.value = ''
  if (!file) return
  busy.value = true
  try { const r = await api.upload(`${prefix}/sources/upload/${sourceKind.value}`, file); ElMessage.success(`暂存更新 ${r.changed} 行，请核对后确认`); filter.sourceState = ''; await search() }
  catch { /* API 客户端提示错误 */ } finally { busy.value = false }
}
async function applyRows(items) {
  try { await ElMessageBox.confirm(`确认将所选 ${items.length} 行写入正式台账？`, '确认入账', { type: 'warning' }) } catch { return }
  busy.value = true
  try {
    const result = await api.post(`${prefix}/sources/apply-batch`, { items: items.map(({ id, version }) => ({ id, version })) })
    const applied = result.filter((r) => r.state === 'applied').length
    ElMessage({ type: applied === result.length ? 'success' : 'warning', message: `已入账 ${applied} 行，待核对 ${result.length - applied} 行` })
    if (applied) notifyChanged()
    await refresh()
  } catch { /* API 客户端提示错误 */ } finally { busy.value = false }
}
</script>

<style scoped>
.ledger-filters, .allocation { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; }
.ledger-filters > .el-input { width: min(250px, 100%); }
.ledger-filters > .el-select { width: 160px; }
.allocation > .el-select { flex: 2 1 200px; }
.allocation > .el-input { flex: 1 1 120px; }
.el-pagination { margin-top: 16px; }
</style>
