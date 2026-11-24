<!-- FR-05-01 合同台账：筛选（读取 route.query 预置，支持看板下钻）+ 合计 + 导出（与筛选一致）+ 新增/编辑 + 来源标记 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="台账中心" title="合同台账" desc="查看签约、回款和企微来源；导出结果与当前筛选一致。">
      <el-button v-if="perms.workbook" @click="openWorkbook()">企业微信同步</el-button>
      <el-button v-if="perms.workbook" @click="reconciliationOpen = true">来源对账</el-button>
      <el-button v-if="perms.export" :loading="exporting" @click="doExport">导出 xlsx</el-button>
      <el-button v-if="perms.write" type="primary" @click="openNew">新增合同</el-button>
    </PageHeader>

    <div class="jc-grid c4 signing-cards" aria-label="签约统计">
      <button v-for="item in signing" :key="item.key" type="button" :aria-pressed="f.sign_period === item.key" @click="choosePeriod(item.key)">
        <span>{{ periodLabel(item.key) }}</span><b class="jc-num">{{ money(item.amount) }}</b><small>{{ item.count }} 份合同 · 元</small><small>{{ item.start || '历史起始' }} 至 {{ item.end }}</small>
      </button>
    </div>
    <p v-if="signing.length" class="jc-muted jc-small">{{ signingNote }}</p>
    <Panel>
      <div class="filters">
        <el-select v-model="f.sign_year" aria-label="签订年份" placeholder="全部年份" style="width: 145px" @change="changeYear"><el-option :value="''" label="全部年份" /><el-option v-for="year in years" :key="year" :value="year" :label="`${year} 年`" /></el-select>
        <el-input v-model="f.keyword" placeholder="合同号 / 名称 / 客户 / 项目 / 审批单号" clearable style="width: 260px" @keyup.enter="search" @clear="search" />
        <el-select v-model="f.business_line" placeholder="业务线" clearable style="width: 140px" @change="search">
          <el-option v-for="(n, k) in BUSINESS_LINES" :key="k" :label="n" :value="k" />
        </el-select>
        <el-select v-model="f.region" placeholder="区域" clearable style="width: 110px" @change="search">
          <el-option v-for="r in REGIONS" :key="r" :label="r" :value="r" />
        </el-select>
        <el-input v-model="f.customer" placeholder="客户" clearable style="width: 160px" @keyup.enter="search" @clear="search" />
        <el-select v-model="f.status" placeholder="状态" clearable style="width: 110px" @change="search">
          <el-option v-for="(n, k) in CONTRACT_STATUS" :key="k" :label="n" :value="Number(k)" />
        </el-select>
        <el-select v-model="f.owner_id" clearable filterable placeholder="负责人" style="width: 134px" @change="search">
          <el-option v-for="u in owners" :key="u.id" :label="u.name" :value="u.id" />
        </el-select>
        <el-date-picker v-model="f.sign_month" type="month" value-format="YYYY-MM" placeholder="签订月份" style="width: 130px" @change="v => { if (v) f.sign_year = Number(v.slice(0, 4)); f.sign_period = 'all'; search() }" />
        <el-select v-model="f.source_type" placeholder="来源" clearable style="width: 120px" @change="search">
          <el-option v-for="(n, k) in SOURCES" :key="k" :label="n" :value="Number(k)" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>
      <div v-if="extraTags.length" class="jc-row wrap mt-8">
        <span class="jc-muted jc-small">来自下钻：</span>
        <el-tag v-for="t in extraTags" :key="t.key" closable @close="dropExtra(t.key)">{{ t.label }}</el-tag>
      </div>
    </Panel>

    <div class="jc-grid c4 mt-16 sums">
      <div class="s"><span class="jc-muted">合同数</span><b class="jc-num">{{ sums.count ?? 0 }}</b></div>
      <div class="s"><span class="jc-muted">合同金额</span><b class="jc-num">{{ money(sums.amount, 0) }}</b></div>
      <div class="s"><span class="jc-muted">已回款</span><b class="jc-num">{{ money(sums.received, 0) }}</b></div>
      <div class="s" v-if="f.paid_month"><span class="jc-muted">{{ f.paid_month }} 回款</span><b class="jc-num jc-up">{{ money(sums.period_paid, 0) }}</b></div>
      <div class="s" v-else><span class="jc-muted">未回款</span><b class="jc-num">{{ money(sums.receivable, 0) }}</b></div>
    </div>

    <Panel class="mt-16" :padding="false" :loading="loading">
      <el-table :data="rows" class="clickable" @row-click="(r) => $router.push(`/contracts/${r.id}`)" @row-contextmenu="contractMenu">
        <el-table-column label="合同编号" width="130"><template #default="{ row }"><span class="jc-mono">{{ row.contract_no }}</span></template></el-table-column>
        <el-table-column label="合同名称 / 客户" min-width="260">
          <template #default="{ row }"><div class="jc-ellipsis"><b>{{ row.name || '—' }}</b></div><div class="jc-muted jc-small jc-ellipsis">{{ row.customer_name }}</div></template>
        </el-table-column>
        <el-table-column label="业务线" width="110"><template #default="{ row }">{{ row.business_line_name }}</template></el-table-column>
        <el-table-column label="区域" width="90"><template #default="{ row }">{{ row.region || '—' }}</template></el-table-column>
        <el-table-column label="金额" width="140" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.amount, 0) }}</span></template></el-table-column>
        <el-table-column label="签订日期" width="110"><template #default="{ row }">{{ row.sign_date }}</template></el-table-column>
        <el-table-column v-if="f.paid_month" :label="`${f.paid_month} 回款`" width="130" align="right">
          <template #default="{ row }"><span class="jc-num">{{ money(row.period_paid, 0) }}</span></template>
        </el-table-column>
        <el-table-column label="回款进度" width="150">
          <template #default="{ row }">
            <div class="pg"><i :style="{ width: Math.min(100, (row.received_rate || 0) * 100) + '%' }" /></div>
            <span class="jc-small jc-muted">{{ percent(row.received_rate) }} · {{ wan(row.received) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="负责人" width="90"><template #default="{ row }">{{ row.owner_name || '—' }}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }"><StatusPill :text="row.status_name" :tone="row.status === 2 ? 'up' : row.status === 3 ? 'down' : 'info'" /></template></el-table-column>
        <el-table-column label="来源" width="150">
          <template #default="{ row }">
            <StatusPill :text="row.source_name" :tone="row.source_type === 1 ? 'brand' : ''" />
            <div v-if="row.source_approval_no" class="jc-mono jc-small jc-muted">{{ row.source_approval_no }}</div>
          </template>
        </el-table-column>
        <el-table-column v-if="perms.write" label="" width="70" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button></template>
        </el-table-column>
      </el-table>
    </Panel>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]"
                   layout="total, sizes, prev, pager, next" :total="total" @current-change="load" @size-change="load" />

    <ContractForm v-model="formOpen" :contract="editing" @saved="onSaved" />
    <ContractWorkbook v-model="workbookOpen" :location="workbookLocation" @synced="load(true)" />
    <ContractReconciliation v-model="reconciliationOpen" />
  </div>
</template>

<script setup>
import { useAuth } from '@/store/auth'
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import ContractWorkbook from '@/components/ContractWorkbook.vue'
import ContractReconciliation from '@/components/ContractReconciliation.vue'
import { BUSINESS_LINES, CONTRACT_STATUS, money, percent, wan } from '@/utils/format'
import ContractForm from './contract-form.vue'

const removalAuth = useAuth()

const SOURCES = { 1: '企微审批', 2: '人工录入', 3: 'Excel 导入', 4: '企微表格同步' }
const REGIONS = ['市直', '江城', '阳春', '阳东', '阳西', '海陵湾', '待定']
const KEYS = ['keyword', 'business_line', 'region', 'customer', 'status', 'sign_month', 'source_type', 'sign_from', 'sign_to', 'paid_month', 'project_code', 'owner_id', 'sign_period', 'sign_year']
const PERIODS = { all: '累计签约', month: '本月签约', week: '本周签约', today: '今日签约' }
const signing = ref([]), signingNote = ref(''), owners = ref([])
const years = ref([]), currentYear = ref(new Date().getFullYear())
function periodLabel(key) { return key === 'all' && f.sign_year ? `${f.sign_year} 年签约` : PERIODS[key] }
const route = useRoute()
const router = useRouter()
const f = reactive(Object.fromEntries(KEYS.map((k) => [k, ''])))
const rows = ref([]); const total = ref(0); const sums = ref({}); const perms = ref({})
const page = ref(1); const pageSize = ref(20); const loading = ref(false); const exporting = ref(false)
const formOpen = ref(false); const editing = ref(null)
const workbookOpen = ref(false); const workbookLocation = ref(null)
const reconciliationOpen = ref(false)
function openWorkbook(location = null) { workbookLocation.value = location; workbookOpen.value = true }

const extraTags = computed(() => {
  const t = []
  if (f.sign_period) t.push({ key: 'sign_period', label: periodLabel(f.sign_period) })
  if (f.paid_month) t.push({ key: 'paid_month', label: `${f.paid_month} 有回款` })
  if (f.sign_from || f.sign_to) t.push({ key: 'sign_range', label: `签订 ${f.sign_from || '…'} ~ ${f.sign_to || '…'}` })
  if (f.project_code) t.push({ key: 'project_code', label: `项目 ${f.project_code}` })
  return t
})

function fromQuery() {
  for (const k of KEYS) {
    const v = route.query[k]
    f[k] = v === undefined || v === '' ? '' : ['status', 'source_type', 'owner_id', 'sign_year'].includes(k) ? Number(v) : String(v)
  }
}
function params() {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== '' && v !== null && v !== undefined))
}
let loadVersion = 0
async function load(withStats = false) {
  const version = ++loadVersion
  loading.value = true
  try {
    const input = params()
    const [r, board] = await Promise.all([
      api.get('/contracts', { ...input, page: page.value, page_size: pageSize.value }),
      withStats || !signing.value.length ? api.get('/contracts/signing', input) : null,
    ])
    if (version !== loadVersion) return
    rows.value = r.items; total.value = r.total; sums.value = r.sums; perms.value = r.perms
    if (perms.value.workbook && route.query.workbook && !workbookOpen.value) {
      openWorkbook({ key: String(route.query.workbook), sheet_id: String(route.query.sheet || ''), row: Number(route.query.row) })
    }
    if (board) { signing.value = board.items; signingNote.value = board.note }
  } catch {} finally { if (version === loadVersion) loading.value = false }
}
function search() {
  page.value = 1
  router.replace({ query: params() })
  load(true)
}
function choosePeriod(key) {
  f.sign_period = key
  search()
}
function changeYear() { f.sign_period = 'all'; f.sign_month = ''; f.sign_from = ''; f.sign_to = ''; search() }
function reset() { KEYS.forEach((k) => (f[k] = '')); f.sign_year = currentYear.value; f.sign_period = 'all'; search() }
function dropExtra(key) {
  if (key === 'sign_range') { f.sign_from = ''; f.sign_to = '' } else f[key] = ''
  search()
}
async function doExport() {
  exporting.value = true
  try { await api.download('/contracts/export', params()) } finally { exporting.value = false }
}
function openNew() { editing.value = null; formOpen.value = true }
function openEdit(row) { editing.value = row; formOpen.value = true }
function onSaved(c) {
  if (!editing.value) router.push(`/contracts/${c.id}`)
  else load(true)
}

onMounted(async () => {
  fromQuery()
  try { const result = await api.get('/contracts/options'); owners.value = result.owners; years.value = result.years; currentYear.value = result.current_year } catch {}
  if (!Object.keys(route.query).some(k => KEYS.includes(k))) { f.sign_year = currentYear.value; f.sign_period = 'all' }
  await load(true)
  if (route.query.new) openNew()
})

const contractMenu = rowDeletion(row => removalAuth.canDelete('contracts:write') && recordRemoval('contract', row.id, row.name || row.contract_no, () => load(true), { title: '删除合同' }))
</script>

<style scoped>
.signing-cards { margin: 0 0 4px; }
.signing-cards button { display: grid; gap: 4px; text-align: left; background: var(--jc-surface); color: var(--jc-ink); border: 1px solid var(--jc-line); border-radius: var(--jc-radius); padding: 13px 16px; cursor: pointer; }
.signing-cards button:is(:hover, [aria-pressed="true"]) { border-color: var(--jc-brand); }
.signing-cards b { font-size: 20px; }
.signing-cards small { color: var(--jc-ink-3); }

.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sums .s { display: flex; flex-direction: column; gap: 4px; padding: 12px 16px; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: var(--jc-radius); font-size: 12px; }
.sums b { font-size: 20px; font-weight: 600; color: var(--jc-ink); }
.pg { height: 4px; background: var(--jc-line-2); border-radius: 2px; overflow: hidden; margin-bottom: 3px; }
.pg i { display: block; height: 100%; background: var(--jc-brand); }
.clickable :deep(.el-table__row) { cursor: pointer; }
</style>
