<template>
  <div class="jc-page">
    <PageHeader eyebrow="财务运营" title="报销归集" desc="企微报销审批通过后按「所属项目」自动归集；缺失或无效项目进入待归集池，由财务补充归集（全程留痕）。公共费用单列，不摊入项目毛利。">
      <el-select v-model="statsMode" aria-label="报销统计周期" style="width: 105px" @change="changeStatsPeriod">
        <el-option label="按月" value="month" /><el-option label="按年" value="year" />
      </el-select>
      <el-date-picker v-if="statsMode === 'year'" v-model="year" type="year" value-format="YYYY" format="YYYY [年]"
        :clearable="false" aria-label="报销统计年份" style="width: 130px" @change="changeStatsPeriod" />
      <el-date-picker v-else v-model="month" type="month" value-format="YYYY-MM" :clearable="false"
        aria-label="报销统计月份" style="width: 130px" @change="changeStatsPeriod" />
      <el-button @click="exportXlsx">导出 xlsx</el-button>
      <el-button type="primary" @click="approvalOpen = true">发起企微报销</el-button>
    </PageHeader>

    <div class="jc-grid c4">
      <Kpi :label="statsMode === 'year' ? '全年报销额' : '当月报销额'" :value="wanNum(kpi.total_amount, 2)" unit="万元" :hint="`${kpi.total_count || 0} 笔已审批`" />
      <Kpi label="已归集率" :value="kpi.allocated_rate == null ? '—' : (kpi.allocated_rate * 100).toFixed(1)" unit="%" :progress="kpi.allocated_rate" hint="按金额，含公共费用" />
      <Kpi label="待归集" :value="kpi.pending_count ?? '—'" unit="笔" :hint="money(kpi.pending_amount)" @click="quick('pending')" class="clickable" />
      <Kpi label="公共费用" :value="wanNum(kpi.public_amount, 2)" unit="万元" :hint="`项目归集 ${wan(kpi.project_amount)}`" />
    </div>

    <div class="jc-grid c2 mt-16">
      <Panel title="按项目归集" :sub="`${stats.start || ''} ~ ${stats.end || ''}`" :loading="statsLoading">
        <Chart :option="projectOption" height="300px" :empty="!stats.by_project?.length" @click="onProjectClick" />
      </Panel>
      <Panel title="按部门 / 类别" sub="审批通过金额">
        <template #extra>
          <el-segmented v-model="dimTab" :options="[{ label: '部门', value: 'dept' }, { label: '类别', value: 'cat' }]" size="small" />
        </template>
        <Chart :option="dimOption" height="300px" :empty="!stats.by_dept?.length" />
      </Panel>
    </div>

    <Panel class="mt-16" :padding="false" :loading="loading">
      <template #extra>
        <el-segmented v-model="f.status" :options="STATUS_OPTS" size="small" @change="load(1)" />
      </template>
      <div class="filters">
        <el-select v-model="f.project" placeholder="归属项目" clearable filterable style="width: 230px" @change="load(1)">
          <el-option label="公共费用" value="__public__" />
          <el-option v-for="p in opts.projects" :key="p.code" :label="`${p.code} ${p.name}`" :value="p.code" />
        </el-select>
        <el-select v-model="f.dept" placeholder="部门" clearable style="width: 120px" @change="load(1)">
          <el-option v-for="d in opts.depts" :key="d.code" :label="d.name" :value="d.code" />
        </el-select>
        <el-select v-model="f.category" placeholder="类别" clearable style="width: 120px" @change="load(1)">
          <el-option v-for="c in opts.categories" :key="c" :label="c" :value="c" />
        </el-select>
        <el-date-picker v-model="f.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="费用日期" end-placeholder="至" style="width: 240px" @change="load(1)" />
        <el-input v-model="f.applicant" placeholder="申请人" clearable style="width: 110px" @change="load(1)" />
        <el-input v-model="f.keyword" placeholder="单号 / 事由 / 项目" clearable style="width: 180px" @change="load(1)" />
        <span class="jc-spacer" />
        <el-button v-if="canAllocate" type="primary" :disabled="!selected.length" @click="openAllocate(selected)">批量归集（{{ selected.length }}）</el-button>
      </div>
      <div class="sumline jc-small">
        <span>共 <b class="jc-num">{{ total }}</b> 笔</span>
        <span>合计 <b class="jc-num">{{ money(summary.amount) }}</b></span>
        <span>项目 <b class="jc-num">{{ money(summary.project_amount) }}</b></span>
        <span>公共 <b class="jc-num">{{ money(summary.public_amount) }}</b></span>
        <span :class="{ 'jc-warn': summary.pending_count }">待归集 <b class="jc-num">{{ summary.pending_count || 0 }}</b> 笔 / {{ money(summary.pending_amount) }}</span>
      </div>
      <el-table :data="rows" :row-class-name="rowClass" @selection-change="(v) => (selected = v)" @row-click="openDetail" @row-contextmenu="expenseMenu">
        <el-table-column v-if="canAllocate" type="selection" width="40" :selectable="(r) => r.status === 2 || r.status === 4" />
        <el-table-column label="审批单号" width="170"><template #default="{ row }"><span class="jc-mono">{{ row.approval_no }}</span></template></el-table-column>
        <el-table-column label="申请人" width="90" prop="applicant" />
        <el-table-column label="部门" width="90" prop="dept_name" />
        <el-table-column label="费用日期" width="105"><template #default="{ row }">{{ row.expense_date }}</template></el-table-column>
        <el-table-column label="类别" width="90" prop="category" />
        <el-table-column label="金额" width="120" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.amount) }}</span></template></el-table-column>
        <el-table-column label="归属" min-width="230">
          <template #default="{ row }">
            <template v-if="row.status === 2">
              <StatusPill text="待归集" tone="warn" dot />
              <span class="jc-muted jc-small raw">审批填写：{{ row.raw_project || '（空）' }}</span>
            </template>
            <template v-else-if="row.project_code"><span class="jc-mono">{{ row.project_code }}</span> <span class="jc-muted">{{ row.project_name }}</span></template>
            <StatusPill v-else-if="row.status === 4" text="公共费用" tone="info" />
            <span v-else class="jc-muted">{{ row.status_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="付款" width="70"><template #default="{ row }"><StatusPill :text="row.pay_status ? '已付' : '未付'" :tone="row.pay_status ? 'up' : ''" /></template></el-table-column>
        <el-table-column label="事由" min-width="120" prop="reason" show-overflow-tooltip />
        <el-table-column v-if="canAllocate" width="80" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 2 || row.status === 4" link type="primary" @click.stop="openAllocate([row])">{{ row.status === 2 ? '归集' : '改归集' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </Panel>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" layout="total, sizes, prev, pager, next" :page-sizes="[20, 50, 100]" :total="total" @current-change="load" @size-change="load(1)" />

    <!-- 批量归集 -->
    <el-drawer v-model="alloc.open" title="补充归集" size="460px">
      <div class="jc-stack">
        <div class="jc-muted jc-small">已选 {{ alloc.rows.length }} 张报销单，合计 <b class="jc-num">{{ money(alloc.rows.reduce((s, r) => s + Number(r.amount), 0)) }}</b></div>
        <div class="alloc-list">
          <div v-for="r in alloc.rows" :key="r.id" class="alloc-item">
            <span class="jc-mono">{{ r.approval_no }}</span><span>{{ r.applicant }} · {{ r.category }}</span>
            <span class="jc-num">{{ money(r.amount) }}</span>
            <span class="jc-muted jc-small full">审批填写：{{ r.raw_project || '（空）' }}</span>
          </div>
        </div>
        <el-radio-group v-model="alloc.mode">
          <el-radio value="project">归集到项目</el-radio>
          <el-radio value="public">公共费用</el-radio>
        </el-radio-group>
        <el-select v-if="alloc.mode === 'project'" v-model="alloc.project" filterable placeholder="选择项目（按编码/名称搜索）">
          <el-option v-for="p in opts.projects" :key="p.code" :label="`${p.code} ${p.name}`" :value="p.code" />
        </el-select>
        <el-input v-model="alloc.remark" type="textarea" :rows="2" placeholder="归集说明（留痕，可选）" maxlength="100" />
        <div class="jc-muted jc-small">归集后：状态改为「已归集」，记录归集人与时间；已付款报销同步更新日记账的项目口径。</div>
      </div>
      <template #footer>
        <el-button @click="alloc.open = false">取消</el-button>
        <el-button type="primary" :loading="alloc.saving" :disabled="alloc.mode === 'project' && !alloc.project" @click="doAllocate">确认归集</el-button>
      </template>
    </el-drawer>

    <!-- 详情 / 溯源 -->
    <RecordDetailDialog v-model="detail.open" dialog-title="报销单详情" :heading="detail.data?.approval_no"
      :target="{ kind: 'expense', id: detail.data?.id }" progress-hint="说明报销资料核对、费用归集或付款跟进情况。">
      <div v-if="detail.data" v-loading="detail.loading" class="jc-stack">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="审批单号"><span class="jc-mono">{{ detail.data.approval_no }}</span></el-descriptions-item>
          <el-descriptions-item label="金额"><b class="jc-num">{{ money(detail.data.amount) }}</b></el-descriptions-item>
          <el-descriptions-item label="申请人">{{ detail.data.applicant }}（{{ detail.data.dept_name }}）</el-descriptions-item>
          <el-descriptions-item label="费用日期">{{ detail.data.expense_date }}</el-descriptions-item>
          <el-descriptions-item label="类别">{{ detail.data.category }}</el-descriptions-item>
          <el-descriptions-item label="审批通过">{{ datetime(detail.data.approved_at) }}</el-descriptions-item>
          <el-descriptions-item label="归属" :span="2">
            <span v-if="detail.data.project_code">{{ detail.data.project_code }} {{ detail.data.project_name }}</span>
            <span v-else>{{ detail.data.allocation }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="事由" :span="2">{{ detail.data.reason || '—' }}</el-descriptions-item>
        </el-descriptions>

        <div>
          <div class="sec">企微审批溯源</div>
          <div class="jc-card src">
            <div class="jc-row between jc-small">
              <span>{{ detail.data.approval.sp_name || '费用报销' }} · <span class="jc-mono">{{ detail.data.approval.sp_no }}</span></span>
              <StatusPill :text="detail.data.approval.exists ? '审批已同步' : '未找到同步记录'" :tone="detail.data.approval.exists ? 'up' : 'warn'" />
            </div>
            <div class="jc-muted jc-small mt-8">提交 {{ datetime(detail.data.approval.apply_time) }} · 登记 {{ datetime(detail.data.approval.dispatched_at) }} · 来源 {{ VIA[detail.data.approval.received_via] || detail.data.approval.received_via || '—' }}</div>
            <table v-if="detail.data.approval.fields" class="kv mt-8">
              <tr v-for="(v, k) in detail.data.approval.fields" :key="k"><th>{{ k }}</th><td :class="{ hl: k === '所属项目' }">{{ v || '（空）' }}</td></tr>
            </table>
          </div>
        </div>

        <div>
          <div class="sec">明细</div>
          <el-table :data="detail.data.items" size="small">
            <el-table-column label="项目" prop="item_name" />
            <el-table-column label="金额" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
            <el-table-column label="发票张数" prop="invoice_count" width="90" />
          </el-table>
        </div>

        <div>
          <div class="sec">付款流水</div>
          <div v-if="detail.data.bank_flow" class="jc-card src jc-small">
            <div>{{ detail.data.bank_flow.trade_date }} · {{ detail.data.bank_flow.bank_account }}</div>
            <div class="mt-8">{{ detail.data.bank_flow.counterparty }} · {{ detail.data.bank_flow.summary }} · <b class="jc-num">{{ money(detail.data.bank_flow.amount) }}</b></div>
            <div v-if="detail.data.journal" class="jc-muted mt-8">已登记日记账 #{{ detail.data.journal.id }}（{{ detail.data.journal.journal_date }}）</div>
          </div>
          <div v-else class="jc-muted jc-small">尚未关联付款流水（出纳在流水核对工作台确认后自动关联）</div>
        </div>

        <div v-if="detail.data.allocation_log.length">
          <div class="sec">归集留痕</div>
          <el-timeline>
            <el-timeline-item v-for="(l, i) in detail.data.allocation_log" :key="i" :timestamp="l.at" size="small">
              {{ l.by_name || '—' }}：{{ l.from || (l.from_status === 2 ? '待归集' : '公共费用') }} → <b>{{ l.to }}</b>
              <span v-if="l.remark" class="jc-muted">（{{ l.remark }}）</span>
            </el-timeline-item>
          </el-timeline>
        </div>
      </div>
      <template v-if="canAllocate && detail.data && (detail.data.status === 2 || detail.data.status === 4)" #footer>
        <el-button type="primary" @click="openAllocate([detail.data])">{{ detail.data.status === 2 ? '补充归集' : '修改归集' }}</el-button>
      </template>
    </RecordDetailDialog>

    <WecomApprovalDialog v-model="approvalOpen" kind="expense" title="费用报销" @submitted="load(1)" />
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import WecomApprovalDialog from '@/components/WecomApprovalDialog.vue'
import { useAuth } from '@/store/auth'
import { datetime, money, wan, wanNum } from '@/utils/format'
import { PALETTE, baseGrid, wanAxis, wanTooltip } from '@/utils/chart'

const auth = useAuth()
const canAllocate = computed(() => auth.can('expense:allocate'))
const STATUS_OPTS = [{ label: '全部已审批', value: 'approved' }, { label: '待归集', value: 'pending' }, { label: '已归集', value: 'allocated' }, { label: '全部', value: 'all' }]
const VIA = { callback: '企微回调', reconcile: '兜底对账', mock: '模拟' }

const month = ref(dayjs().format('YYYY-MM')), year = ref(dayjs().format('YYYY')), statsMode = ref('month')
const approvalOpen = ref(false)
const stats = ref({})
const statsLoading = ref(false)
const kpi = computed(() => stats.value.kpi || {})
const dimTab = ref('dept')
const opts = ref({ projects: [], depts: [], categories: [] })

const route = useRoute()
const f = reactive({ status: 'approved', project: '', dept: '', category: '', range: null, applicant: '', keyword: '' })
const rows = ref([]); const total = ref(0); const page = ref(1); const pageSize = ref(20); const loading = ref(false)
const summary = ref({}); const selected = ref([])

function params() {
  return { status: f.status, project: f.project || '', dept: f.dept || '', category: f.category || '', applicant: f.applicant, keyword: f.keyword,
    start: f.range?.[0], end: f.range?.[1] }
}
async function load(p = page.value) {
  page.value = p; loading.value = true
  try {
    const r = await api.get('/expenses', { ...params(), page: p, page_size: pageSize.value })
    rows.value = r.items; total.value = r.total; summary.value = r.summary
  } finally { loading.value = false }
}
function statsRange() {
  if (statsMode.value === 'year') return [`${year.value}-01-01`, `${year.value}-12-31`]
  return [`${month.value}-01`, dayjs(`${month.value}-01`).endOf('month').format('YYYY-MM-DD')]
}
async function loadStats() {
  statsLoading.value = true
  const [start, end] = statsRange()
  try { stats.value = await api.get('/expenses/stats', { start, end }) } finally { statsLoading.value = false }
}
function changeStatsPeriod() {
  f.range = statsRange()
  load(1)
  loadStats()
}
function quick(status) { f.status = status; load(1) }
function rowClass({ row }) { return row.status === 2 ? 'is-alert' : '' }
function onProjectClick(p) {
  const item = stats.value.by_project?.[p.dataIndex]
  if (!item) return
  f.project = item.key === '公共费用' ? '__public__' : item.key === '待归集' ? '' : item.key
  if (item.key === '待归集') f.status = 'pending'
  const [s, e] = [stats.value.start, stats.value.end]
  f.range = [s, e]; load(1)
}

const projectOption = computed(() => {
  const list = (stats.value.by_project || []).slice(0, 14).reverse()
  return {
    grid: baseGrid({ left: 4, top: 8 }),
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: wanTooltip },
    xAxis: { type: 'value', ...wanAxis },
    yAxis: { type: 'category', data: list.map((x) => (x.key === x.name ? x.name : `${x.name}`)), axisLabel: { width: 150, overflow: 'truncate' } },
    series: [{ type: 'bar', barMaxWidth: 14, data: list.map((x) => ({ value: Number(x.amount),
      itemStyle: { color: x.key === '公共费用' ? PALETTE[4] : x.key === '待归集' ? PALETTE[1] : PALETTE[0], borderRadius: [0, 3, 3, 0] } })) }],
  }
})
const dimOption = computed(() => {
  const list = (dimTab.value === 'dept' ? stats.value.by_dept : stats.value.by_category) || []
  return {
    tooltip: { trigger: 'item', valueFormatter: wanTooltip },
    legend: { orient: 'vertical', right: 8, top: 'middle' },
    series: [{ type: 'pie', radius: ['48%', '72%'], center: ['36%', '50%'], label: { show: false },
      data: list.map((x) => ({ name: x.name, value: Number(x.amount) })) }],
  }
})

// 归集
const alloc = reactive({ open: false, rows: [], mode: 'project', project: '', remark: '', saving: false })
function openAllocate(list) {
  alloc.rows = list; alloc.mode = 'project'; alloc.project = list.length === 1 ? (list[0].project_code || '') : ''; alloc.remark = ''; alloc.open = true
}
async function doAllocate() {
  alloc.saving = true
  try {
    const r = await api.post('/expenses/allocate', { ids: alloc.rows.map((x) => x.id), project_code: alloc.mode === 'public' ? null : alloc.project, remark: alloc.remark })
    ElMessage.success(`已归集 ${r.count} 张`)
    alloc.open = false; selected.value = []
    if (detail.open && detail.data) openDetail(detail.data)
    load(); loadStats()
  } finally { alloc.saving = false }
}

// 详情
const detail = reactive({ open: false, data: null, loading: false })
async function openDetail(row) {
  detail.open = true; detail.loading = true
  try { detail.data = await api.get(`/expenses/${row.id}`) } finally { detail.loading = false }
}

function exportXlsx() { api.download('/expenses/export', params()) }

onMounted(async () => {
  const rq = route.query
  if (rq.status) f.status = String(rq.status)
  if (rq.project) f.project = String(rq.project)
  opts.value = await api.get('/expenses/options')
  load(1); loadStats()
})

const expenseMenu = rowDeletion(row => canAllocate.value && recordRemoval('expense', row.id, row.approval_no, async () => { await load(); await loadStats() }, { title: '删除报销单' }))
</script>

<style scoped>
.filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 18px; border-bottom: 1px solid var(--jc-line-2); align-items: center; }
.sumline { display: flex; gap: 20px; padding: 8px 18px; color: var(--jc-ink-3); border-bottom: 1px solid var(--jc-line-2); flex-wrap: wrap; }
.sumline b { color: var(--jc-ink); font-weight: 600; }
.raw { margin-left: 6px; }
.clickable { cursor: pointer; }
.alloc-list { max-height: 240px; overflow: auto; border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); }
.alloc-item { display: grid; grid-template-columns: 1fr 1fr auto; gap: 4px 10px; padding: 8px 12px; border-bottom: 1px solid var(--jc-line-2); font-size: 13px; }
.alloc-item:last-child { border-bottom: none; }
.alloc-item .full { grid-column: 1 / -1; }
.sec { font-size: 13px; font-weight: 600; color: var(--jc-ink-2); margin-bottom: 8px; }
.src { padding: 12px 14px; box-shadow: none; background: var(--jc-surface-2); }
.kv { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.kv th { text-align: left; color: var(--jc-ink-3); font-weight: 400; width: 90px; padding: 4px 0; vertical-align: top; }
.kv td { padding: 4px 0; color: var(--jc-ink); }
.kv td.hl { color: var(--jc-brand); font-weight: 500; }
</style>
