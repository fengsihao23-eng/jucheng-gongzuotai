<!-- 项目利润（FR-04-02 / 3.3 / 7.1）：共享利润口径 profit_service 实时计算，与周利润表、看板、WorkBuddy 一致 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="财务" title="项目利润" :desc="`项目收入 − 归集成本 = 毛利（成本含报表待支付应付）；公共费用不摊入项目，单列为管理费用-公共。统计周 ${res?.period || ''}（${date(res?.start)} ~ ${date(res?.end)}）`">
      <el-date-picker v-model="weekDate" type="week" format="gggg 第 ww 周" value-format="YYYY-MM-DD" :clearable="false"
        :disabled-date="future" style="width: 170px" @change="reload" />
      <FileLink v-if="res?.report?.files?.[0]" :file="res.report.files[0]" label="下载周利润表" />
      <el-button v-if="auth.can('report:generate') && auth.hasMenu('reports')" :loading="genLoading" @click="generate">生成本周利润表</el-button>
    </PageHeader>

    <div class="jc-grid c6" v-loading="loading">
      <Kpi label="累计收入" :value="wanNum(k.income)" unit="万元" :hint="`本周新增 ${wan(k.period_income)}`" />
      <Kpi label="累计成本" :value="wanNum(k.cost)" unit="万元" :hint="`本周发生 ${wan(k.period_cost)} · 含待支付 ${wanNum(k.cost_payable)} 万元`" />
      <Kpi label="毛利" :value="wanNum(k.gross_profit)" unit="万元" :hint="`${res?.rows?.length || 0} 个项目`" />
      <Kpi label="毛利率" :value="k.gross_margin === null || k.gross_margin === undefined ? '—' : (Number(k.gross_margin) * 100).toFixed(1)" unit="%"
        :hint="`低于 ${percent(res?.alert_margin, 0)} 的项目 ${k.low_margin_count || 0} 个`" />
      <Kpi label="本周费用" :value="wanNum(k.period_expense, 2)" unit="万元" hint="本周归集到项目的报销" />
      <Kpi label="公共费用" :value="wanNum(k.public_expense, 2)" unit="万元" hint="管理费用-公共，不摊入项目" />
    </div>

    <Panel class="mt-16" :padding="false" :loading="loading">
      <el-tabs v-model="tableTab" class="profit-tabs">
        <el-tab-pane name="profit" :label="rows.length ? `项目利润表 · ${rows.length} 个项目` : '项目利润表'">
          <div class="pane-tools">
            <span class="jc-muted jc-small">点击项目查看明细卡片：归集报销、采购成本、合同{{ hasLemon ? '、柠檬云辅助核算' : '' }}</span>
            <span class="jc-spacer" />
            <el-select v-model="lineFilter" placeholder="业务线" clearable size="small" style="width: 130px">
              <el-option v-for="(n, k2) in BUSINESS_LINES" :key="k2" :label="n" :value="k2" />
            </el-select>
            <el-checkbox v-model="onlyLow" size="small">只看低毛利</el-checkbox>
          </div>
      <el-table :data="rows" row-key="project_code" :row-class-name="({ row }) => (row.low_margin ? 'is-alert' : '')"
        show-summary :summary-method="summary" @row-click="openDetail">
        <el-table-column label="项目" min-width="230" fixed>
          <template #default="{ row }">
            <div class="pn">{{ row.name }}<StatusPill v-if="row.is_key" text="重点" tone="brand" /></div>
            <div class="jc-small jc-muted jc-mono">{{ row.project_code }} · {{ row.line_name }} · {{ row.status_name }}</div>
            <div v-if="row.group_note" class="jc-small jc-muted">{{ row.group_note }}</div>
          </template>
        </el-table-column>
        <el-table-column label="累计收入" min-width="120" align="right" prop="income"><template #default="{ row }"><span class="jc-num">{{ money(row.income, 0) }}</span></template></el-table-column>
        <el-table-column label="报销费用" min-width="110" align="right" prop="cost_expense"><template #default="{ row }"><span class="jc-num">{{ money(row.cost_expense, 0) }}</span></template></el-table-column>
        <el-table-column label="采购成本" min-width="120" align="right" prop="cost_project"><template #default="{ row }"><span class="jc-num">{{ money(row.cost_project, 0) }}</span></template></el-table-column>
        <el-table-column label="待支付" min-width="110" align="right" prop="cost_payable">
          <template #default="{ row }"><span class="jc-num" :class="{ 'jc-warn': Number(row.cost_payable) }">{{ money(row.cost_payable, 0) }}</span></template>
        </el-table-column>
        <el-table-column v-if="hasLemon" label="柠檬云核算" min-width="110" align="right" prop="cost_lemon"><template #default="{ row }"><span class="jc-num">{{ money(row.cost_lemon, 0) }}</span></template></el-table-column>
        <el-table-column label="累计成本" min-width="120" align="right" prop="cost"><template #default="{ row }"><span class="jc-num">{{ money(row.cost, 0) }}</span></template></el-table-column>
        <el-table-column label="毛利" min-width="120" align="right" prop="gross_profit"><template #default="{ row }"><span class="jc-num" :class="{ 'jc-down': Number(row.gross_profit) < 0 }">{{ money(row.gross_profit, 0) }}</span></template></el-table-column>
        <el-table-column label="毛利率" width="96" align="right" prop="gross_margin">
          <template #default="{ row }"><span class="jc-num" :class="{ 'jc-down': row.low_margin }">{{ percent(row.gross_margin) }}</span></template>
        </el-table-column>
        <el-table-column label="本周收入" min-width="110" align="right" prop="period_income"><template #default="{ row }"><span class="jc-num">{{ money(row.period_income, 0) }}</span></template></el-table-column>
        <el-table-column label="本周成本" min-width="110" align="right" prop="period_cost"><template #default="{ row }"><span class="jc-num">{{ money(row.period_cost, 0) }}</span></template></el-table-column>
        <el-table-column label="本周费用" min-width="100" align="right" prop="period_expense"><template #default="{ row }"><span class="jc-num">{{ money(row.period_expense, 0) }}</span></template></el-table-column>
      </el-table>
        </el-tab-pane>
        <el-tab-pane label="历史项目报表" name="sources">
          <ProjectReportSources bare />
        </el-tab-pane>
      </el-tabs>
    </Panel>

    <el-drawer v-model="drawer" :title="current?.name || '项目明细'" size="66%" destroy-on-close>
      <div v-loading="detailLoading" class="jc-stack drawer-body">
        <template v-if="current">
          <div class="jc-row between wrap">
            <div>
              <div class="jc-small jc-muted jc-mono">{{ current.project_code }} · {{ current.line_name }} · {{ current.status_name }}</div>
              <div v-if="current.group_note" class="jc-small jc-muted">{{ current.group_note }}</div>
            </div>
            <router-link v-if="auth.hasMenu('projects')" :to="`/projects/${current.project_code}`" @click="drawer = false">查看项目流程 →</router-link>
          </div>
          <div class="dstats">
            <div><span class="jc-muted jc-small">累计收入</span><b class="jc-num">{{ money(current.income, 0) }}</b></div>
            <div><span class="jc-muted jc-small">累计成本</span><b class="jc-num">{{ money(current.cost, 0) }}</b><em class="jc-small jc-muted">含待支付 {{ money(current.cost_payable, 0) }}</em></div>
            <div><span class="jc-muted jc-small">毛利</span><b class="jc-num" :class="{ 'jc-down': Number(current.gross_profit) < 0 }">{{ money(current.gross_profit, 0) }}</b></div>
            <div><span class="jc-muted jc-small">毛利率</span><b class="jc-num" :class="{ 'jc-down': current.low_margin }">{{ percent(current.gross_margin) }}</b></div>
          </div>
          <el-tabs v-if="detailData" v-model="drawerTab">
            <el-tab-pane :label="`归集报销 ${detailData.expenses.length}`" name="exp">
              <el-table :data="detailData.expenses" max-height="460" :row-class-name="periodRow">
                <el-table-column label="审批单号" width="160" prop="approval_no" />
                <el-table-column label="费用日" width="110"><template #default="{ row: e }">{{ date(e.expense_date) }}</template></el-table-column>
                <el-table-column label="申请人" width="90" prop="applicant" />
                <el-table-column label="类别" width="100" prop="category" />
                <el-table-column label="事由" min-width="200" prop="reason" />
                <el-table-column label="金额" width="130" align="right"><template #default="{ row: e }"><span class="jc-num">{{ money(e.amount) }}</span></template></el-table-column>
                <el-table-column label="" width="64"><template #default="{ row: e }"><StatusPill v-if="e.in_period" text="本周" tone="brand" /></template></el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane :label="`采购成本 ${detailData.costs.length}`" name="cost">
              <el-table :data="detailData.costs" max-height="460" :row-class-name="periodRow">
                <el-table-column label="日期" width="110"><template #default="{ row: c }">{{ date(c.cost_date) }}</template></el-table-column>
                <el-table-column label="类型" width="90" prop="cost_type" />
                <el-table-column label="摘要" min-width="260" prop="summary" />
                <el-table-column label="金额" width="130" align="right"><template #default="{ row: c }"><span class="jc-num">{{ money(c.amount) }}</span></template></el-table-column>
                <el-table-column label="" width="64"><template #default="{ row: c }"><StatusPill v-if="c.in_period" text="本周" tone="brand" /></template></el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane :label="`合同 ${detailData.contracts.length}`" name="con">
              <el-table :data="detailData.contracts">
                <el-table-column label="合同编号" width="140" prop="contract_no" />
                <el-table-column label="合同名称" min-width="240" prop="name" />
                <el-table-column label="签订日" width="110"><template #default="{ row: c }">{{ date(c.sign_date) }}</template></el-table-column>
                <el-table-column label="合同额" width="130" align="right"><template #default="{ row: c }"><span class="jc-num">{{ money(c.amount) }}</span></template></el-table-column>
                <el-table-column label="已回款" width="130" align="right"><template #default="{ row: c }"><span class="jc-num">{{ money(c.received) }}</span></template></el-table-column>
                <el-table-column label="计入收入" width="96"><template #default="{ row: c }"><StatusPill :text="c.counted ? '是' : '已终止'" :tone="c.counted ? 'up' : 'down'" /></template></el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane v-if="hasLemon" :label="`柠檬云辅助核算 ${detailData.lemon.length}`" name="lemon">
              <el-table :data="detailData.lemon" max-height="460">
                <el-table-column label="凭证号" width="110" prop="voucher_no" />
                <el-table-column label="日期" width="110"><template #default="{ row: v }">{{ date(v.voucher_date) }}</template></el-table-column>
                <el-table-column label="科目" width="180" prop="subject" />
                <el-table-column label="摘要" min-width="220" prop="summary" />
                <el-table-column label="金额" width="130" align="right"><template #default="{ row: v }"><span class="jc-num">{{ money(v.amount) }}</span></template></el-table-column>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </template>
      </div>
    </el-drawer>

    <div class="jc-grid c3 mt-16">
      <Panel class="jc-span-2" title="近 12 周毛利趋势" sub="柱：本周收入 / 本周成本；线：累计毛利率" :loading="trendLoading">
        <Chart :option="trendOption" height="300px" :empty="!trend.length" />
      </Panel>
      <Panel title="公共费用构成" :sub="`管理费用-公共 ${money(res?.public_expense)}`" :padding="false">
        <el-table :data="res?.public_detail || []" size="small" max-height="300">
          <el-table-column label="类别" prop="category" />
          <el-table-column label="笔数" width="64" align="right" prop="count" />
          <el-table-column label="金额" width="120" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.amount) }}</span></template></el-table-column>
        </el-table>
      </Panel>
    </div>

    <el-collapse class="mt-16 cal">
      <el-collapse-item name="cal">
        <template #title>
          <span class="cal-t">口径说明</span>
          <span class="jc-muted jc-small">收入口径 {{ INCOME[res?.caliber?.income_basis] || res?.caliber?.income_basis }} · 成本来源
            {{ (res?.caliber?.cost_sources || []).map((s) => SOURCES[s] || s).join('、') }}</span>
        </template>
        <ol class="cal-list"><li v-for="(c, i) in res?.caliber_lines || []" :key="i">{{ c }}</li></ol>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import ProjectReportSources from '@/components/ProjectReportSources.vue'
import { useAuth } from '@/store/auth'
import { BUSINESS_LINES, date, money, percent, wan, wanNum } from '@/utils/format'
import { baseGrid, wanAxis, wanTooltip } from '@/utils/chart'

dayjs.extend(isoWeek)
const INCOME = { contract: '已签合同额', payment: '已到账回款' }
const SOURCES = { expense: '归集报销', project_cost: '采购成本', report_payable: '报表待支付', lemon_aux: '柠檬云辅助核算' }
const auth = useAuth()
const loading = ref(false)
const trendLoading = ref(false)
const genLoading = ref(false)
const res = ref(null)
const trend = ref([])
const weekDate = ref('')
const lineFilter = ref('')
const onlyLow = ref(false)
const tableTab = ref('profit')
const drawer = ref(false)
const current = ref(null)
const detailData = ref(null)
const detailLoading = ref(false)
const drawerTab = ref('exp')
const future = (d) => d.getTime() > Date.now()

const isoWeekOf = (d) => `${dayjs(d).isoWeekYear()}-W${String(dayjs(d).isoWeek()).padStart(2, '0')}`
const week = computed(() => (weekDate.value ? isoWeekOf(weekDate.value) : undefined))
const k = computed(() => res.value?.kpi || {})
const hasLemon = computed(() => (res.value?.caliber?.cost_sources || []).includes('lemon_aux'))
const rows = computed(() => (res.value?.rows || []).filter((r) => (!lineFilter.value || r.business_line === lineFilter.value) && (!onlyLow.value || r.low_margin)))
const periodRow = ({ row }) => (row.in_period ? 'in-period' : '')

const SUM_KEYS = ['income', 'cost_expense', 'cost_project', 'cost_payable', 'cost_lemon', 'cost', 'gross_profit', 'period_income', 'period_cost', 'period_expense']
function summary({ columns, data }) {
  return columns.map((c, i) => {
    if (c.label === '项目') return `合计（${data.length}）`
    if (SUM_KEYS.includes(c.property)) return money(data.reduce((s, r) => s + Number(r[c.property] || 0), 0), 0)
    if (c.property === 'gross_margin') {
      const inc = data.reduce((s, r) => s + Number(r.income || 0), 0)
      const gp = data.reduce((s, r) => s + Number(r.gross_profit || 0), 0)
      return inc ? percent(gp / inc) : '—'
    }
    return ''
  })
}

const trendOption = computed(() => ({
  grid: baseGrid({ top: 40 }),
  legend: { top: 0, right: 0 },
  tooltip: { trigger: 'axis', valueFormatter: (v) => (typeof v === 'number' && Math.abs(v) <= 1.5 && v % 1 !== 0 ? (v * 100).toFixed(1) + '%' : wanTooltip(v)) },
  xAxis: { type: 'category', data: trend.value.map((w) => w.week.slice(5)) },
  yAxis: [{ type: 'value', ...wanAxis }, { type: 'value', axisLabel: { formatter: (v) => (v * 100).toFixed(0) + '%' }, splitLine: { show: false } }],
  series: [
    { name: '本周收入', type: 'bar', data: trend.value.map((w) => Number(w.period_income)) },
    { name: '本周成本', type: 'bar', data: trend.value.map((w) => Number(w.period_cost)) },
    { name: '累计毛利率', type: 'line', yAxisIndex: 1, data: trend.value.map((w) => (w.gross_margin === null ? null : Number(w.gross_margin))) },
  ],
}))

async function load() {
  loading.value = true
  try {
    res.value = await api.get('/reports/profit', { week: week.value })
    if (!weekDate.value) weekDate.value = res.value.start
  } finally {
    loading.value = false
  }
}
async function loadTrend() {
  trendLoading.value = true
  try {
    trend.value = (await api.get('/reports/profit/trend', { week: week.value })).weeks
  } finally {
    trendLoading.value = false
  }
}
function reload() {
  drawer.value = false
  current.value = null
  detailData.value = null
  load()
  loadTrend()
}
useBusinessRefresh(reload)
async function openDetail(row) {
  current.value = row
  detailData.value = null
  drawerTab.value = 'exp'
  drawer.value = true
  detailLoading.value = true
  try {
    detailData.value = (await api.get(`/reports/profit/${row.project_code}`, { week: week.value })).detail
  } finally {
    detailLoading.value = false
  }
}
async function generate() {
  genLoading.value = true
  try {
    const r = await api.post('/reports/generate', { report_type: 'weekly_profit', period: week.value || res.value?.period })
    ElMessage.success(`已生成 ${r.title} v${r.version}`)
    await load()
  } finally {
    genLoading.value = false
  }
}

onMounted(async () => {
  await load()
  loadTrend()
})
</script>

<style scoped>
.profit-tabs :deep(.el-tabs__header) { margin: 0; padding: 0 18px; }
.profit-tabs :deep(.el-tabs__nav-wrap::after) { height: 1px; background: var(--jc-line-2); }
.profit-tabs :deep(.el-tabs__item) { height: 46px; font-size: 14px; }
.pane-tools { display: flex; align-items: center; gap: 8px; padding: 10px 18px; border-bottom: 1px solid var(--jc-line-2); flex-wrap: wrap; }
.pn { font-weight: 600; color: var(--jc-ink); display: flex; align-items: center; gap: 6px; }
.drawer-body { min-height: 200px; }
.dstats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; padding: 12px 16px; background: var(--jc-surface-2); border-radius: var(--jc-radius-sm); }
.dstats > div { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.dstats b { font-size: 20px; font-weight: 600; color: var(--jc-ink); }
:deep(.in-period td.el-table__cell) { background: var(--jc-brand-50); }
.cal { border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); padding: 0 16px; }
.cal :deep(.el-collapse-item__header), .cal :deep(.el-collapse-item__wrap) { border: none; background: transparent; }
.cal-t { font-weight: 600; margin-right: 12px; color: var(--jc-ink); }
.cal-list { margin: 0; padding-left: 18px; color: var(--jc-ink-2); font-size: 12.5px; line-height: 1.75; }
:deep(.el-table__row) { cursor: pointer; }
</style>
