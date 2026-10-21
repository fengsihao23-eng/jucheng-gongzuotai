<!-- 汇报提交（FR-07-05）：周/月/日报提交率（部门/人员）、未提交名单 + 一键催交、近期趋势、规则总结 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="总经办 · 汇报管理" title="汇报提交"
                desc="以企业微信「汇报」提交记录为准；应提交名单按字典 report_rule 配置。每周五 17:00 自动识别未提交并提醒。">
      <el-segmented v-model="type" :options="TYPE_OPTS" @change="onType" />
      <el-select v-model="period" style="width: 150px" @change="load">
        <el-option v-for="p in periods" :key="p" :label="p" :value="p" />
      </el-select>
      <el-button v-if="d?.can_remind" :loading="syncing" @click="sync">同步企微汇报</el-button>
    </PageHeader>

    <div v-loading="loading && !d">
      <template v-if="d">
        <div class="jc-grid c4">
          <Kpi :label="`${d.type_name}提交率`" :value="pct(d.rate)" unit="%" :progress="d.rate"
               :delta="d.prev_rate != null && d.rate != null ? d.rate - d.prev_rate : null"
               :delta-text="d.prev_rate != null ? `${Math.abs((d.rate - d.prev_rate) * 100).toFixed(1)} pp` : null"
               :hint="`较 ${d.prev_period}`" />
          <Kpi label="应提交 / 已提交" :value="`${d.submitted} / ${d.required}`" unit="人" :hint="`迟交 ${d.late} 人`" />
          <Kpi label="未提交" :value="d.missing.length" unit="人" :hint="`连续两期未交 ${d.missing.filter((m) => m.consecutive).length} 人`" />
          <Kpi label="截止时间" :value="dayjs(d.deadline).format('MM-DD HH:mm')" :hint="deadlineHint" />
        </div>

        <Panel class="mt-16" title="提交情况总结" sub="规则生成：提交率、较上期变化、最低部门、连续未交人员">
          <p class="jc-quote summary">{{ d.summary }}</p>
        </Panel>

        <div class="jc-grid c2 mt-16">
          <Panel title="各部门提交率" :sub="d.period">
            <Chart :option="deptOption" :height="Math.max(220, d.by_dept.length * 34) + 'px'" />
          </Panel>
          <Panel :title="`近 ${d.trend.length} 期趋势`">
            <Chart :option="trendOption" height="260px" />
          </Panel>
        </div>

        <div class="jc-grid c2 mt-16">
          <Panel :title="`未提交名单（${d.missing.length} 人）`" :padding="false">
            <template #extra>
              <template v-if="d.can_remind && d.missing.length">
                <el-button size="small" :disabled="!selected.length" :loading="reminding" @click="remind(selected)">提醒所选（{{ selected.length }}）</el-button>
                <el-button size="small" type="primary" :loading="reminding" @click="remind(null)">一键催交</el-button>
              </template>
            </template>
            <el-table :data="d.missing" max-height="420" :row-class-name="({ row }) => (row.consecutive ? 'is-alert' : '')"
                      empty-text="全部已提交" @selection-change="(v) => (selected = v.map((x) => x.user_id))">
              <el-table-column v-if="d.can_remind" type="selection" width="40" />
              <el-table-column label="姓名" width="100" prop="name" />
              <el-table-column label="部门" width="110" prop="dept_name" />
              <el-table-column label="岗位" min-width="120" prop="position" />
              <el-table-column label="上期" width="110">
                <template #default="{ row }"><StatusPill v-if="row.consecutive" tone="down" text="连续未交" /><span v-else class="jc-muted jc-small">上期已交</span></template>
              </el-table-column>
            </el-table>
          </Panel>
          <Panel title="催交记录" :padding="false" :sub="d.period">
            <el-table :data="d.remind_history" max-height="420" empty-text="本期尚未催交">
              <el-table-column label="人员" width="100" prop="user" />
              <el-table-column label="内容" min-width="180" prop="title" />
              <el-table-column label="时间" width="150"><template #default="{ row }">{{ datetime(row.sent_at) }}</template></el-table-column>
            </el-table>
          </Panel>
        </div>

        <Panel class="mt-16" title="人员明细" :padding="false">
          <template #extra>
            <el-select v-model="deptFilter" placeholder="全部部门" clearable size="small" style="width: 140px">
              <el-option v-for="x in d.by_dept" :key="x.dept_code" :label="x.dept_name" :value="x.dept_code" />
            </el-select>
          </template>
          <el-table :data="people" max-height="480">
            <el-table-column label="姓名" width="100" prop="name" />
            <el-table-column label="部门" width="110" prop="dept_name" />
            <el-table-column label="岗位" min-width="140" prop="position" />
            <el-table-column label="本期" width="110">
              <template #default="{ row }">
                <StatusPill v-if="row.submitted" :tone="row.late ? 'warn' : 'up'" :text="row.late ? '迟交' : '已提交'" />
                <StatusPill v-else tone="down" text="未提交" />
              </template>
            </el-table-column>
            <el-table-column label="提交时间" width="150"><template #default="{ row }">{{ row.submit_time ? datetime(row.submit_time) : '—' }}</template></el-table-column>
            <el-table-column label="上期" width="90"><template #default="{ row }"><span :class="row.prev_submitted ? 'jc-muted' : 'jc-down'">{{ row.prev_submitted ? '已交' : '未交' }}</span></template></el-table-column>
          </el-table>
        </Panel>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'
import { baseGrid } from '@/utils/chart'

const TYPE_OPTS = [{ label: '周报', value: 'weekly' }, { label: '月报', value: 'monthly' }, { label: '日报', value: 'daily' }]
const route = useRoute()
const router = useRouter()
const type = ref(route.query.type || 'weekly')
const period = ref(route.query.period || '')
const periods = ref([])
const d = ref(null)
const loading = ref(false); const syncing = ref(false); const reminding = ref(false)
const selected = ref([])
const deptFilter = ref('')

const pct = (v) => (v == null ? '—' : (v * 100).toFixed(1))
const deadlineHint = computed(() => (d.value && dayjs().isBefore(dayjs(d.value.deadline)) ? '尚未截止' : '已截止'))
const people = computed(() => (d.value?.people || []).filter((p) => !deptFilter.value || p.dept_code === deptFilter.value)
  .sort((a, b) => a.submitted - b.submitted))

async function load() {
  loading.value = true
  try {
    d.value = await api.get('/submissions', { type: type.value, period: period.value || undefined })
    periods.value = d.value.periods
    period.value = d.value.period
    selected.value = []
    router.replace({ query: { type: type.value, period: period.value } })
  } finally { loading.value = false }
}
function onType() { period.value = ''; deptFilter.value = ''; load() }
async function sync() {
  syncing.value = true
  try { const r = await api.post('/submissions/sync', { hours: 24 * 14 }); ElMessage.success(r.message); load() } finally { syncing.value = false }
}
async function remind(ids) {
  const n = ids ? ids.length : d.value.missing.length
  await ElMessageBox.confirm(`将通过企业微信向 ${n} 人发送${d.value.type_name}催交提醒，确认发送？`, '一键催交', { type: 'warning' })
  reminding.value = true
  try {
    const r = await api.post('/submissions/remind', { type: type.value, period: period.value, user_ids: ids })
    ElMessage.success(`已提醒 ${r.count} 人`)
    load()
  } finally { reminding.value = false }
}

const deptOption = computed(() => {
  const rows = [...(d.value?.by_dept || [])].reverse()
  return {
    grid: baseGrid({ top: 6, right: 70 }),
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (p) => { const r = rows[p[0].dataIndex]; return `${r.dept_name}<br/>${r.submitted}/${r.required} 人${r.missing_names.length ? '<br/>未交：' + r.missing_names.join('、') : ''}` } },
    xAxis: { type: 'value', max: 1, axisLabel: { formatter: (v) => v * 100 + '%' } },
    yAxis: { type: 'category', data: rows.map((r) => r.dept_name) },
    series: [{ type: 'bar', barMaxWidth: 16,
               data: rows.map((r) => ({ value: r.rate ?? 0, itemStyle: { color: (r.rate ?? 0) < 0.6 ? '#a8741a' : '#1B3275', borderRadius: [0, 3, 3, 0] } })),
               label: { show: true, position: 'right', formatter: (p) => `${(p.value * 100).toFixed(0)}%  ${rows[p.dataIndex].submitted}/${rows[p.dataIndex].required}` } }],
  }
})
const trendOption = computed(() => {
  const t = d.value?.trend || []
  return {
    grid: baseGrid({ top: 20 }), tooltip: { trigger: 'axis', valueFormatter: (v) => (v * 100).toFixed(1) + '%' },
    xAxis: { type: 'category', data: t.map((x) => x.period.replace(/^\d{4}-/, '')) },
    yAxis: { type: 'value', max: 1, axisLabel: { formatter: (v) => v * 100 + '%' } },
    series: [{ type: 'line', name: '提交率', data: t.map((x) => x.rate ?? 0), lineStyle: { color: '#1B3275' }, itemStyle: { color: '#1B3275' },
               label: { show: true, formatter: (p) => (p.value * 100).toFixed(0) + '%', fontSize: 11 } }],
  }
})

onMounted(load)
</script>

<style scoped>
.summary { margin: 0; line-height: 1.8; font-size: 14px; }
</style>
