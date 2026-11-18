<template>
  <section class="income-report">
    <div class="report-heading">
      <div><h3>收入情况</h3><p>五项数据均来自本次指定工作簿，各表按自身口径独立展示。</p></div>
      <span class="source-name">智慧校园 · 1113 版</span>
    </div>

    <div class="report-tabs" role="tablist" aria-label="收入情况数据视图">
      <button v-for="item in views" :key="item.key" type="button" role="tab" :aria-selected="view === item.key" @click="view = item.key">{{ item.label }}</button>
    </div>

    <div class="summary-grid">
      <div v-for="card in summaryCards" :key="card.label" class="summary-card">
        <span>{{ card.label }}</span>
        <div><strong>{{ card.value }}</strong><small v-if="card.unit">{{ card.unit }}</small></div>
        <p v-if="card.hint">{{ card.hint }}</p>
      </div>
    </div>

    <Panel v-if="view === 'annual'" title="2026年度情况" sub="2025年与2026年营收对照，金额单位：元" :padding="false">
      <el-table :data="annualRows" :row-class-name="totalRow" max-height="560">
        <el-table-column prop="name" label="学校 / 项目" min-width="250" fixed show-overflow-tooltip />
        <el-table-column label="2025年" min-width="125" align="right"><template #default="{ row }">{{ number(row.lastYear) }}</template></el-table-column>
        <el-table-column label="2026年" min-width="125" align="right"><template #default="{ row }"><b>{{ number(row.currentYear) }}</b></template></el-table-column>
        <el-table-column label="增长率" min-width="105" align="right"><template #default="{ row }"><span :class="tone(row.growth)">{{ percent(row.growth) }}</span></template></el-table-column>
        <el-table-column label="增长金额" min-width="125" align="right"><template #default="{ row }"><span :class="tone(row.growthAmount)">{{ number(row.growthAmount) }}</span></template></el-table-column>
        <el-table-column label="本年目标金额" min-width="140" align="right"><template #default="{ row }">{{ number(row.target) }}</template></el-table-column>
        <el-table-column label="目标完成率" min-width="120" align="right"><template #default="{ row }">{{ percent(row.targetRate) }}</template></el-table-column>
      </el-table>
    </Panel>

    <Panel v-else-if="view === 'semester'" title="本学期汇总" :sub="semester.period + ' · 回款率按原表缴费人数 ÷ 学生总人数'" :padding="false">
      <el-table :data="semesterRows" :row-class-name="totalRow" max-height="560">
        <el-table-column prop="name" label="学校 / 项目" min-width="250" fixed show-overflow-tooltip />
        <el-table-column label="学生总人数" min-width="125" align="right"><template #default="{ row }">{{ number(row.students) }}</template></el-table-column>
        <el-table-column label="回款金额（元）" min-width="145" align="right"><template #default="{ row }"><b>{{ number(row.amount) }}</b></template></el-table-column>
        <el-table-column label="缴费人数" min-width="115" align="right"><template #default="{ row }">{{ number(row.payers) }}</template></el-table-column>
        <el-table-column label="总人数回款率" min-width="140" align="right"><template #default="{ row }">{{ percent(row.rate) }}</template></el-table-column>
        <el-table-column prop="note" label="备注" min-width="160" show-overflow-tooltip><template #default="{ row }">{{ row.note || '—' }}</template></el-table-column>
      </el-table>
    </Panel>

    <template v-else-if="view === 'history'">
      <div class="period-filter"><span>查看学期</span><el-select v-model="historyIndex" aria-label="历史学期" style="width: 290px"><el-option v-for="(item, index) in historyPeriods" :key="item.label" :value="index" :label="item.label" /></el-select></div>
      <Panel :title="'学期历史累计 · ' + currentPeriod.short" :sub="currentPeriod.label + '；历史累计为原表所列各学期之和'" :padding="false">
        <el-table :data="historyRows" :row-class-name="totalRow" max-height="560">
          <el-table-column prop="name" label="学校 / 项目" min-width="260" fixed show-overflow-tooltip />
          <el-table-column label="所选学期回款（元）" min-width="175" align="right"><template #default="{ row }"><b>{{ number(row.values[historyIndex][0]) }}</b></template></el-table-column>
          <el-table-column :label="currentPeriod.rateLabel" min-width="120" align="right"><template #default="{ row }">{{ percent(row.values[historyIndex][1]) }}</template></el-table-column>
          <el-table-column label="历史累计（元）" min-width="160" align="right"><template #default="{ row }">{{ number(row.cumulative) }}</template></el-table-column>
        </el-table>
      </Panel>
      <p class="source-note">{{ history.note }}</p>
    </template>

    <Panel v-else-if="view === 'weekly'" title="周汇总" :sub="weekly.range + ' · 指标：缴费人数'" :padding="false">
      <el-table :data="weeklyRows" :row-class-name="totalRow" max-height="560">
        <el-table-column prop="name" label="学校 / 项目" min-width="240" fixed show-overflow-tooltip />
        <el-table-column v-for="(day, index) in weekly.dates" :key="day" :label="day" min-width="100" align="right"><template #default="{ row }">{{ number(row.values[index]) }}</template></el-table-column>
        <el-table-column label="总计" width="100" align="right" fixed="right"><template #default="{ row }"><b>{{ number(row.sum) }}</b></template></el-table-column>
      </el-table>
    </Panel>

    <Panel v-else title="月汇总" sub="按原表1—11月列示 · 指标：缴费人数" :padding="false">
      <el-table :data="monthlyRows" :row-class-name="totalRow" max-height="560">
        <el-table-column prop="name" label="学校 / 项目" min-width="240" fixed show-overflow-tooltip />
        <el-table-column v-for="(month, index) in monthly.months" :key="month" :label="month" min-width="90" align="right"><template #default="{ row }">{{ number(row.values[index]) }}</template></el-table-column>
        <el-table-column label="总计" width="105" align="right" fixed="right"><template #default="{ row }"><b>{{ number(row.sum) }}</b></template></el-table-column>
      </el-table>
    </Panel>

    <p class="source-note">来源：{{ incomeSource }}。金额与人数按原表显示精度接入，原表空白以“—”表示，五项数据不跨表累加。</p>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import Panel from '@/components/Panel.vue'
import { annual, semester, historyPeriods, history, weekly, monthly, incomeSource } from './campusIncomeData'

const views = [
  { key: 'annual', label: '2026年度情况' },
  { key: 'semester', label: '本学期汇总' },
  { key: 'history', label: '学期历史累计' },
  { key: 'weekly', label: '周汇总' },
  { key: 'monthly', label: '月汇总' },
]
const view = ref('annual'), historyIndex = ref(8)
const annualRows = [...annual.rows, annual.total]
const semesterRows = [...semester.rows, semester.total]
const historyRows = [...history.rows, history.total]
const weeklyRows = [...weekly.rows, weekly.total]
const monthlyRows = [...monthly.rows, monthly.total]
const currentPeriod = computed(() => historyPeriods[historyIndex.value])
const summaryCards = computed(() => {
  if (view.value === 'semester') return [
    card('本学期回款', number(semester.total.amount), '元', semester.period),
    card('学生总人数', number(semester.total.students), '人'),
    card('缴费人数', number(semester.total.payers), '人'),
    card('总人数回款率', percent(semester.total.rate)),
  ]
  if (view.value === 'history') return [
    card('所选学期回款', number(history.total.values[historyIndex.value][0]), '元', currentPeriod.value.short),
    card('本学期回款', number(history.total.values[8][0]), '元'),
    card('历史累计回款', number(history.total.cumulative), '元', '按原表所列学期累计'),
    card('已提供学期', historyPeriods.length, '个'),
  ]
  if (view.value === 'weekly') return [
    card('本周缴费人数', number(weekly.total.sum), '人', weekly.range),
    card('日均缴费人数', number(weekly.total.sum / 7), '人'),
    card('单日最高', number(Math.max(...weekly.total.values)), '人', '11月9日'),
    card('上榜学校 / 项目', weekly.rows.length, '个'),
  ]
  if (view.value === 'monthly') return [
    card('1—11月缴费人数', number(monthly.total.sum), '人'),
    card('8月缴费人数', number(monthly.total.values[7]), '人'),
    card('9月缴费人数', number(monthly.total.values[8]), '人'),
    card('11月缴费人数', number(monthly.total.values[10]), '人'),
  ]
  return [
    card('2026年营收', number(annual.total.currentYear), '元'),
    card('较2025年增长', percent(annual.total.growth), '', number(annual.total.growthAmount) + ' 元'),
    card('本年目标金额', number(annual.total.target), '元'),
    card('目标完成率', percent(annual.total.targetRate)),
  ]
})
function card(label, value, unit = '', hint = '') { return { label, value, unit, hint } }
function number(value) { return value === null || value === undefined ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) }
function percent(value) { return value === null || value === undefined ? '—' : (Number(value) * 100).toFixed(1) + '%' }
function tone(value) { return value > 0 ? 'jc-up' : value < 0 ? 'jc-down' : '' }
function totalRow({ row }) { return row.isTotal ? 'is-total' : '' }
</script>

<style scoped>
.income-report { padding-top: 14px; }
.report-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; margin-bottom: 16px; }
.report-heading h3 { margin: 0 0 6px; font-size: 17px; }
.report-heading p { margin: 0; color: var(--jc-ink-3); font-size: 12px; }
.source-name { flex-shrink: 0; padding: 5px 9px; border: 1px solid var(--jc-line); border-radius: 6px; color: var(--jc-ink-3); font-size: 12px; }
.report-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.report-tabs button { border: 1px solid var(--jc-line); border-radius: 7px; padding: 9px 18px; background: var(--jc-surface); color: var(--jc-ink-2); font: inherit; font-size: 13px; cursor: pointer; }
.report-tabs button:hover { border-color: var(--jc-brand); color: var(--jc-brand); }
.report-tabs button[aria-selected="true"] { border-color: var(--jc-brand); background: var(--jc-brand); color: #fff; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.summary-card { min-width: 0; padding: 14px 16px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); }
.summary-card > span { color: var(--jc-ink-3); font-size: 12px; }
.summary-card > div { display: flex; align-items: baseline; gap: 5px; margin-top: 9px; }
.summary-card strong { color: var(--jc-ink); font-size: 25px; line-height: 1.15; }
.summary-card small { color: var(--jc-ink-3); }
.summary-card p { min-height: 16px; margin: 7px 0 0; color: var(--jc-ink-3); font-size: 12px; }
.period-filter { display: flex; align-items: center; gap: 10px; margin: 0 0 14px; color: var(--jc-ink-2); font-size: 13px; }
.source-note { margin: 12px 0 0; color: var(--jc-ink-3); font-size: 12px; line-height: 1.7; }
.income-report :deep(.el-table .is-total) { --el-table-tr-bg-color: var(--jc-bg-2, #f7f7f4); font-weight: 600; }
.income-report :deep(.el-table .cell) { white-space: nowrap; }
@media (max-width: 1000px) { .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) {
  .report-heading { display: block; }.source-name { display: inline-block; margin-top: 10px; }
  .report-tabs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }.report-tabs button { padding: 9px 8px; }
  .summary-grid { grid-template-columns: 1fr 1fr; gap: 8px; }.summary-card { padding: 12px; }.summary-card strong { font-size: 21px; }
  .period-filter { align-items: flex-start; flex-direction: column; }.period-filter .el-select { width: 100% !important; }
}
</style>
