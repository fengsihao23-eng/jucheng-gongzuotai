<!-- 业绩与运营（FR-05-07）：业绩统计（当月/年度 KPI、业务线/负责人排行、近 12 月趋势、同比）+ 项目整体运营看板 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="营销管控部" title="业绩与运营"
      desc="合同台账、回款与项目流程数据自动汇总，无需人工统计；口径见 PRD 3.4（待财务评审，sys_dict 可调）。">
      <template v-if="tab === 'perf'">
        <el-date-picker v-model="period" type="month" value-format="YYYY-MM" :clearable="false" style="width: 130px" @change="loadPerf" />
        <el-button @click="api.download('/performance/export', { period })">导出业绩 xlsx</el-button>
      </template>
      <template v-else>
        <el-select v-model="line" placeholder="全部业务线" clearable style="width: 150px" @change="loadBoard">
          <el-option v-for="(n, k) in BUSINESS_LINES" :key="k" :label="n" :value="k" />
        </el-select>
        <el-button @click="api.download('/performance/board/export')">导出运营看板 xlsx</el-button>
      </template>
    </PageHeader>

    <el-tabs v-model="tab" @tab-change="onTab">
      <!-- ================= 业绩统计 ================= -->
      <el-tab-pane label="业绩统计" name="perf">
        <div v-loading="loadingPerf">
          <div class="jc-grid c4">
            <Kpi label="当月合同签订额" :value="wanNum(ov.month_sign?.amount)" unit="万" :progress="ov.month_sign?.rate"
              :hint="ov.source_mode === 'workbook' ? `${ov.month_sign?.count ?? 0} 份 · 上月 ${wan(ov.month_sign?.prev)}` : `目标 ${wan(ov.month_sign?.target)} · 达成 ${percent(ov.month_sign?.rate)}`" :delta="ov.month_sign?.yoy"
              :to="{ path: '/contracts', query: { sign_month: ov.period } }" />
            <Kpi label="年度累计业绩" :value="wanNum(ov.year_sign?.amount)" unit="万" :progress="ov.year_sign?.rate"
              :hint="ov.source_mode === 'workbook' ? `${ov.year_sign?.count ?? 0} 份 · 上年同期 ${wan(ov.year_sign?.last_year)}` : `年度目标 ${wan(ov.year_sign?.target)} · 达成 ${percent(ov.year_sign?.rate)}`" :delta="ov.year_sign?.yoy"
              :to="{ path: '/contracts' }" />
            <Kpi :label="ov.source_mode === 'workbook' ? '当月已回款' : '当月回款'" :value="wanNum(ov.month_pay?.amount)" unit="万" :progress="ov.month_pay?.rate"
              :hint="ov.source_mode === 'workbook' ? `${ov.month_pay?.count ?? 0} 笔 · 原表月度回款进度` : `目标 ${wan(ov.month_pay?.target)} · 达成 ${percent(ov.month_pay?.rate)}`" :delta="ov.month_pay?.yoy"
              :to="ov.source_mode === 'workbook' ? { path: '/collections', query: { basis: 'monthly', period: ov.period } } : undefined" />
            <Kpi v-if="ov.source_mode === 'workbook'" :label="ov.planned_pay?.title || '当月预估回款金额'" :value="wanNum(ov.planned_pay?.amount)" unit="万"
              :hint="`${ov.planned_pay?.count ?? 0} 个项目 · 次月预估 ${wan(ov.planned_pay?.next_amount)}`"
              :to="{ path: '/collections' }" />
            <Kpi v-else label="年度累计回款" :value="wanNum(ov.year_pay?.amount)" unit="万" :progress="ov.year_pay?.rate"
              :hint="`年度回款目标 ${wan(ov.year_pay?.target)}`" :delta="ov.year_pay?.yoy" />
          </div>
          <p class="jc-muted jc-small cmp">
            统计区间 {{ ov.start }} ~ {{ ov.end }}；同比对照上年同期 {{ ov.compare?.month?.[0] }} ~ {{ ov.compare?.month?.[1] }}
            （年度 {{ ov.compare?.year?.[0] }} 起）。箭头为同比变化。
          </p>

          <div class="jc-grid c3 mt-16">
            <Panel class="jc-span-2" title="近 12 个月签约与回款" sub="柱：签约额 / 回款额；线：回款达成率">
              <Chart :option="trendOption" height="300px" :empty="!ov.trend?.length" />
            </Panel>
            <Panel :title="ov.source_mode === 'workbook' ? '区域业绩分布' : '业务线业绩进度'" :sub="ov.source_mode === 'workbook' ? '年度签约 / 同比变化' : '年度签约 / 当月回款达成'">
              <div v-for="r in ov.by_line || []" :key="r.business_line" class="prog">
                <div class="jc-row between"><b>{{ r.name }}</b><span class="jc-num jc-small">{{ ov.source_mode === 'workbook' ? wan(r.year_sign) : `${wan(r.year_sign)} / ${wan(r.year_sign_target)}` }}</span></div>
                <div v-if="ov.source_mode !== 'workbook'" class="bar"><i :style="barStyle(r.year_sign_rate)" /></div>
                <div class="jc-row between jc-small jc-muted">
                  <span>{{ ov.source_mode === 'workbook' ? '当月签约 ' + wan(r.month_sign) : '年签约 ' + percent(r.year_sign_rate) }} · 同比 <span :class="tone(r.year_sign_yoy)">{{ signed(r.year_sign_yoy) }}</span></span>
                  <span>{{ ov.source_mode === 'workbook' ? '已收 ' + wan(r.year_pay) : '月回款 ' + percent(r.month_pay_rate) }}</span>
                </div>
              </div>
            </Panel>
          </div>

          <div class="jc-grid c2 mt-16">
            <Panel :title="ov.dimension_label || '按业务线'" :padding="false">
              <el-table :data="ov.by_line || []" size="small">
                <el-table-column prop="name" :label="ov.dimension_col_label || '业务线'" min-width="110" />
                <el-table-column label="当月签约" align="right" min-width="100"><template #default="{ row }"><span class="jc-num">{{ wan(row.month_sign) }}</span></template></el-table-column>
                <el-table-column label="年度签约" align="right" min-width="100"><template #default="{ row }"><span class="jc-num">{{ wan(row.year_sign) }}</span></template></el-table-column>
                <el-table-column label="当月回款" align="right" min-width="100"><template #default="{ row }"><span class="jc-num">{{ wan(row.month_pay) }}</span></template></el-table-column>
                <el-table-column label="回款达成" align="right" width="86"><template #default="{ row }"><span :class="rateTone(row.month_pay_rate)">{{ percent(row.month_pay_rate) }}</span></template></el-table-column>
                <el-table-column label="年签约同比" align="right" width="96"><template #default="{ row }"><span :class="tone(row.year_sign_yoy)">{{ signed(row.year_sign_yoy) }}</span></template></el-table-column>
              </el-table>
            </Panel>
            <Panel title="销售负责人排行" sub="按年度签约额" :padding="false">
              <el-table :data="ov.by_owner || []" size="small" max-height="320">
                <el-table-column prop="rank" label="#" width="44" />
                <el-table-column prop="owner_name" label="负责人" min-width="80" />
                <el-table-column label="当月签约" align="right" min-width="100"><template #default="{ row }"><span class="jc-num">{{ wan(row.month_sign) }}</span></template></el-table-column>
                <el-table-column label="年度签约" align="right" min-width="120">
                  <template #default="{ row }">
                    <div class="mini"><i :style="{ width: ownerPct(row) }" /></div>
                    <span class="jc-num">{{ wan(row.year_sign) }}</span><span class="jc-muted jc-small"> · {{ row.year_sign_count }} 单</span>
                  </template>
                </el-table-column>
                <el-table-column label="年度回款" align="right" min-width="100"><template #default="{ row }"><span class="jc-num">{{ wan(row.year_pay) }}</span></template></el-table-column>
              </el-table>
            </Panel>
          </div>

          <Panel class="mt-16" title="历史快照" sub="每月 1 日 08:30 自动生成上月业绩、每周一 09:15 生成运营看板并推送营销管控与总办" :padding="false">
            <el-table :data="reports" @row-click="row => followupRow = row" size="small" empty-text="暂无快照">
              <el-table-column prop="title" label="报表" min-width="180" />
              <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
              <el-table-column label="触发" width="80"><template #default="{ row }">{{ row.trigger === 'schedule' ? '定时' : '手动' }}</template></el-table-column>
              <el-table-column label="生成时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
              <el-table-column label="文件" min-width="200"><template #default="{ row }"><FileLink v-for="f in row.files" :key="f.id" :file="f" /></template></el-table-column>
            </el-table>
          </Panel>
        </div>
      </el-tab-pane>

      <!-- ================= 项目运营看板 ================= -->
      <el-tab-pane label="项目运营看板" name="board">
        <div v-loading="loadingBoard">
          <div class="jc-grid c5">
            <Kpi :label="bd.source_mode === 'workbook' ? '合同项目' : '运营项目'" :value="sm.projects ?? '—'" unit="个"
              :hint="bd.source_mode === 'workbook' ? `未收项目 ${sm.unpaid_projects ?? 0} 个` : `节点延期 ${sm.delayed_projects ?? 0} 个`" />
            <Kpi label="合同总额" :value="wanNum(sm.contract_amount)" unit="万" :hint="`未收余额 ${wan(sm.receivable)}`" />
            <Kpi label="累计已收" :value="wanNum(sm.received)" unit="万" :progress="sm.pay_rate" :hint="`回款率 ${percent(sm.pay_rate)}`" />
            <Kpi v-if="bd.source_mode === 'workbook'" label="当月预估回款金额" :value="wanNum(sm.planned_month)" unit="万"
              :hint="`${sm.planned_month_count ?? 0} 个项目 · 原表预估`" :to="{ path: '/collections' }" />
            <Kpi v-else label="逾期应收" :value="wanNum(sm.overdue)" unit="万" :hint="`${sm.overdue_projects ?? 0} 个项目存在逾期`" />
            <Kpi v-if="bd.source_mode === 'workbook'" label="次月预估回款金额" :value="wanNum(sm.planned_next)" unit="万"
              :hint="`${sm.planned_next_count ?? 0} 个项目 · 原表预估`" :to="{ path: '/collections' }" />
            <Kpi v-else label="项目毛利" :value="wanNum(sm.gross_profit)" unit="万" :hint="`毛利率 ${percent(sm.gross_margin)}`" />
          </div>

          <Panel class="mt-16" :title="bd.source_mode === 'workbook' ? '合同与回款台账' : '运营项目'" :sub="bd.source_mode === 'workbook' ? `两张表合并 · 统计日 ${bd.as_of || ''}` : `在建 / 已验收 / 运维 · 统计日 ${bd.as_of || ''} · 逾期或延期行标红`" :padding="false">
            <el-table :data="bd.projects || []" :row-class-name="({ row }) => (row.alert ? 'is-alert' : '')" size="small"
              :default-sort="{ prop: 'contract_amount', order: 'descending' }">
              <el-table-column label="项目" min-width="230" fixed>
                <template #default="{ row }">
                  <router-link class="plink" :to="row.detail_path || `/projects/${row.project_code}`">{{ row.name }}</router-link>
                  <div class="jc-muted jc-small">{{ row.project_code }} · {{ row.customer_name }}</div>
                </template>
              </el-table-column>
              <el-table-column label="业务线" width="104"><template #default="{ row }">{{ row.line_name }}</template></el-table-column>
              <el-table-column label="状态" width="76"><template #default="{ row }"><StatusPill :text="row.status_name" :tone="row.status === 2 ? 'brand' : 'info'" /></template></el-table-column>
              <el-table-column prop="contract_amount" label="合同额" align="right" width="110" sortable :sort-method="sortBy('contract_amount')">
                <template #default="{ row }"><span class="jc-num">{{ wan(row.contract_amount) }}</span></template>
              </el-table-column>
              <el-table-column label="回款率" width="150" sortable :sort-method="sortBy('pay_rate')">
                <template #default="{ row }">
                  <div class="jc-row" style="gap: 8px">
                    <div class="bar flex"><i :style="barStyle(row.pay_rate)" /></div>
                    <span class="jc-num jc-small">{{ percent(row.pay_rate, 0) }}</span>
                  </div>
                  <div class="jc-muted jc-small">已收 {{ wan(row.received) }}</div>
                </template>
              </el-table-column>
              <el-table-column label="应收余额" align="right" width="104"><template #default="{ row }"><span class="jc-num">{{ wan(row.receivable) }}</span></template></el-table-column>
              <el-table-column label="逾期应收" align="right" width="104" sortable :sort-method="sortBy('overdue')">
                <template #default="{ row }"><span class="jc-num" :class="{ 'jc-down': Number(row.overdue) > 0 }">{{ Number(row.overdue) > 0 ? wan(row.overdue) : '—' }}</span></template>
              </el-table-column>
              <el-table-column label="成本" align="right" width="100"><template #default="{ row }"><span class="jc-num">{{ wan(row.cost) }}</span></template></el-table-column>
              <el-table-column label="毛利率" align="right" width="86" sortable :sort-method="sortBy('gross_margin')">
                <template #default="{ row }"><span :class="{ 'jc-down': row.gross_margin !== null && row.gross_margin < 0.15 }">{{ percent(row.gross_margin) }}</span></template>
              </el-table-column>
              <el-table-column label="节点进度" width="170">
                <template #default="{ row }">
                  <div class="jc-small">{{ row.milestone_done }}/{{ row.milestone_total }} · {{ row.current_node || '—' }}</div>
                  <div v-if="row.delayed_count" class="jc-down jc-small">延期：{{ row.delayed_nodes.join('、') }}</div>
                </template>
              </el-table-column>
            </el-table>
          </Panel>

          <div class="jc-grid c3 mt-16">
            <Panel title="业务线汇总" :padding="false">
              <el-table :data="bd.by_line || []" size="small">
                <el-table-column prop="name" label="业务线" min-width="100" />
                <el-table-column prop="projects" label="项目" width="56" align="right" />
                <el-table-column label="合同额" align="right" min-width="90"><template #default="{ row }"><span class="jc-num">{{ wan(row.contract_amount) }}</span></template></el-table-column>
                <el-table-column label="回款率" align="right" width="72"><template #default="{ row }">{{ percent(row.pay_rate, 0) }}</template></el-table-column>
                <el-table-column label="逾期" align="right" min-width="84"><template #default="{ row }"><span :class="{ 'jc-down': Number(row.overdue) > 0 }">{{ wan(row.overdue) }}</span></template></el-table-column>
              </el-table>
            </Panel>
            <Panel title="客户 Top10" sub="按合同额">
              <Chart :option="topOption" height="300px" :empty="!bd.top_customers?.length" />
            </Panel>
            <Panel title="回款账龄分布" :sub="`已到期未收 ${wan(bd.aging?.total)} · 未到期 ${wan(bd.aging?.not_due)}`">
              <Chart :option="agingOption" height="300px" :empty="!Number(bd.aging?.total)" />
            </Panel>
          </div>

          <Panel class="mt-16" title="账龄明细" sub="到期未收清的回款计划，按逾期天数降序" :padding="false">
            <el-table :data="bd.aging?.detail || []" size="small" max-height="360">
              <el-table-column prop="project_name" label="项目" min-width="200" show-overflow-tooltip />
              <el-table-column label="合同" width="130">
                <template #default="{ row }"><span class="jc-mono">{{ row.contract_no }}</span></template>
              </el-table-column>
              <el-table-column prop="stage_name" label="回款阶段" width="90" />
              <el-table-column label="到期日" width="104"><template #default="{ row }">{{ date(row.due_date) }}</template></el-table-column>
              <el-table-column label="未收金额" align="right" width="120"><template #default="{ row }"><span class="jc-num">{{ money(row.outstanding, 0) }}</span></template></el-table-column>
              <el-table-column label="逾期天数" align="right" width="90"><template #default="{ row }"><span :class="{ 'jc-down': row.days > 90 }">{{ row.days }}</span></template></el-table-column>
              <el-table-column label="账龄" width="90"><template #default="{ row }"><StatusPill :text="row.bucket + ' 天'" :tone="row.days > 180 ? 'down' : row.days > 90 ? 'warn' : 'info'" /></template></el-table-column>
            </el-table>
          </Panel>
          <p class="jc-muted jc-small cmp">{{ bd.caliber }}</p>
        </div>
      </el-tab-pane>
    </el-tabs>
    <RecordRowDetails v-model:row="followupRow" kind="performance-report" title="业绩快照详情" heading-field="title"
      :fields="{ period: '期间', version: '版本' }" progress-hint="说明业绩核对、差异原因或需协调的事项。">
      <FileLink v-for="file in followupRow?.files || []" :key="file.id" :file="file" />
    </RecordRowDetails>
  </div>
</template>

<script setup>
import RecordRowDetails from '@/components/records/RecordRowDetails.vue'
import { computed, onMounted, ref } from 'vue'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import api from '@/api/client'
import { BUSINESS_LINES, date, datetime, money, percent, wan, wanNum, num } from '@/utils/format'
import { PALETTE, baseGrid, wanAxis, wanTooltip } from '@/utils/chart'

const route = useRoute()
const router = useRouter()
const followupRow = ref(null)
const tab = ref(route.query.tab === 'board' ? 'board' : 'perf')
const period = ref(route.query.period || dayjs().format('YYYY-MM'))
const line = ref('')
const ov = ref({})
const bd = ref({})
const reports = ref([])
const loadingPerf = ref(false)
const loadingBoard = ref(false)
const sm = computed(() => bd.value.summary || {})

async function loadPerf() {
  loadingPerf.value = true
  try {
    ov.value = await api.get('/performance/overview', { period: period.value })
    reports.value = await api.get('/performance/reports')
  } finally { loadingPerf.value = false }
}
async function loadBoard() {
  loadingBoard.value = true
  try { bd.value = await api.get('/performance/board', { business_line: line.value || undefined }) } finally { loadingBoard.value = false }
}
function onTab(t) {
  router.replace({ query: { ...route.query, tab: t } })
  if (t === 'board' && !bd.value.projects) loadBoard()
  if (t === 'perf' && !ov.value.period) loadPerf()
}

const barStyle = (r) => ({
  width: Math.min(100, num(r) * 100) + '%',
  background: r >= 1 ? 'var(--jc-up)' : r >= 0.6 ? 'var(--jc-brand)' : 'var(--jc-warn)',
})
const signed = (v) => (v === null || v === undefined ? '—' : (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%')
const tone = (v) => (v > 0 ? 'jc-up' : v < 0 ? 'jc-down' : 'jc-muted')
const rateTone = (v) => (v === null ? 'jc-muted' : v >= 1 ? 'jc-up' : v < 0.6 ? 'jc-down' : '')
const ownerMax = computed(() => Math.max(1, ...(ov.value.by_owner || []).map((r) => num(r.year_sign))))
const ownerPct = (r) => (num(r.year_sign) / ownerMax.value) * 100 + '%'
const sortBy = (k) => (a, b) => num(a[k]) - num(b[k])

const trendOption = computed(() => {
  const t = ov.value.trend || []
  return {
    grid: baseGrid({ top: 36 }), legend: { top: 0, right: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v > 1.5 || v < 0 ? wanTooltip(v) : (v * 100).toFixed(1) + '%') },
    xAxis: { type: 'category', data: t.map((x) => x.period.slice(2)) },
    yAxis: [{ type: 'value', ...wanAxis }, { type: 'value', max: (v) => Math.max(1.2, v.max), axisLabel: { formatter: (v) => (v * 100).toFixed(0) + '%' }, splitLine: { show: false } }],
    series: [
      { name: '签约额', type: 'bar', data: t.map((x) => num(x.sign)) },
      { name: '回款额', type: 'bar', data: t.map((x) => num(x.pay)) },
      { name: '回款达成率', type: 'line', yAxisIndex: 1, data: t.map((x) => x.pay_rate), itemStyle: { color: PALETTE[2] } },
    ],
  }
})

const topOption = computed(() => {
  const rows = [...(bd.value.top_customers || [])].reverse()
  return {
    grid: baseGrid({ top: 8, right: 24 }), tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: wanTooltip },
    xAxis: { type: 'value', ...wanAxis }, legend: { show: false },
    yAxis: { type: 'category', data: rows.map((r) => r.customer_name.length > 9 ? r.customer_name.slice(0, 9) + '…' : r.customer_name) },
    series: [
      { name: '已回款', type: 'bar', stack: 't', data: rows.map((r) => num(r.received)), itemStyle: { borderRadius: 0 } },
      { name: '应收', type: 'bar', stack: 't', data: rows.map((r) => num(r.receivable)), itemStyle: { color: PALETTE[5], borderRadius: [0, 3, 3, 0] } },
    ],
  }
})

const agingOption = computed(() => {
  const b = bd.value.aging?.buckets || []
  const colors = [PALETTE[0], PALETTE[1], PALETTE[3], '#8a3b2e']
  return {
    grid: baseGrid({ top: 16 }), tooltip: { trigger: 'axis', valueFormatter: wanTooltip },
    xAxis: { type: 'category', data: b.map((x) => x.bucket + ' 天') },
    yAxis: { type: 'value', ...wanAxis },
    series: [{ type: 'bar', name: '未收金额', barMaxWidth: 36, data: b.map((x, i) => ({ value: num(x.amount), itemStyle: { color: colors[i] } })),
      label: { show: true, position: 'top', fontSize: 11, formatter: (p) => (p.value ? wan(p.value) : '') } }],
  }
})

useBusinessRefresh(() => tab.value === 'board' ? loadBoard() : loadPerf())
onMounted(() => (tab.value === 'board' ? loadBoard() : loadPerf()))
</script>

<style scoped>
.cmp { margin: 10px 2px 0; }
.prog { padding: 8px 0 10px; border-bottom: 1px solid var(--jc-line-2); }
.prog:last-child { border-bottom: 0; }
.prog b { font-size: 13px; font-weight: 600; }
.bar { height: 6px; background: var(--jc-line-2); border-radius: 3px; overflow: hidden; margin: 8px 0 6px; }
.bar.flex { flex: 1; margin: 0; min-width: 50px; }
.bar i { display: block; height: 100%; border-radius: 3px; transition: width 0.5s ease; }
.mini { height: 3px; background: var(--jc-line-2); border-radius: 2px; overflow: hidden; margin-bottom: 4px; }
.mini i { display: block; height: 100%; background: var(--jc-brand); }
.plink { color: var(--jc-ink); font-weight: 500; }
.plink:hover { color: var(--jc-brand); }
</style>
