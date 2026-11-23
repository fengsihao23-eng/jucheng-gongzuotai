<!-- 考勤核对：上传企微考勤 Excel → 异常清单 / 汇总 / 跨表核对 → 汇总表下载；规则配置抽屉 -->
<template>
  <div class="jc-stack">
    <div class="jc-grid c3">
      <Panel title="上传考勤 Excel" sub="企微考勤导出（姓名/账号/日期/上班打卡/下班打卡/审批单）" class="jc-span-2">
        <div class="jc-row up">
          <UploadBox ref="ub" accept=".xlsx,.xlsm" tip="xlsx；同一期间重新上传将覆盖原明细" style="flex: 1" @change="(f) => (file = f)" />
          <div class="jc-stack" style="gap: 8px; width: 200px">
            <el-input v-model="forcePeriod" placeholder="考勤期间（自动识别）" clearable />
            <el-button v-if="auth.can('hr:attendance')" type="primary" :disabled="!file" :loading="importing" @click="doImport">上传并核对</el-button>
          </div>
        </div>
        <div class="pull-row">
          <span class="jc-small jc-muted">也可以直接从企业微信打卡拉取（原始打卡，不含请假审批）</span>
          <el-button v-if="auth.can('hr:attendance')" size="small" :loading="pulling" @click="pullWecom">
            从企业微信拉取
          </el-button>
        </div>
      </Panel>
      <Panel title="判定规则" sub="与《考勤管理制度》一致，可在线修改">
        <template #extra><el-button v-if="auth.can('hr:attendance')" link type="primary" @click="openRule">配置</el-button></template>
        <ul class="rules jc-small">
          <li v-for="t in ruleInfo.text" :key="t">{{ t }}</li>
        </ul>
      </Panel>
    </div>

    <Panel :loading="loading">
      <template #extra>
        <el-select v-model="period" style="width: 130px" @change="load">
          <el-option v-for="p in periodOptions" :key="p" :label="p" :value="p" />
        </el-select>
        <el-button v-if="auth.can('hr:attendance') && res" @click="recheck">按当前规则重新核对</el-button>
        <el-button v-if="res" type="primary" plain @click="download">下载考勤汇总表</el-button>
      </template>
      <template v-if="res">
        <div class="jc-grid c6 mb-16">
          <div v-for="m in metrics" :key="m.label" class="metric">
            <div class="jc-muted jc-small">{{ m.label }}</div>
            <div class="jc-num v" :class="m.tone">{{ m.value }}</div>
          </div>
        </div>
        <p class="analysis">{{ res.analysis }}</p>
        <el-tabs v-model="sub">
          <el-tab-pane :label="`异常清单 ${res.exceptions.length}`" name="exc">
            <div class="jc-row mb-8">
              <el-checkbox-group v-model="types" size="small">
                <el-checkbox-button v-for="t in ['迟到', '早退', '缺卡', '旷工']" :key="t" :value="t">{{ t }}</el-checkbox-button>
              </el-checkbox-group>
              <el-input v-model="kw" size="small" placeholder="姓名/部门" clearable style="width: 160px" />
            </div>
            <el-table :data="excFiltered" size="small" max-height="460" :row-class-name="({ row }) => (row.types.includes('旷工') ? 'is-alert' : '')">
              <el-table-column label="日期" width="120"><template #default="{ row }"><span class="jc-num">{{ row.work_date }}</span> <span class="jc-muted">周{{ row.weekday }}</span></template></el-table-column>
              <el-table-column prop="dept_name" label="部门" width="100" />
              <el-table-column prop="name" label="姓名" width="90" />
              <el-table-column label="上班" width="80"><template #default="{ row }"><span :class="{ 'jc-down': !row.check_in }">{{ row.check_in || '未打卡' }}</span></template></el-table-column>
              <el-table-column label="下班" width="80"><template #default="{ row }"><span :class="{ 'jc-down': !row.check_out }">{{ row.check_out || '未打卡' }}</span></template></el-table-column>
              <el-table-column label="异常" min-width="160">
                <template #default="{ row }"><StatusPill v-for="t in row.types" :key="t" :text="t" :tone="TONE[t]" class="pl" /></template>
              </el-table-column>
              <el-table-column label="迟到/早退(分)" width="110" align="right"><template #default="{ row }"><span class="jc-num">{{ row.late_minutes || row.early_minutes || '—' }}</span></template></el-table-column>
              <el-table-column label="请假" width="120"><template #default="{ row }">{{ row.leave_type ? `${row.leave_type.replace('/', '·')} ${+row.leave_hours}h` : '—' }}</template></el-table-column>
              <el-table-column prop="source_row" label="源行" width="70" align="right" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`月度汇总 ${res.summary_rows.length}`" name="sum">
            <el-table :data="res.summary_rows" @row-click="row => followupRow = row" size="small" max-height="480" :row-class-name="({ row }) => (row.absent_times ? 'is-alert' : '')">
              <el-table-column prop="dept_name" label="部门" width="100" />
              <el-table-column prop="name" label="姓名" width="90" />
              <el-table-column prop="work_days" label="应出勤" width="80" align="right" />
              <el-table-column prop="actual_days" label="实际打卡" width="90" align="right" />
              <el-table-column prop="late_times" label="迟到" width="70" align="right" />
              <el-table-column prop="early_times" label="早退" width="70" align="right" />
              <el-table-column prop="missing_times" label="缺卡" width="70" align="right" />
              <el-table-column prop="absent_times" label="旷工" width="70" align="right" />
              <el-table-column label="请假(天)" width="90" align="right"><template #default="{ row }">{{ +row.leave_days }}</template></el-table-column>
              <el-table-column label="加班(时)" width="90" align="right"><template #default="{ row }">{{ +row.overtime_hours }}</template></el-table-column>
              <el-table-column prop="abnormal" label="异常合计" align="right" sortable />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`跨表核对 ${cross.items.length}`" name="cross">
            <p class="jc-muted jc-small">考勤 × 员工花名册 × 请假审批比对：在职无打卡、打卡人不在花名册、离职后打卡、全天请假仍打卡、企微请假审批未体现。</p>
            <el-table :data="cross.items" size="small">
              <el-table-column label="类型" width="150"><template #default="{ row }"><StatusPill :text="cross.labels?.[row.kind] || row.kind" tone="warn" /></template></el-table-column>
              <el-table-column label="姓名/账号" width="140"><template #default="{ row }">{{ row.name || row.account || '—' }}</template></el-table-column>
              <el-table-column prop="message" label="说明" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`行级错误 ${res.import.row_errors.length}`" name="err">
            <el-table :data="res.import.row_errors" size="small">
              <el-table-column prop="row" label="行号" width="80" />
              <el-table-column label="姓名" width="100"><template #default="{ row }">{{ row.name || '—' }}</template></el-table-column>
              <el-table-column prop="message" label="错误" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
      <div v-else class="empty jc-muted">{{ period }} 尚未导入考勤数据，请上传企微考勤导出 Excel。</div>
    </Panel>

    <el-drawer v-model="ruleOpen" title="考勤判定规则" size="480px">
      <el-form label-position="top" :model="rf">
        <div class="jc-grid c2">
          <el-form-item label="上班时间"><el-time-select v-model="rf.work_start" start="06:00" step="00:15" end="12:00" /></el-form-item>
          <el-form-item label="下班时间"><el-time-select v-model="rf.work_end" start="15:00" step="00:15" end="22:00" /></el-form-item>
          <el-form-item label="迟到宽限（分钟）"><el-input-number v-model="rf.late_grace_minutes" :min="0" :max="60" /></el-form-item>
          <el-form-item label="早退宽限（分钟）"><el-input-number v-model="rf.early_grace_minutes" :min="0" :max="60" /></el-form-item>
          <el-form-item label="超过该分钟记旷工半天"><el-input-number v-model="rf.absent_threshold_minutes" :min="30" :max="480" :step="10" /></el-form-item>
          <el-form-item label="加班起算时间"><el-time-select v-model="rf.overtime_start" start="17:00" step="00:30" end="23:00" /></el-form-item>
          <el-form-item label="加班最小计量（分钟）"><el-input-number v-model="rf.overtime_min_minutes" :min="0" :max="240" :step="10" /></el-form-item>
          <el-form-item label="加班计量粒度（分钟）"><el-input-number v-model="rf.overtime_unit_minutes" :min="1" :max="120" :step="5" /></el-form-item>
          <el-form-item label="请假每天折算（小时）"><el-input-number v-model="rf.leave_hours_per_day" :min="4" :max="12" /></el-form-item>
        </div>
        <el-form-item><el-checkbox v-model="rf.no_punch_absent_full_day">工作日全天无打卡且无请假记旷工 1 天</el-checkbox></el-form-item>
        <el-form-item><el-checkbox v-model="rf.weekend_overtime">休息日打卡全程计加班</el-checkbox></el-form-item>
        <el-form-item label="法定节假日（逗号分隔 YYYY-MM-DD）"><el-input v-model="holidays" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="调休上班日"><el-input v-model="makeups" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRule">保存并重新核对</el-button>
      </template>
    </el-drawer>
    <RecordRowDetails v-model:row="followupRow" kind="hr-attendance" title="月度考勤详情" heading-field="name"
      :ident="followupRow ? `${period}:${followupRow.user_id}` : undefined"
      :fields="{ dept_name: '部门', work_days: '应出勤', actual_days: '实际打卡', late_times: '迟到', early_times: '早退', missing_times: '缺卡', absent_times: '旷工' }"
      progress-hint="记录考勤异常的核对结果及后续处理。" />
  </div>
</template>

<script setup>
import RecordRowDetails from '@/components/records/RecordRowDetails.vue'
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import api, { openFile } from '@/api/client'
import { useAuth } from '@/store/auth'

const auth = useAuth()
const TONE = { 迟到: 'warn', 早退: 'warn', 缺卡: 'info', 旷工: 'down', 请假: 'brand', 加班: 'up' }
const prevP = dayjs().subtract(1, 'month').format('YYYY-MM')
const followupRow = ref(null)
const period = ref(prevP)
const periods = ref([])
const periodOptions = computed(() => [...new Set([dayjs().format('YYYY-MM'), prevP, ...periods.value.map((p) => p.period)])].sort().reverse())
const res = ref(null)
const cross = ref({ items: [], labels: {} })
const loading = ref(false)
const file = ref(null)
const ub = ref()
const forcePeriod = ref('')
const importing = ref(false)
const pulling = ref(false)
const sub = ref('exc')
const types = ref([])
const kw = ref('')
const ruleInfo = ref({ text: [] })
const ruleOpen = ref(false)
const rf = ref({})
const holidays = ref('')
const makeups = ref('')
const saving = ref(false)

const metrics = computed(() => {
  const t = res.value?.totals || {}
  return [
    { label: '核对人数', value: res.value?.summary_rows.length ?? 0 },
    { label: '迟到', value: t.late_times ?? 0, tone: 'jc-warn' },
    { label: '早退', value: t.early_times ?? 0, tone: 'jc-warn' },
    { label: '缺卡', value: t.missing_times ?? 0 },
    { label: '旷工', value: t.absent_times ?? 0, tone: 'jc-down' },
    { label: '加班(时) / 请假(天)', value: `${+(t.overtime_hours || 0)} / ${+(t.leave_days || 0)}` },
  ]
})
const excFiltered = computed(() => (res.value?.exceptions || []).filter((e) =>
  (!types.value.length || e.types.some((t) => types.value.includes(t))) &&
  (!kw.value || e.name.includes(kw.value) || (e.dept_name || '').includes(kw.value))))

async function load() {
  loading.value = true
  try {
    res.value = await api.get('/hr/attendance/summary', { period: period.value }, { silent: true })
    cross.value = await api.get('/hr/attendance/crosscheck', { period: period.value })
  } catch {
    res.value = null
    cross.value = { items: [], labels: {} }
  } finally { loading.value = false }
}
async function loadRule() { ruleInfo.value = await api.get('/hr/attendance/rule') }
async function loadPeriods() { periods.value = await api.get('/hr/attendance/periods') }

async function pullWecom() {
  const target = forcePeriod.value || period.value
  if (!target) {
    ElMessage.warning('请先填写或选择考勤期间')
    return
  }
  pulling.value = true
  try {
    const r = await api.post('/hr/attendance/pull-wecom', { period: target })
    ElMessage.success(`已从企业微信拉取 ${target}：${r.pull.records} 条明细，异常 ${r.pull.exception_rows} 条`)
    period.value = target
    await loadPeriods()
    await load()
  } finally {
    pulling.value = false
  }
}

async function doImport() {
  importing.value = true
  try {
    const r = await api.upload('/hr/attendance/import', file.value, { period: forcePeriod.value || undefined })
    ElMessage.success(`已导入 ${r.period}：${r.import.total_rows} 行，异常 ${r.import.exception_rows} 行`)
    period.value = r.period
    ub.value?.reset()
    file.value = null
    await loadPeriods()
    await load()
  } finally { importing.value = false }
}
async function recheck() {
  const r = await api.post('/hr/attendance/recheck', { period: period.value })
  ElMessage.success(`已按当前规则重新核对，异常 ${r.exception_rows} 行`)
  await load()
}
async function download() {
  const f = await api.get('/hr/attendance/export', { period: period.value })
  openFile(f.url)
}
function openRule() {
  rf.value = { ...ruleInfo.value.rule }
  holidays.value = (rf.value.holidays || []).join(', ')
  makeups.value = (rf.value.makeup_workdays || []).join(', ')
  ruleOpen.value = true
}
const split = (s) => s.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)
async function saveRule() {
  saving.value = true
  try {
    await api.put('/hr/attendance/rule', { rule: { ...rf.value, holidays: split(holidays.value), makeup_workdays: split(makeups.value) } })
    await loadRule()
    ruleOpen.value = false
    if (res.value) await recheck()
    else ElMessage.success('规则已保存')
  } finally { saving.value = false }
}
onMounted(() => { loadRule(); loadPeriods(); load() })
</script>

<style scoped>
.up { align-items: stretch; }
.pull-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px;
  padding-top: 10px; border-top: 1px dashed var(--jc-line-2); }
.rules { margin: 0; padding-left: 16px; color: var(--jc-ink-2); line-height: 1.7; }
.metric { border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); padding: 10px 14px; background: var(--jc-surface-2); }
.metric .v { font-size: 22px; font-weight: 600; margin-top: 4px; }
.analysis { white-space: pre-line; margin: 0 0 8px; color: var(--jc-ink-2); font-size: 13px; line-height: 1.7; }
.pl { margin-right: 4px; }
.empty { padding: 48px 0; text-align: center; }
</style>
