<template>
  <div class="jc-page">
    <PageHeader eyebrow="财务运营" title="出纳日记账" desc="银行日记账由流水核对确认后自动登记，现金日记账手工补录；期初 / 本期收支 / 期末按账户实时计算，与银行对账单余额对照显示未达账项。">
      <el-segmented v-model="mode" :options="[{ label: '日视图', value: 'day' }, { label: '月视图', value: 'month' }, { label: '年视图', value: 'year' }]" @change="load" />
      <el-date-picker v-if="mode === 'day'" v-model="day" type="date" value-format="YYYY-MM-DD" :clearable="false" style="width: 140px" @change="load" />
      <el-date-picker v-else-if="mode === 'month'" v-model="period" type="month" value-format="YYYY-MM" :clearable="false" style="width: 130px" @change="load" />
      <el-date-picker v-else v-model="year" type="year" value-format="YYYY" format="YYYY [年]" aria-label="按年份筛选"
        :clearable="false" style="width: 130px" @change="load" />
      <el-button @click="exportXlsx">导出 xlsx</el-button>
      <el-button v-if="canWrite" type="primary" @click="openForm()">补录现金账</el-button>
    </PageHeader>

    <div class="cards">
      <div class="jc-card acct" :class="{ on: account === '' }" @click="setAccount('')">
        <div class="lbl">全部账户</div>
        <div class="jc-num big">{{ money(view.total?.closing) }}</div>
        <div class="row jc-small"><span>期初 {{ money(view.total?.opening) }}</span></div>
        <div class="row jc-small"><span class="jc-up">收 {{ money(view.total?.income) }}</span><span>支 {{ money(view.total?.expense) }}</span></div>
      </div>
      <div v-for="a in view.accounts || []" :key="a.account" class="jc-card acct" :class="{ on: account === a.account }" @click="setAccount(a.account)">
        <div class="lbl jc-ellipsis" :title="a.name">{{ a.type === 'cash' ? '现金' : a.name }}</div>
        <div class="jc-num big">{{ money(a.closing) }}</div>
        <div class="row jc-small"><span>期初 {{ money(a.opening) }}</span><span>{{ a.count }} 笔</span></div>
        <div class="row jc-small"><span class="jc-up">收 {{ money(a.income) }}</span><span>支 {{ money(a.expense) }}</span></div>
        <div v-if="a.type === 'bank' && a.statement_balance != null" class="row jc-small stmt">
          <span>对账单 {{ money(a.statement_balance) }}</span>
          <span :class="{ 'jc-warn': Number(a.difference) }">{{ Number(a.difference) ? `未达 ${a.unmatched_count} 笔` : '已对平' }}</span>
        </div>
      </div>
    </div>

    <Panel v-if="mode === 'month'" class="mt-16" title="逐日收支" :sub="`${view.start} ~ ${view.end}${account ? ' · ' + accountName : ' · 全部账户'}`">
      <Chart :option="dailyOption" height="240px" :empty="!view.daily?.length" />
    </Panel>

    <Panel class="mt-16" :padding="false" :loading="loading" :title="account ? accountName : '全部账户明细'" :sub="`${view.start}${view.end !== view.start ? ' ~ ' + view.end : ''} · ${view.entries?.length || 0} 笔`">
      <template #extra>
        <el-input v-model="kw" placeholder="摘要 / 单号 / 项目" clearable size="small" style="width: 180px" />
        <el-select v-model="src" size="small" style="width: 110px">
          <el-option label="全部来源" value="" /><el-option label="流水核对" :value="1" /><el-option label="手工" :value="2" />
        </el-select>
      </template>
      <el-table :data="filtered" @row-click="row => followupRow = row" :row-class-name="({ row }) => (row.source_type === 2 ? 'manual' : '')" max-height="620" @row-contextmenu="removeMenu">
        <el-table-column label="日期" width="105" prop="journal_date" />
        <el-table-column v-if="!account" label="账户" width="180" prop="account_name" show-overflow-tooltip />
        <el-table-column label="摘要" min-width="240" prop="summary" show-overflow-tooltip />
        <el-table-column label="类别" width="90" prop="category" />
        <el-table-column label="项目" width="140"><template #default="{ row }"><span class="jc-mono">{{ row.project_code || '' }}</span></template></el-table-column>
        <el-table-column label="关联单号" width="160"><template #default="{ row }"><span class="jc-mono jc-small">{{ row.biz_ref || '' }}</span></template></el-table-column>
        <el-table-column label="收入" width="125" align="right"><template #default="{ row }"><span v-if="row.income" class="jc-num jc-up">{{ money(row.income) }}</span></template></el-table-column>
        <el-table-column label="支出" width="125" align="right"><template #default="{ row }"><span v-if="row.expense" class="jc-num">{{ money(row.expense) }}</span></template></el-table-column>
        <el-table-column v-if="account" label="余额" width="140" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.balance) }}</span></template></el-table-column>
        <el-table-column label="来源" width="90"><template #default="{ row }"><StatusPill :text="row.source_name" :tone="row.source_type === 1 ? 'brand' : ''" /></template></el-table-column>
        <el-table-column label="登记人" width="80" prop="register_by" />
        <el-table-column v-if="canWrite" width="100" fixed="right">
          <template #default="{ row }">
            <template v-if="row.editable">
              <el-button link type="primary" @click.stop="openForm(row)">编辑</el-button>
              <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </Panel>

    <el-dialog v-model="form.open" :title="form.id ? '编辑现金日记账' : '补录现金日记账'" width="480px">
      <el-form label-width="80px">
        <el-form-item label="日期"><el-date-picker v-model="form.journal_date" type="date" value-format="YYYY-MM-DD" :disabled-date="(d) => d > new Date()" /></el-form-item>
        <el-form-item label="方向">
          <el-radio-group v-model="form.direction"><el-radio :value="1">收入</el-radio><el-radio :value="2">支出</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item label="金额"><el-input-number v-model="form.amount" :min="0.01" :precision="2" :controls="false" style="width: 180px" /></el-form-item>
        <el-form-item label="类别">
          <el-select v-model="form.category" style="width: 180px"><el-option v-for="c in categories" :key="c" :value="c" /></el-select>
        </el-form-item>
        <el-form-item label="摘要"><el-input v-model="form.summary" maxlength="120" placeholder="如：备用金-快递费" /></el-form-item>
        <el-form-item label="关联单号"><el-input v-model="form.biz_ref" placeholder="可选" /></el-form-item>
      </el-form>
      <div class="jc-muted jc-small">银行账由流水核对自动生成，此处仅补录现金账；修改与删除均记入操作审计。</div>
      <template #footer>
        <el-button @click="form.open = false">取消</el-button>
        <el-button type="primary" :loading="form.saving" :disabled="!form.amount || !form.summary" @click="save">保存</el-button>
      </template>
    </el-dialog>
    <RecordRowDetails v-model:row="followupRow" kind="journal" title="日记账详情" heading-field="summary"
      :fields="{ account_name: '账户', journal_date: '日期', project_code: '项目编号', biz_ref: '关联单号', register_by: '登记人' }"
      progress-hint="记录账务核对情况和待补资料。">
      <template #actions><el-button v-if="canWrite && followupRow?.editable" @click="openForm(followupRow)">编辑现金账</el-button></template>
    </RecordRowDetails>
  </div>
</template>

<script setup>
import { deleteRecord, rowDeletion } from '@/composables/useRecordDeletion'
import RecordRowDetails from '@/components/records/RecordRowDetails.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { money } from '@/utils/format'
import { PALETTE, baseGrid, wanAxis, wanTooltip } from '@/utils/chart'

const auth = useAuth()
const canWrite = computed(() => auth.can('journal:write'))
const followupRow = ref(null)
const mode = ref('day'); const day = ref(dayjs().format('YYYY-MM-DD')); const period = ref(dayjs().format('YYYY-MM'))
const year = ref(dayjs().format('YYYY'))
const route = useRoute()
const account = ref(''); const view = ref({}); const loading = ref(false)
const categories = ref([]); const kw = ref(''); const src = ref('')
const accountName = computed(() => (account.value === 'cash' ? '现金日记账' : account.value))

async function load() {
  loading.value = true
  try {
    const selectedPeriod = { month: period.value, year: year.value }[mode.value]
    view.value = await api.get('/journal', { mode: mode.value, day: day.value, period: selectedPeriod, account: account.value })
  } finally { loading.value = false }
}
function setAccount(a) { account.value = a; load() }
const filtered = computed(() => (view.value.entries || []).filter((r) =>
  (!src.value || r.source_type === src.value) &&
  (!kw.value || [r.summary, r.biz_ref, r.project_code, r.category].some((x) => (x || '').includes(kw.value)))))

const dailyOption = computed(() => {
  const d = view.value.daily || []
  return {
    grid: baseGrid({ top: 30 }),
    legend: { top: 0, right: 0 },
    tooltip: { trigger: 'axis', valueFormatter: wanTooltip },
    xAxis: { type: 'category', data: d.map((x) => x.date.slice(5)) },
    yAxis: [{ type: 'value', ...wanAxis }, { type: 'value', ...wanAxis, splitLine: { show: false } }],
    series: [
      { name: '收入', type: 'bar', stack: 'a', data: d.map((x) => Number(x.income)), itemStyle: { color: PALETTE[2] } },
      { name: '支出', type: 'bar', stack: 'b', data: d.map((x) => -Number(x.expense)), itemStyle: { color: PALETTE[3] } },
      { name: '余额', type: 'line', yAxisIndex: 1, data: d.map((x) => Number(x.balance)), itemStyle: { color: PALETTE[0] }, symbol: 'none' },
    ],
  }
})

const form = reactive({ open: false, id: null, journal_date: '', direction: 2, amount: null, summary: '', category: '其他', biz_ref: '', saving: false })
function openForm(row) {
  Object.assign(form, row
    ? { id: row.id, journal_date: row.journal_date, direction: row.direction, amount: Number(row.amount), summary: row.summary, category: row.category || '其他', biz_ref: row.biz_ref || '' }
    : { id: null, journal_date: mode.value === 'day' ? day.value : dayjs().format('YYYY-MM-DD'), direction: 2, amount: null, summary: '', category: '其他', biz_ref: '' })
  form.open = true
}
async function save() {
  form.saving = true
  const body = { journal_date: form.journal_date, direction: form.direction, amount: String(form.amount), summary: form.summary, category: form.category, biz_ref: form.biz_ref || null }
  try {
    if (form.id) await api.put(`/journal/${form.id}`, body)
    else await api.post('/journal', body)
    ElMessage.success('已保存'); form.open = false; load()
  } finally { form.saving = false }
}
const removal = row => auth.canDelete('journal:write') && row.source_type === 2 && {
  endpoint: `/journal/${row.id}`, name: row.summary, title: '删除日记账', onDeleted: load,
}
const removeMenu = rowDeletion(removal)
const remove = row => deleteRecord(removal(row))
function exportXlsx() {
  const p = { month: { period: period.value }, year: { start: `${year.value}-01-01`, end: `${year.value}-12-31` } }[mode.value]
    || { start: day.value, end: day.value }
  api.download('/journal/export', p)
}

onMounted(async () => {
  const r = await api.get('/journal/accounts'); categories.value = r.categories
  load()
  if (route.query.new) openForm()
})
</script>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
.acct { padding: 14px 16px; cursor: pointer; transition: border-color 0.15s; }
.acct:hover { border-color: var(--jc-brand-100); }
.acct.on { border-color: var(--jc-brand); box-shadow: 0 0 0 1px var(--jc-brand) inset; }
.acct .lbl { font-size: 13px; color: var(--jc-ink-3); }
.acct .big { font-size: 22px; font-weight: 600; margin: 8px 0 6px; }
.acct .row { display: flex; justify-content: space-between; color: var(--jc-ink-3); margin-top: 2px; }
.acct .stmt { border-top: 1px solid var(--jc-line-2); padding-top: 6px; margin-top: 8px; }
:deep(.el-table__row.manual td.el-table__cell) { background: #fdfcf8; }
</style>
