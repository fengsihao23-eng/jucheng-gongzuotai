<template>
  <div v-if="showReconciliation" class="finance-reconciliation">
    <el-button v-if="!isJournal || tabs.length" class="return-ledger" :icon="ArrowLeft" text @click="go(tabs[0]?.[0])">返回账表</el-button>
    <Journal v-if="isJournal" />
    <Receivables v-else />
  </div>
  <div v-else class="jc-page ledger-page" v-loading="loadingSchema">
    <PageHeader :title="route.meta.title">
      <el-button v-if="isJournal" text @click="go('reconciliation')">流水核对与补录</el-button>
      <el-button v-if="tabs.length" text :icon="Clock" @click="openHistory">导入记录</el-button>
      <el-button v-if="activeLedger?.can_import" type="primary" :icon="Upload" @click="openImport">导入财务账表</el-button>
    </PageHeader>

    <template v-if="activeLedger">
      <nav class="ledger-tabs" aria-label="财务账表">
        <button v-for="[key, item] in tabs" :key="key" type="button" :class="{ active: activeTab === key }"
          :aria-current="activeTab === key ? 'page' : undefined" @click="go(key)">
          <el-icon><component :is="icons[key]" /></el-icon>{{ key === 'bank_expense' ? '银行日记账' : item.label }}
        </button>
      </nav>

      <div v-if="isJournal" class="ledger-directions" role="group" aria-label="收支方向">
        <button v-for="[value, label] in directionOptions" :key="value" type="button" :aria-pressed="activeDirection === value"
          @click="changeDirection(value)">{{ label }}</button>
      </div>

      <div class="ledger-stats" :style="{ '--stat-columns': Object.keys(visibleTotals).length + 1 }">
        <div v-for="(label, key) in visibleTotals" :key="key" class="ledger-stat">
          <span>{{ label }}</span>
          <strong class="jc-num">{{ money(result.totals?.[key] || '0.00') }}</strong>
        </div>
        <div class="ledger-stat">
          <span>筛选记录</span>
          <strong class="jc-num">{{ thousands(result.total) }} <small>笔</small></strong>
        </div>
      </div>

      <section class="ledger-sheet" v-loading="loadingEntries" :aria-label="activeLedger.label">
        <div class="ledger-toolbar">
          <el-date-picker v-model="filters.year" type="year" value-format="YYYY" format="YYYY [年]" placeholder="全部年份"
            aria-label="按年份筛选" @change="changeYear" />
          <el-select v-model="filters.month" placeholder="全年" :disabled="!filters.year"
            aria-label="按月份筛选" class="ledger-month" @change="search">
            <el-option label="全年" value="" />
            <el-option v-for="month in months" :key="month" :label="`${Number(month)} 月`" :value="month" />
          </el-select>
          <el-select v-if="isJournal && (activeKind !== 'cash_journal' || result.accounts.length)" v-model="filters.account" clearable filterable
            :placeholder="accountLabels[activeKind]" aria-label="按账户筛选" @change="search">
            <el-option v-for="account in result.accounts" :key="account" :value="account" />
          </el-select>
          <el-input v-model="filters.keyword" clearable maxlength="120" :prefix-icon="Search"
            :placeholder="searchLabels[activeKind]" aria-label="搜索台账"
            @change="search" @keyup.enter="search" />
          <el-checkbox v-if="showsExpense" v-model="filters.unassigned" @change="search">待归集支出</el-checkbox>
        </div>
        <el-table :key="activeKind" :data="result.items" row-key="id" :row-class-name="rowClass" stripe @row-contextmenu="entryMenu">
          <el-table-column v-for="field in mainFields" :key="field.key" :label="field.label"
            :min-width="columnWidth(field)" :align="field.type === 'money' ? 'right' : 'left'" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="field.type === 'money'" class="jc-num cell-amount">{{ money(row.values[field.key]) }}</span>
              <span v-else-if="field.key === 'sequence'">{{ row.values.sequence || row.source.row }}</span>
              <span v-else-if="field.key === 'date' && opening(row)" class="jc-muted">{{ row.values.date || row.period }}</span>
              <span v-else>{{ row.values[field.key] || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" :width="showsExpense && canAllocate ? 134 : 96" fixed="right">
            <template #default="{ row }">
              <div class="ledger-actions">
                <el-button link type="primary" @click="selected = row">明细</el-button>
                <el-button v-if="showsExpense && canAllocate && Number(row.values.payment)" link type="primary" @click="openAllocation(row)">归集</el-button>
                <el-button v-if="row.can_delete" link type="danger" @click="openEntryRemoval(row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
          <template #empty>
            <div class="ledger-empty">
              <el-icon><component :is="icons[activeKind]" /></el-icon>
              <strong>{{ hasFilter ? '没有符合筛选条件的记录' : '暂无台账记录' }}</strong>
              <el-button v-if="!hasFilter && activeLedger.can_import" link type="primary" @click="openImport">导入第一份账表</el-button>
            </div>
          </template>
        </el-table>
        <div class="ledger-pagination">
          <el-pagination v-model:current-page="page" :page-size="30" :total="result.total" layout="total, prev, pager, next" @current-change="loadEntries" />
        </div>
      </section>
    </template>

    <el-dialog v-model="historyOpen" title="导入记录" width="min(920px, 95vw)" :close-on-click-modal="false">
      <el-table v-loading="historyLoading" :data="history.items" row-key="id" empty-text="这里会显示本模块的导入批次和待确认预览" stripe @row-contextmenu="historyMenu">
        <el-table-column label="文件名" prop="filename" min-width="160" show-overflow-tooltip />
        <el-table-column label="台账" min-width="145">
          <template #default="{ row }">{{ row.kinds.map((kind) => schema[kind]?.label).join('、') }}</template>
        </el-table-column>
        <el-table-column label="操作时间" min-width="142">
          <template #default="{ row }">{{ datetime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="上传人" prop="uploader" min-width="78" show-overflow-tooltip />
        <el-table-column label="状态" min-width="112">
          <template #default="{ row }">{{ historyStatus[row.status] }}<span v-if="row.status !== 'draft'" class="history-count">{{ row.active }} / {{ row.inserted }} 笔</span></template>
        </el-table-column>
        <el-table-column label="操作" min-width="100" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'draft'" link type="primary" @click="resumeImport(row)">继续核对</el-button>
            <el-button v-if="row.can_revoke" link type="danger" :disabled="loadingBatch" @click="openBatchRemoval(row.id)">撤销本批</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="history.total > 20" v-model:current-page="historyPage" :page-size="20" :total="history.total"
        layout="total, prev, pager, next" class="ledger-pagination" @current-change="loadHistory" />
    </el-dialog>
    <FinanceLedgerImport v-if="activeLedger" v-model="importOpen" :kind="importKind" :ledgers="schema"
      :resume-id="resumeId" @confirmed="afterImport" @history="historyAfterImport" @finished="openImportedLedger" />
    <el-dialog :model-value="!!selected" :title="selected ? schema[selected.kind]?.label + ' · 明细' : '账表明细'"
      width="min(610px, 94vw)" @close="selected = null">
      <template v-if="selected">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item v-for="field in schema[selected.kind].fields" :key="field.key" :label="field.label">
            {{ field.type === 'money' ? money(selected.values[field.key]) : selected.values[field.key] || '—' }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="ledger-source"><b>导入来源</b><span>{{ selected.source.filename }} · {{ selected.source.sheet }} · 第 {{ selected.source.row }} 行</span>
          <span>{{ datetime(selected.source.imported_at) }}</span></div>
      </template>
      <template v-if="selected?.can_delete" #footer>
        <el-button plain type="danger" :loading="loadingBatch" @click="openBatchRemoval(selected.source.batch)">撤销本批导入</el-button>
        <el-button @click="selected = null">关闭</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="allocationOpen" title="归集支出" width="min(440px, 94vw)" :close-on-click-modal="false">
      <p v-if="allocationRow" class="allocation-info">{{ allocationRow.values.summary || allocationRow.values.payee || allocationRow.values.counterparty }} · {{ money(allocationRow.values.payment) }}</p>
      <el-input v-model="allocationProject" maxlength="128" clearable aria-label="费用归属"
        placeholder="输入项目名称，或填写公共费用" />
      <template #footer>
        <el-button @click="allocationOpen = false">取消</el-button>
        <el-button type="primary" :loading="allocating" @click="saveAllocation">保存归属</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="removalOpen" :title="removal?.batch ? '撤销整批导入' : '删除台账记录'" width="min(485px, 94vw)"
      append-to-body :close-on-click-modal="false" :show-close="!removing" :close-on-press-escape="!removing">
      <div v-if="removal" class="removal-body">
        <template v-if="removal.batch">
          <p>本次撤销将移除该批次中仍在台账里的 <strong>{{ removal.total }} 笔</strong>记录。</p>
          <div class="removal-summary">
            <strong>{{ removal.filename }}</strong>
            <span v-for="(count, kind) in removal.by_kind" :key="kind">{{ schema[kind]?.label }} · {{ count }} 笔</span>
          </div>
        </template>
        <template v-else>
          <p>请核对要删除的这笔记录。</p>
          <div class="removal-summary">
            <strong>{{ schema[removal.row.kind].label }} · {{ removalLabel }}</strong>
            <span>{{ removal.row.values.date || removal.row.period }} · {{ money(removalAmount) }}</span>
            <span>{{ removal.row.source.filename }} · 第 {{ removal.row.source.row }} 行</span>
          </div>
        </template>
        <p class="jc-muted">确认后，台账合计会更新。操作留痕，可重新导入正确的数据。</p>
        <label class="removal-reason"><span>原因（选填）</span>
          <el-input v-model="removalReason" type="textarea" :rows="2" maxlength="200" aria-label="原因（选填）" />
        </label>
        <label class="removal-reason"><span>删除密码</span>
          <el-input v-model="removalPassword" type="password" show-password autocomplete="off" maxlength="128"
            aria-label="删除密码" @keyup.enter="saveRemoval" />
        </label>
      </div>
      <template #footer>
        <el-button :disabled="removing" @click="removalOpen = false">取消</el-button>
        <el-button type="danger" :loading="removing" :disabled="!removalPassword" @click="saveRemoval">{{ removal?.batch ? '确认撤销' : '确认删除' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { rowDeletion } from '@/composables/useRecordDeletion'
import { computed, defineAsyncComponent, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Clock, Document, Memo, Money, Search, Upload, Wallet } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import FinanceLedgerImport from '@/components/finance/FinanceLedgerImport.vue'
import { datetime, money, thousands } from '@/utils/format'

const Journal = defineAsyncComponent(() => import('@/views/Journal.vue'))
const Receivables = defineAsyncComponent(() => import('@/views/Receivables.vue'))
const icons = { invoice: Document, collection_receipt: Wallet, bank_receipt: Money, bank_expense: Money, cash_journal: Memo }
const ledgerOrder = ['invoice', 'collection_receipt', 'bank_expense', 'cash_journal']
const movements = [['income', '收入'], ['expense', '支出']]
const accountLabels = { bank_receipt: '全部收款银行', bank_expense: '全部汇款公司', cash_journal: '全部现金账户' }
const searchLabels = {
  invoice: '客户、项目名称、开票单位',
  collection_receipt: '名称、开票单位、备注',
  bank_receipt: '汇款公司、摘要、项目',
  bank_expense: '摘要、审批编号、项目',
  cash_journal: '对方科目、摘要、申请编号',
}
const months = Array.from({ length: 12 }, (_, index) => String(index + 1).padStart(2, '0'))
const historyStatus = { draft: '待确认', confirmed: '已完成', partial: '部分移除', removed: '已移除' }
const route = useRoute(), router = useRouter()
const schema = ref({}), canAllocate = ref(false), loadingSchema = ref(true), loadingEntries = ref(false)
const isJournal = computed(() => route.meta.menu === 'journal')
const tabs = computed(() => ledgerOrder.filter((kind) => schema.value[kind]?.menu === route.meta.menu).map((kind) => [kind, schema.value[kind]]))
const activeKind = computed(() => schema.value[route.query.ledger]?.menu === route.meta.menu ? route.query.ledger : tabs.value[0]?.[0])
const activeTab = computed(() => activeKind.value === 'bank_receipt' ? 'bank_expense' : activeKind.value)
const activeLedger = computed(() => schema.value[activeKind.value])
const isCash = computed(() => activeKind.value === 'cash_journal')
const cashDirection = computed(() => isCash.value && movements.some(([value]) => value === route.query.direction) ? route.query.direction : '')
const directionOptions = computed(() => isCash.value ? [['', '全部'], ...movements] : movements)
const activeDirection = computed(() => isCash.value ? cashDirection.value : activeKind.value === 'bank_receipt' ? 'income' : 'expense')
const showsExpense = computed(() => activeKind.value === 'bank_expense' || (isCash.value && cashDirection.value !== 'income'))
const visibleTotals = computed(() => {
  const totals = activeLedger.value.totals
  if (cashDirection.value === 'income') return { inflow: totals.inflow }
  if (cashDirection.value === 'expense') return { outflow: totals.outflow }
  return totals
})
const showReconciliation = computed(() => isJournal.value
  ? route.query.ledger === 'reconciliation' || Boolean(route.query.new) || (!loadingSchema.value && !tabs.value.length)
  : !route.query.ledger && ['tab', 'contract_no', 'state', 'keyword', 'period'].some((key) => key in route.query))
const mainFields = computed(() => activeLedger.value.fields.filter((field) => field.visible))
const importOpen = ref(false), importKind = ref(''), resumeId = ref(''), importedKind = ref(''), selected = ref(null)
const historyOpen = ref(false), historyLoading = ref(false), historyPage = ref(1)
const history = ref({ items: [], total: 0 })
const allocationOpen = ref(false), allocationRow = ref(null), allocationProject = ref(''), allocating = ref(false)
const removal = ref(null), removalOpen = ref(false), removalReason = ref(''), removing = ref(false), loadingBatch = ref(false)
const removalPassword = ref('')
const entryMenu = rowDeletion(row => row.can_delete && { open: () => openEntryRemoval(row) })
const historyMenu = rowDeletion(row => row.can_revoke && { menuLabel: '撤销本批', open: () => openBatchRemoval(row.id) })
const removalValues = computed(() => removal.value?.row?.values || {})
const removalLabel = computed(() => {
  const { payer, payee, customer, name, summary, project, account } = removalValues.value
  return payer || payee || customer || name || summary || project || account
})
const removalAmount = computed(() => {
  const { net, payment, income, balance } = removalValues.value
  return net || payment || income || balance
})
const filters = reactive({ year: '', month: '', account: '', keyword: '', unassigned: false })
const emptyResult = () => ({ items: [], accounts: [], total: 0, totals: {} })
const result = ref(emptyResult()), page = ref(1)
const hasFilter = computed(() => filters.year || filters.account || filters.keyword || filters.unassigned)
let requestId = 0

onMounted(async () => {
  try {
    const data = await api.get('/finance-ledgers/schema')
    schema.value = data.ledgers
    canAllocate.value = data.can_allocate
    if (route.query.ledger === 'history' && tabs.value.length) openHistory()
  } catch { /* API client reports the error. */ }
  finally { loadingSchema.value = false }
})

watch([activeKind, cashDirection, showReconciliation], ([kind, , reconciliation], [previousKind]) => {
  if (kind !== previousKind) {
    filters.account = ''
    filters.keyword = ''
    filters.unassigned = false
  }
  if (!showsExpense.value) filters.unassigned = false
  result.value = emptyResult()
  requestId++
  if (kind && !reconciliation) search()
}, { immediate: true })

function go(kind, direction = '') {
  if (kind !== activeKind.value || direction !== cashDirection.value || showReconciliation.value) {
    const query = kind ? { ledger: kind } : {}
    if (direction) query.direction = direction
    router.push({ query })
  }
}

function changeDirection(direction) {
  if (isCash.value) go('cash_journal', direction)
  else go(direction === 'income' ? 'bank_receipt' : 'bank_expense')
}

function changeYear() {
  filters.month = ''
  return search()
}

function openImport() {
  resumeId.value = ''
  importedKind.value = ''
  importKind.value = activeKind.value
  importOpen.value = true
}

function openHistory() {
  historyPage.value = 1
  historyOpen.value = true
  return loadHistory()
}

async function loadHistory() {
  historyLoading.value = true
  try {
    history.value = await api.get('/finance-ledgers/imports', { menu: route.meta.menu, page: historyPage.value })
  } catch { /* API client reports the error. */ }
  finally { historyLoading.value = false }
}

function resumeImport(row) {
  historyOpen.value = false
  importedKind.value = ''
  importKind.value = row.kinds[0]
  resumeId.value = row.id
  importOpen.value = true
}

function search() {
  page.value = 1
  return loadEntries()
}

async function loadEntries() {
  if (!activeKind.value || showReconciliation.value) return
  const request = ++requestId
  loadingEntries.value = true
  try {
    const data = await api.get(`/finance-ledgers/entries/${activeKind.value}`, {
      period: filters.year && filters.month ? `${filters.year}-${filters.month}` : (filters.year || ''),
      account: filters.account, keyword: filters.keyword,
      project: filters.unassigned ? 'unassigned' : '', direction: cashDirection.value || undefined, page: page.value,
    })
    if (request === requestId) result.value = data
  } catch { /* API client reports the error. */ }
  finally { if (request === requestId) loadingEntries.value = false }
}

function columnWidth(field) {
  if (field.type === 'money' || field.type === 'date') return 112
  return { sequence: 55, summary: 168, project: 160, name: 270, note: 120, account: 120, approval_no: 133, applicant: 86 }[field.key] || 128
}

function opening(row) {
  return row.cash_category === 'opening'
}

function rowClass({ row }) {
  return opening(row) ? 'opening-balance-row' : ''
}

async function afterImport(receipt) {
  const kinds = Object.keys(receipt.inserted_by_kind)
  if (!kinds.length) return
  filters.year = ''
  filters.month = ''
  filters.account = ''
  filters.keyword = ''
  filters.unassigned = false
  if (kinds.includes(activeKind.value)) {
    if (cashDirection.value) await router.replace({ query: { ledger: 'cash_journal' } })
    else await search()
  }
  else importedKind.value = kinds[0]
}

function openImportedLedger() {
  const kind = importedKind.value
  if (kind) router.push({ name: schema.value[kind].menu, query: { ledger: kind } })
}

function historyAfterImport() {
  importedKind.value = ''
  openHistory()
}

function openAllocation(row) {
  allocationRow.value = row
  allocationProject.value = row.values.project || ''
  allocationOpen.value = true
}

async function saveAllocation() {
  allocating.value = true
  try {
    await api.patch(`/finance-ledgers/entries/${allocationRow.value.id}/project`, { project: allocationProject.value })
    allocationOpen.value = false
    ElMessage.success('已保存费用归属')
    await loadEntries()
  } catch { /* API client reports the error. */ }
  finally { allocating.value = false }
}

function openEntryRemoval(row) {
  removal.value = { row }
  removalReason.value = ''
  removalPassword.value = ''
  removalOpen.value = true
}

async function openBatchRemoval(token) {
  loadingBatch.value = true
  try {
    const data = await api.get(`/finance-ledgers/imports/${token}/revoke`)
    if (!data.total) {
      selected.value = null
      ElMessage.info('本批记录已撤销')
      if (historyOpen.value) await loadHistory()
      await search()
      return
    }
    removal.value = { ...data, batch: token }
    removalReason.value = ''
    removalPassword.value = ''
    removalOpen.value = true
  } catch { /* API client reports the error. */ }
  finally { loadingBatch.value = false }
}

async function saveRemoval() {
  if (!removalPassword.value || removing.value) return
  const target = removal.value
  const body = { reason: removalReason.value, password: removalPassword.value }
  removing.value = true
  try {
    const result = target.batch
      ? await api.post(`/finance-ledgers/imports/${target.batch}/revoke`, body)
      : await api.del(`/finance-ledgers/entries/${target.row.id}`, undefined, { data: body })
    removalOpen.value = false
    removalPassword.value = ''
    if (target.batch) selected.value = null
    if (historyOpen.value) await loadHistory()
    ElMessage.success(result.removed ? `已移除 ${result.removed} 笔台账记录` : '记录已移除')
    await search()
  } catch { /* API client reports the error. */ }
  finally { removing.value = false }
}
</script>

<style scoped>
.ledger-page :deep(.ph) { margin-bottom: 24px; }
.ledger-tabs { display: flex; gap: 24px; border-bottom: 1px solid var(--jc-line); margin-bottom: 18px; overflow: auto; }
.ledger-tabs button { display: inline-flex; gap: 8px; align-items: center; padding: 9px 4px 12px; color: var(--jc-ink-2); background: transparent; border: 0; border-bottom: 2px solid transparent; white-space: nowrap; font: inherit; cursor: pointer; }
.ledger-tabs button.active { color: var(--jc-brand); border-bottom-color: var(--jc-brand); font-weight: 650; }
.ledger-tabs button:hover { color: var(--jc-brand); }
.ledger-directions { display: inline-flex; gap: 3px; margin-bottom: 14px; padding: 3px; border: 1px solid var(--jc-line); border-radius: 8px; background: var(--jc-surface-2); }
.ledger-directions button { min-width: 66px; min-height: 30px; padding: 3px 12px; border: 0; border-radius: 5px; background: transparent; color: var(--jc-ink-2); font: inherit; cursor: pointer; }
.ledger-directions button[aria-pressed="true"] { background: var(--jc-brand); color: white; font-weight: 600; }
.ledger-directions button:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 2px; }
.ledger-stats { display: grid; grid-template-columns: repeat(var(--stat-columns), minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
.ledger-stat { min-width: 0; display: grid; gap: 7px; padding: 15px 19px; border: 1px solid var(--jc-line); background: var(--jc-surface); border-radius: 10px; }
.ledger-stat span { font-size: 12px; color: var(--jc-ink-2); }
.ledger-stat strong { color: var(--jc-brand); font-size: clamp(16px, 2.2vw, 27px); line-height: 1.25; overflow-wrap: anywhere; }
.ledger-stat small { font-size: 12px; color: var(--jc-ink-3); }
.ledger-sheet { background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 10px; overflow: hidden; }
.ledger-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 14px; }
.ledger-toolbar :is(.el-input, .el-select) { width: 220px; }
.ledger-toolbar :deep(.el-date-editor) { width: 135px; }
.ledger-toolbar .ledger-month { width: 95px; }
.history-count { display: block; font-size: 12px; color: var(--jc-ink-3); }
.ledger-sheet :deep(.el-table) { --el-table-header-bg-color: #eef2f9; --el-table-header-text-color: #37456a; }
.ledger-sheet :deep(.el-table__cell) { padding: 9px 0; }
.ledger-sheet :deep(.opening-balance-row) { --el-table-tr-bg-color: #f1f6f2; color: var(--jc-up); }
.cell-amount { white-space: nowrap; font-size: 14px; }
.ledger-actions { display: flex; align-items: center; gap: 8px; }
.ledger-actions .el-button + .el-button { margin-left: 0; }
.ledger-pagination { display: flex; justify-content: flex-end; padding: 12px 16px; }
.ledger-empty { display: grid; justify-items: center; padding: 44px 14px; gap: 10px; line-height: 1.4; }
.ledger-empty .el-icon { background: var(--jc-brand-50); color: var(--jc-brand); border-radius: 10px; width: 44px; height: 44px; font-size: 21px; }
.ledger-empty strong { color: var(--jc-ink-2); font-size: 14px; }
.ledger-source { display: grid; gap: 5px; margin-top: 15px; color: var(--jc-ink-2); font-size: 13px; overflow-wrap: anywhere; }
.allocation-info { margin-top: 0; }
.removal-body { display: grid; gap: 12px; }
.removal-body p { margin: 0; }
.removal-summary { display: grid; gap: 5px; padding: 12px; border: 1px solid var(--jc-line); border-radius: 7px; background: var(--jc-surface-2); overflow-wrap: anywhere; }
.removal-reason { display: grid; gap: 6px; }
.return-ledger { margin: 16px var(--jc-page-pad) 0; }
@media (max-width: 640px) {
  .ledger-tabs { gap: 14px; }
  .ledger-stat { padding: 12px 9px; }
  .ledger-stats { gap: 7px; }
  .ledger-toolbar :is(.el-input, .el-select) { width: 100%; }
  .ledger-toolbar :deep(.el-date-editor), .ledger-toolbar .ledger-month { width: calc(50% - 5px); }
}
</style>
