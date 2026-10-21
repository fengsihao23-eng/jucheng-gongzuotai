<!-- 人事概览：KPI + 人员结构 + 入离职趋势 + 编制 + 近期到期 -->
<template>
  <div v-loading="loading" class="jc-stack">
    <div class="jc-grid c5">
      <Kpi label="在职人数" :value="k.active" unit="人" :hint="`编制 ${k.planned ?? '—'} 人`" />
      <Kpi label="编制满编率" :value="k.fill_rate != null ? (k.fill_rate * 100).toFixed(1) : '—'" unit="%" :progress="k.fill_rate" hint="实际 / 年度编制" />
      <Kpi label="本月入职 / 离职" :value="`${k.month_hires ?? 0} / ${k.month_leaves ?? 0}`" unit="人"
        :hint="`年度离职率 ${k.year_leave_rate != null ? (k.year_leave_rate * 100).toFixed(1) : '—'}%`" />
      <Kpi label="考勤异常" :value="k.attendance_exceptions" unit="次" :hint="`${k.attendance_period || '—'} 迟到/早退/缺卡/旷工`"
        @click="$emit('go', 'attendance')" class="link" />
      <Kpi label="到期预警" :value="k.alerts" unit="条" hint="合同 / 试用期待处理" @click="$emit('go', 'alerts')" class="link" />
    </div>
    <div class="jc-grid c3">
      <Panel title="部门人数"><Chart :option="deptOpt" height="260px" :empty="!st.dept?.length" /></Panel>
      <Panel title="学历结构"><Chart :option="pie(st.education)" height="260px" :empty="!st.education?.length" /></Panel>
      <Panel title="司龄结构"><Chart :option="pie(st.tenure)" height="260px" :empty="!st.tenure?.length" /></Panel>
    </div>
    <div class="jc-grid c2">
      <Panel title="入离职趋势" sub="近 12 个月"><Chart :option="trendOpt" height="280px" /></Panel>
      <Panel title="编制 vs 实际" :padding="false">
        <el-table :data="data.headcount || []" :row-class-name="({ row }) => (row.gap > 0 ? 'is-alert' : '')" size="small">
          <el-table-column prop="dept_name" label="部门" />
          <el-table-column prop="planned" label="编制" width="70" align="right" />
          <el-table-column prop="actual" label="实际" width="70" align="right" />
          <el-table-column label="差额" width="80" align="right">
            <template #default="{ row }"><span :class="row.gap > 0 ? 'jc-down' : row.gap < 0 ? 'jc-warn' : 'jc-muted'">{{ row.gap > 0 ? `缺 ${row.gap}` : row.gap < 0 ? `超 ${-row.gap}` : '满编' }}</span></template>
          </el-table-column>
          <el-table-column label="满编率" width="90" align="right"><template #default="{ row }"><span class="jc-num">{{ row.rate != null ? (row.rate * 100).toFixed(0) + '%' : '—' }}</span></template></el-table-column>
        </el-table>
      </Panel>
    </div>
    <Panel title="近期到期" sub="合同到期 / 试用期届满" :padding="false">
      <template #extra><el-button link type="primary" @click="$emit('go', 'alerts')">全部预警</el-button></template>
      <el-table :data="data.alerts || []" size="small" :row-class-name="({ row }) => (row.days_left <= 7 ? 'is-alert' : '')">
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="dept_name" label="部门" width="120" />
        <el-table-column label="类型" width="120"><template #default="{ row }"><StatusPill :text="row.alert_type_name" :tone="row.alert_type === 'contract' ? 'brand' : 'warn'" /></template></el-table-column>
        <el-table-column label="到期日" width="120"><template #default="{ row }"><span class="jc-num">{{ row.due_date }}</span></template></el-table-column>
        <el-table-column label="剩余" width="90"><template #default="{ row }"><span :class="row.days_left <= 7 ? 'jc-down' : ''">{{ row.days_left }} 天</span></template></el-table-column>
        <el-table-column label="通知部门负责人"><template #default="{ row }">{{ row.leaders.join('、') || '—' }}</template></el-table-column>
      </el-table>
    </Panel>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '@/api/client'
import { PALETTE, baseGrid } from '@/utils/chart'

defineEmits(['go'])
const loading = ref(false)
const data = ref({})
const k = computed(() => data.value.kpi || {})
const st = computed(() => data.value.structure || {})

const pie = (arr = []) => ({
  tooltip: { trigger: 'item', formatter: '{b}：{c} 人（{d}%）' },
  legend: { bottom: 0 },
  series: [{ type: 'pie', radius: ['45%', '70%'], center: ['50%', '45%'], label: { formatter: '{b}\n{c}' }, data: arr }],
})
const deptOpt = computed(() => ({
  grid: baseGrid({ top: 12 }), tooltip: { trigger: 'axis' },
  xAxis: { type: 'value' }, yAxis: { type: 'category', data: (st.value.dept || []).map((x) => x.name).reverse() },
  series: [{ type: 'bar', data: (st.value.dept || []).map((x) => x.value).reverse(), itemStyle: { color: PALETTE[0] }, label: { show: true, position: 'right' } }],
}))
const trendOpt = computed(() => {
  const rows = data.value.turnover || []
  return {
    grid: baseGrid({ top: 36 }), tooltip: { trigger: 'axis' }, legend: { top: 0 },
    xAxis: { type: 'category', data: rows.map((r) => r.period.slice(2)) },
    yAxis: [{ type: 'value', minInterval: 1 }, { type: 'value', name: '在职', splitLine: { show: false } }],
    series: [
      { name: '入职', type: 'bar', data: rows.map((r) => r.hires) },
      { name: '离职', type: 'bar', data: rows.map((r) => r.leaves) },
      { name: '月末在职', type: 'line', yAxisIndex: 1, data: rows.map((r) => r.end) },
    ],
  }
})
onMounted(async () => {
  loading.value = true
  try { data.value = await api.get('/hr/overview') } finally { loading.value = false }
})
</script>

<style scoped>
.link { cursor: pointer; }
</style>
