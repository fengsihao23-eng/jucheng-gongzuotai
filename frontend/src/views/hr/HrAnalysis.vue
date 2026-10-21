<!-- 人力分析：编制、入离职率（月度/年度）、人员结构、人效；生成月度人力分析 PPT -->
<template>
  <div v-loading="loading" class="jc-stack">
    <Panel>
      <div class="jc-row between wrap">
        <div class="jc-row">
          <span class="jc-muted">统计期间</span>
          <el-date-picker v-model="period" type="month" value-format="YYYY-MM" :clearable="false" style="width: 140px" @change="load" />
        </div>
        <div class="jc-row">
          <FileLink v-if="ppt" :file="ppt" />
          <el-button v-if="auth.can('hr:write')" type="primary" :loading="building" @click="buildPpt">生成月度人力分析 PPT</el-button>
        </div>
      </div>
    </Panel>
    <template v-if="a">
      <div class="jc-grid c4">
        <Kpi label="满编率" :value="(a.headcount.rate * 100).toFixed(1)" unit="%" :hint="`实际 ${a.headcount.actual} / 编制 ${a.headcount.planned}`" :progress="a.headcount.rate" />
        <Kpi label="年度离职率" :value="(a.turnover.year.leave_rate * 100).toFixed(1)" unit="%" :hint="`入职 ${a.turnover.year.hires} · 离职 ${a.turnover.year.leaves} · 平均在职 ${a.turnover.year.avg_headcount}`" />
        <Kpi label="人均回款" :value="wanNum(a.efficiency.per_capita_payment)" unit="万" :hint="`${a.efficiency.year} 年回款 ${wan(a.efficiency.payment_total)}`" />
        <Kpi label="人均签约额" :value="wanNum(a.efficiency.per_capita_sign)" unit="万" :hint="`${a.efficiency.year} 年签约 ${wan(a.efficiency.sign_total)}`" />
      </div>
      <div class="jc-grid c2">
        <Panel title="编制 vs 实际"><Chart :option="hcOpt" height="300px" /></Panel>
        <Panel title="月度入离职率" sub="入职率 = 入职 / 平均在职；离职率同理"><Chart :option="rateOpt" height="300px" /></Panel>
      </div>
      <div class="jc-grid c4">
        <Panel title="学历"><Chart :option="pie(a.structure.education)" height="220px" /></Panel>
        <Panel title="司龄"><Chart :option="pie(a.structure.tenure)" height="220px" /></Panel>
        <Panel title="性别"><Chart :option="pie(a.structure.gender)" height="220px" /></Panel>
        <Panel title="用工类型"><Chart :option="pie(a.structure.employ_type)" height="220px" /></Panel>
      </div>
      <p class="jc-muted jc-small">口径：{{ a.efficiency.caliber }}；在职以人事档案入职/离职日期判定，编制取 hr_headcount_plan 当年计划。</p>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { baseGrid } from '@/utils/chart'
import { wan, wanNum } from '@/utils/format'

const auth = useAuth()
const period = ref(dayjs().subtract(1, 'month').format('YYYY-MM'))
const a = ref(null)
const ppt = ref(null)
const loading = ref(false)
const building = ref(false)
const pie = (arr = []) => ({
  tooltip: { trigger: 'item', formatter: '{b}：{c} 人（{d}%）' },
  series: [{ type: 'pie', radius: ['40%', '68%'], label: { formatter: '{b} {c}' }, data: arr }],
})
const hcOpt = computed(() => {
  const rows = a.value?.headcount.rows || []
  return { grid: baseGrid({ top: 36 }), legend: { top: 0 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: rows.map((r) => r.dept_name) }, yAxis: { type: 'value', minInterval: 1 },
    series: [{ name: '编制', type: 'bar', data: rows.map((r) => r.planned) }, { name: '实际', type: 'bar', data: rows.map((r) => r.actual) }] }
})
const rateOpt = computed(() => {
  const rows = a.value?.turnover.rows || []
  return { grid: baseGrid({ top: 36 }), legend: { top: 0 }, tooltip: { trigger: 'axis', valueFormatter: (v) => v + '%' },
    xAxis: { type: 'category', data: rows.map((r) => r.period.slice(2)) }, yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    series: [{ name: '入职率', type: 'line', data: rows.map((r) => +(r.hire_rate * 100).toFixed(1)) },
      { name: '离职率', type: 'line', data: rows.map((r) => +(r.leave_rate * 100).toFixed(1)) }] }
})
async function load() {
  loading.value = true
  ppt.value = null
  try { a.value = await api.get('/hr/analysis', { period: period.value }) } finally { loading.value = false }
}
async function buildPpt() {
  building.value = true
  try {
    const r = await api.post('/hr/analysis/ppt', { period: period.value })
    ppt.value = r.file
    ElMessage.success('PPT 已生成')
  } finally { building.value = false }
}
onMounted(load)
</script>
