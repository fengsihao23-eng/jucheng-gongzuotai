<template>
  <div v-if="legacy" class="finance-history">
    <el-button v-if="tabs.length" class="return-ledger" :icon="ArrowLeft" text @click="go(tabs[0][0])">返回财务台账</el-button>
    <component :is="legacyView" />
  </div>
  <div v-else class="jc-page ledger-page" v-loading="loadingSchema">
    <PageHeader :title="route.meta.title" :desc="isExpense ? '汇总银行支出与现金流水，按项目核对费用归属。' : '查阅银行与二维码到账明细，登记实际开票。'">
      <el-button text :icon="Clock" @click="go('history')">历史记录</el-button>
      <el-button v-if="activeLedger?.can_import" type="primary" :icon="Upload" @click="importOpen = true">导入财务账表</el-button>
    </PageHeader>

    <template v-if="activeLedger">
      <nav class="ledger-tabs" aria-label="财务账表">
        <button v-for="[key, item] in tabs" :key="key" type="button" :class="{ active: activeKind === key }"
          :aria-current="activeKind === key ? 'page' : undefined" @click="go(key)">
          <el-icon><component :is="icons[key]" /></el-icon>{{ item.label }}
        </button>
      </nav>

      <div class="ledger-stats" :style="{ '--stat-columns': Object.keys(activeLedger.totals).length + 1 }">
        <div v-for="(label, key) in activeLedger.totals" :key="key" class="ledger-stat">
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
          <el-date-picker v-model="filters.period" type="month" value-format="YYYY-MM" placeholder="所有月份"
            aria-label="按月份筛选" @change="search" />
          <el-select v-if="activeKind !== 'invoice'" v-model="filters.account" clearable filterable
            :placeholder="activeKind === 'bank_expense' ? '全部汇款公司' : '全部账户'" aria-label="按账户筛选" @change="search">
            <el-option v-for="account in result.accounts" :key="account" :value="account" />
          </el-select>
          <el-input v-model="filters.keyword" clearable maxlength="120" :prefix-icon="Search"
            :placeholder="isExpense ? '摘要、申请编号、项目' : '付款方、发票号码、项目'" aria-label="搜索台账"
            @change="search" @keyup.enter="search" />
          <el-checkbox v-if="isExpense" v-model="filters.unassigned" @change="search">待归集支出</el-checkbox>
        </div>
        <el-table :key="activeKind" :data="result.items" row-key="id" :row-class-name="rowClass" stripe>
          <el-table-column v-for="field in mainFields" :key="field.key" :label="field.label"
            :min-width="columnWidth(field)" :align="field.type === 'money' ? 'right' : 'left'" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="field.type === 'money'" class="jc-num cell-amount">{{ money(row.values[field.key]) }}</span>
              <span v-else-if="field.key === 'sequence'">{{ row.values.sequence || row.source.row }}</span>
              <span v-else-if="field.key === 'date' && opening(row)" class="jc-muted">{{ row.values.date || row.period }}</span>
              <span v-else>{{ row.values[field.key] || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" :width="isExpense && canAllocate ? 134 : 96" fixed="right">
            <template #default="{ row }">
              <div class="ledger-actions">
                <el-button link type="primary" @click="selected = row">明细</el-button>
                <el-button v-if="canAllocate && Number(row.values.payment)" link type="primary" @click="openAllocation(row)">归集</el-button>
                <el-button v-if="row.can_delete" link type="danger" @click="openEntryRemoval(row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
          <template #empty>
            <div class="ledger-empty">
              <el-icon><component :is="icons[activeKind]" /></el-icon>
              <strong>{{ hasFilter ? '没有符合筛选条件的记录' : '暂无台账记录' }}</strong>
              <el-button v-if="!hasFilter && activeLedger.can_import" link type="primary" @click="importOpen = true">导入第一份账表</el-button>
            </div>
          </template>
        </el-table>
        <div class="ledger-pagination">
          <el-pagination v-model:current-page="page" :page-size="30" :total="result.total" layout="total, prev, pager, next" @current-change="loadEntries" />
        </div>
      </section>
    </template>

    <FinanceLedgerImport v-if="activeLedger" v-model="importOpen" :kind="activeKind" :ledgers="schema"
      @confirmed="afterImport" />
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
        <el-button plain type="danger" :loading="loadingBatch" @click="openBatchRemoval">撤销本批导入</el-button>
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
      </div>
      <template #footer>
        <el-button :disabled="removing" @click="removalOpen = false">取消</el-button>
        <el-button type="danger" :loading="removing" @click="saveRemoval">{{ removal?.batch ? '确认撤销' : '确认删除' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Clock, Document, Memo, Money, Search, Ticket, Upload, Wallet } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import FinanceLedgerImport from '@/components/finance/FinanceLedgerImport.vue'
import { datetime, money, thousands } from '@/utils/format'

const LegacyExpenses = defineAsyncComponent(() => import('@/views/Expenses.vue'))
const LegacyReceivables = defineAsyncComponent(() => import('@/views/Receivables.vue'))
const icons = { bank_receipt: Wallet, qr_receipt: Ticket, invoice: Document, bank_expense: Money, cash_journal: Memo }
const route = useRoute(), router = useRouter()
const schema = ref({}), canAllocate = ref(false), loadingSchema = ref(true), loadingEntries = ref(false)
const isExpense = computed(() => route.meta.menu === 'expenses')
const tabs = computed(() => Object.entries(schema.value).filter(([, item]) => item.menu === route.meta.menu))
const activeKind = computed(() => tabs.value.find(([key]) => key === route.query.ledger)?.[0] || tabs.value[0]?.[0])
const activeLedger = computed(() => schema.value[activeKind.value])
const legacy = computed(() => route.query.ledger === 'history' || (!route.query.ledger && !isExpense.value && ['tab', 'contract_no', 'state', 'keyword', 'period'].some((key) => key in route.query)) || (!loadingSchema.value && !tabs.value.length))
const legacyView = computed(() => isExpense.value ? LegacyExpenses : LegacyReceivables)
const mainFields = computed(() => activeLedger.value.fields.filter((field) => field.visible))
const importOpen = ref(false), selected = ref(null)
const allocationOpen = ref(false), allocationRow = ref(null), allocationProject = ref(''), allocating = ref(false)
const removal = ref(null), removalOpen = ref(false), removalReason = ref(''), removing = ref(false), loadingBatch = ref(false)
const removalValues = computed(() => removal.value?.row?.values || {})
const removalLabel = computed(() => {
  const { payer, payee, customer, summary, project, account } = removalValues.value
  return payer || payee || customer || summary || project || account
})
const removalAmount = computed(() => {
  const { net, payment, income, balance } = removalValues.value
  return net || payment || income || balance
})
const filters = reactive({ period: '', account: '', keyword: '', unassigned: false })
const emptyResult = () => ({ items: [], accounts: [], total: 0, totals: {} })
const result = ref(emptyResult()), page = ref(1)
const hasFilter = computed(() => filters.period || filters.account || filters.keyword || filters.unassigned)
let requestId = 0

onMounted(async () => {
  try {
    const data = await api.get('/finance-ledgers/schema')
    schema.value = data.ledgers
    canAllocate.value = data.can_allocate
  } catch { /* API client reports the error. */ }
  finally { loadingSchema.value = false }
})

watch([activeKind, legacy], ([kind, history]) => {
  filters.account = ''
  filters.keyword = ''
  filters.unassigned = false
  result.value = emptyResult()
  requestId++
  if (kind && !history) search()
}, { immediate: true })

function go(kind) {
  if (kind !== activeKind.value || legacy.value) router.push({ query: { ledger: kind } })
}

function search() {
  page.value = 1
  return loadEntries()
}

async function loadEntries() {
  if (!activeKind.value || legacy.value) return
  const request = ++requestId
  loadingEntries.value = true
  try {
    const data = await api.get(`/finance-ledgers/entries/${activeKind.value}`, {
      period: filters.period || '', account: filters.account, keyword: filters.keyword,
      project: filters.unassigned ? 'unassigned' : '', page: page.value,
    })
    if (request === requestId) result.value = data
  } catch { /* API client reports the error. */ }
  finally { if (request === requestId) loadingEntries.value = false }
}

function columnWidth(field) {
  if (field.type === 'money' || field.type === 'date') return 112
  return { sequence: 55, summary: 168, project: 130, note: 120, account: 120, approval_no: 133, applicant: 86 }[field.key] || 128
}

function opening(row) {
  return row.kind === 'cash_journal' && !Number(row.values.income) && !Number(row.values.payment)
}

function rowClass({ row }) {
  return opening(row) ? 'opening-balance-row' : ''
}

async function afterImport(receipt) {
  const kinds = Object.keys(receipt.inserted_by_kind)
  if (!kinds.length) return
  filters.period = ''
  filters.account = ''
  filters.keyword = ''
  filters.unassigned = false
  if (kinds.includes(activeKind.value)) await search()
  else router.push({ name: schema.value[kinds[0]].menu, query: { ledger: kinds[0] } })
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
  removalOpen.value = true
}

async function openBatchRemoval() {
  const row = selected.value
  loadingBatch.value = true
  try {
    const token = row.source.batch
    const data = await api.get(`/finance-ledgers/imports/${token}/revoke`)
    if (selected.value?.id !== row.id) return
    if (!data.total) {
      selected.value = null
      ElMessage.info('本批记录已撤销')
      await search()
      return
    }
    removal.value = { ...data, batch: token }
    removalReason.value = ''
    removalOpen.value = true
  } catch { /* API client reports the error. */ }
  finally { loadingBatch.value = false }
}

async function saveRemoval() {
  const target = removal.value
  const body = { reason: removalReason.value }
  removing.value = true
  try {
    const result = target.batch
      ? await api.post(`/finance-ledgers/imports/${target.batch}/revoke`, body)
      : await api.del(`/finance-ledgers/entries/${target.row.id}`, undefined, { data: body })
    removalOpen.value = false
    if (target.batch) selected.value = null
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
.ledger-stats { display: grid; grid-template-columns: repeat(var(--stat-columns), minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
.ledger-stat { min-width: 0; display: grid; gap: 7px; padding: 15px 19px; border: 1px solid var(--jc-line); background: var(--jc-surface); border-radius: 10px; }
.ledger-stat span { font-size: 12px; color: var(--jc-ink-2); }
.ledger-stat strong { color: var(--jc-brand); font-size: clamp(16px, 2.2vw, 27px); line-height: 1.25; overflow-wrap: anywhere; }
.ledger-stat small { font-size: 12px; color: var(--jc-ink-3); }
.ledger-sheet { background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 10px; overflow: hidden; }
.ledger-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 14px; }
.ledger-toolbar :is(.el-input, .el-select) { width: 220px; }
.ledger-toolbar :deep(.el-date-editor) { width: 144px; }
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
  .ledger-toolbar :deep(.el-date-editor) { width: 100%; }
}
</style>
