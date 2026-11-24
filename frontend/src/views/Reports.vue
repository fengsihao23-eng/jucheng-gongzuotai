<!-- 报表中心（FR-04）：报表类型目录 + 报表列表（同期间只显示最新版）+ 手动生成 + 一键更新 + 详情抽屉 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="财务 · 总办" title="报表中心"
      desc="每日支出、银行流水、项目利润、公司周报/月报与在建运营项目报表按计划自动生成并归档；口径统一取自台账与共享利润口径，可随时手动重算。">
      <template v-if="canGen">
        <el-button @click="openGen()">手动生成</el-button>
        <el-button type="primary" :loading="regen" @click="regenerateAll">一键更新</el-button>
      </template>
    </PageHeader>

    <div class="layout">
      <!-- 左：报表类型目录 -->
      <aside class="jc-stack cat">
        <div class="type jc-card" :class="{ on: !filter.report_type }" @click="pickType('')">
          <div class="jc-row between"><b>全部报表</b><span class="jc-muted jc-small">{{ list.total }} 期</span></div>
        </div>
        <div v-for="t in types" :key="t.report_type" class="type jc-card" :class="{ on: filter.report_type === t.report_type }"
          @click="pickType(t.report_type)">
          <div class="jc-row between">
            <b>{{ t.name }}</b>
            <el-tooltip placement="right" :show-after="200" popper-class="jc-caliber-tip">
              <template #content>
                <div class="tip-t">{{ t.name }} · 口径说明</div>
                <ol class="tip-l"><li v-for="(c, i) in t.caliber" :key="i">{{ c }}</li></ol>
              </template>
              <span class="cal" @click.stop>口径</span>
            </el-tooltip>
          </div>
          <div class="meta jc-small">
            <span>{{ t.schedule }}</span>
            <span class="jc-muted">{{ t.formats.join(' + ').toUpperCase() }}</span>
          </div>
          <div class="jc-small jc-muted last">
            {{ t.latest ? `最新 ${t.latest.period} · v${t.latest.version} · ${fromNow(t.latest.created_at)}` : '尚未生成' }}
          </div>
        </div>
      </aside>

      <!-- 右：报表列表 -->
      <Panel :title="curType ? curType.name : '全部报表'" :sub="curType ? `${curType.prd} · ${curType.schedule}` : '同一期间只显示最新版本，可展开历史版本'"
        :padding="false" :loading="loading">
        <template #extra>
          <el-input v-model="filter.period" placeholder="期间，如 2026-10 / 2026-W42" clearable size="small" style="width: 210px"
            @change="load(1)" />
          <el-button v-if="canGen && curType" size="small" @click="openGen(curType.report_type)">生成{{ curType.name }}</el-button>
        </template>
        <el-table :data="list.items" row-key="id" @row-click="openDetail" class="rows" @expand-change="loadVersions" @row-contextmenu="reportMenu">
          <el-table-column type="expand" width="36">
            <template #default="{ row }">
              <div class="hist">
                <div class="jc-small jc-muted">历史版本（{{ row.versions }}）</div>
                <el-table :data="versions[row.id] || []" size="small" v-loading="!versions[row.id]" @row-contextmenu="reportMenu">
                  <el-table-column label="版本" width="70"><template #default="{ row: h }">v{{ h.version }}</template></el-table-column>
                  <el-table-column label="生成时间" width="150"><template #default="{ row: h }">{{ datetime(h.created_at) }}</template></el-table-column>
                  <el-table-column label="方式" width="90"><template #default="{ row: h }"><StatusPill :text="TRIGGER[h.trigger] || h.trigger" :tone="h.trigger === 'schedule' ? 'info' : 'brand'" /></template></el-table-column>
                  <el-table-column label="操作人" width="90"><template #default="{ row: h }">{{ h.created_by_name || '系统' }}</template></el-table-column>
                  <el-table-column label="关键指标" min-width="280"><template #default="{ row: h }"><span class="jc-small">{{ summaryText(h.summary, 3) }}</span></template></el-table-column>
                  <el-table-column label="文件" min-width="200">
                    <template #default="{ row: h }"><div class="files"><FileLink v-for="f in h.files" :key="f.id" :file="f" @click.stop /></div></template>
                  </el-table-column>
                </el-table>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="报表" min-width="210">
            <template #default="{ row }">
              <div class="t1">{{ row.type_name }}</div>
              <div class="jc-small jc-muted">{{ row.period_label }}</div>
            </template>
          </el-table-column>
          <el-table-column label="版本" width="74">
            <template #default="{ row }"><span class="jc-num">v{{ row.version }}</span><span v-if="row.versions > 1" class="jc-muted jc-small"> /{{ row.versions }}</span></template>
          </el-table-column>
          <el-table-column label="生成方式" width="96">
            <template #default="{ row }"><StatusPill :text="TRIGGER[row.trigger] || row.trigger" :tone="row.trigger === 'schedule' ? 'info' : 'brand'" /></template>
          </el-table-column>
          <el-table-column label="生成时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
          <el-table-column label="耗时" width="80" align="right"><template #default="{ row }"><span class="jc-num jc-muted">{{ row.cost_ms }} ms</span></template></el-table-column>
          <el-table-column label="关键指标" min-width="320">
            <template #default="{ row }">
              <div class="sm">
                <span v-for="s in row.summary.slice(0, 4)" :key="s.label"><i>{{ s.label }}</i><b class="jc-num">{{ fmtVal(s) }}</b></span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="文件下载" min-width="220">
            <template #default="{ row }"><div class="files" @click.stop><FileLink v-for="f in row.files" :key="f.id" :file="f" /></div></template>
          </el-table-column>
        </el-table>
        <div class="pg">
          <el-pagination v-model:current-page="filter.page" layout="total, prev, pager, next" :total="list.total"
            :page-size="filter.page_size" @current-change="load" />
        </div>
      </Panel>
    </div>

    <!-- 手动生成 -->
    <el-dialog v-model="gen.show" title="手动生成报表" width="520px">
      <el-form label-width="84px">
        <el-form-item label="报表类型">
          <el-select v-model="gen.report_type" style="width: 100%" @change="gen.period = defaultPeriod(gen.report_type)">
            <el-option v-for="t in types" :key="t.report_type" :label="t.name" :value="t.report_type" />
          </el-select>
        </el-form-item>
        <el-form-item :label="kindLabel">
          <el-date-picker v-if="genKind === 'day'" v-model="gen.period" type="date" value-format="YYYY-MM-DD"
            :disabled-date="future" style="width: 100%" />
          <el-date-picker v-else-if="genKind === 'week'" v-model="gen.period" type="week" format="gggg 第 ww 周"
            value-format="YYYY-MM-DD" :disabled-date="future" style="width: 100%" />
          <el-date-picker v-else v-model="gen.period" type="month" value-format="YYYY-MM" :disabled-date="future" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="genType?.supports_project" label="指定项目">
          <el-select v-model="gen.project_codes" multiple filterable clearable placeholder="不选则全部项目" style="width: 100%">
            <el-option v-for="p in projects" :key="p.project_code" :label="`${p.project_code} ${p.name}`" :value="p.project_code" />
          </el-select>
        </el-form-item>
        <div v-if="genType" class="jc-small jc-muted gen-cal">{{ genType.caliber[0] }}</div>
      </el-form>
      <template #footer>
        <el-button @click="gen.show = false">取消</el-button>
        <el-button type="primary" :loading="gen.loading" :disabled="!gen.report_type || !gen.period" @click="doGenerate">生成</el-button>
      </template>
    </el-dialog>

    <!-- 一键更新结果 -->
    <el-dialog v-model="regenResult.show" title="一键更新完成" width="720px">
      <div class="jc-row regen-sum">
        <span>共 <b class="jc-num">{{ regenResult.data?.total }}</b> 份</span>
        <span class="jc-up">成功 <b class="jc-num">{{ regenResult.data?.success }}</b></span>
        <span :class="regenResult.data?.failed ? 'jc-down' : 'jc-muted'">失败 <b class="jc-num">{{ regenResult.data?.failed }}</b></span>
        <span class="jc-muted">耗时 {{ ((regenResult.data?.cost_ms || 0) / 1000).toFixed(1) }} s</span>
      </div>
      <el-table :data="regenResult.data?.results || []" size="small" max-height="420">
        <el-table-column label="报表" width="150" prop="type_name" />
        <el-table-column label="期间" width="110" prop="period" />
        <el-table-column label="版本" width="64"><template #default="{ row }">{{ row.ok ? 'v' + row.version : '—' }}</template></el-table-column>
        <el-table-column label="结果" min-width="300">
          <template #default="{ row }">
            <span v-if="row.ok" class="jc-small">{{ summaryText(row.summary, 3) }}</span>
            <span v-else class="jc-down jc-small">{{ row.error }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawer.show" size="760px" :title="drawer.data?.title || '报表详情'">
      <div v-if="drawer.data" v-loading="drawer.loading" class="jc-stack">
        <div class="jc-row wrap jc-small jc-muted">
          <span>期间 {{ drawer.data.period_label }}</span><span>· v{{ drawer.data.version }}</span>
          <span>· {{ TRIGGER[drawer.data.trigger] }}</span><span>· {{ datetime(drawer.data.created_at) }}</span>
          <span>· 耗时 {{ drawer.data.cost_ms }} ms</span>
        </div>
        <div class="jc-grid c3">
          <Kpi v-for="s in drawer.data.summary" :key="s.label" :label="s.label" :value="kpiVal(s)" :unit="kpiUnit(s)" />
        </div>
        <div class="files box"><span class="jc-muted jc-small">文件</span><FileLink v-for="f in drawer.data.files" :key="f.id" :file="f" /></div>
        <div v-if="drawer.data.metrics?.missing?.length" class="jc-quote jc-small">
          数据缺失：{{ drawer.data.metrics.missing.join('、') }}（报表中已注明，未以其他数据替代）
        </div>
        <Panel v-for="sec in sections" :key="sec.title" :title="sec.title" :sub="sec.sub" :padding="false">
          <el-table :data="sec.rows" size="small" max-height="320" :row-class-name="sec.alert ? ({ row }) => (sec.alert(row) ? 'is-alert' : '') : undefined">
            <el-table-column v-for="c in sec.cols" :key="c.key" :label="c.label" :min-width="c.w || 100"
              :align="['money', 'pct', 'int'].includes(c.kind) ? 'right' : 'left'">
              <template #default="{ row }"><span :class="{ 'jc-num': c.kind !== 'text' }">{{ fmt(row[c.key], c.kind) }}</span></template>
            </el-table-column>
          </el-table>
        </Panel>
        <Panel title="口径说明">
          <ol class="cal-list"><li v-for="(c, i) in drawer.data.caliber" :key="i">{{ c }}</li></ol>
        </Panel>
        <Panel title="历史版本" :padding="false">
          <el-table :data="drawer.data.history" size="small" @row-contextmenu="reportMenu">
            <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
            <el-table-column label="生成时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
            <el-table-column label="方式" width="80"><template #default="{ row }">{{ TRIGGER[row.trigger] }}</template></el-table-column>
            <el-table-column label="关键指标" min-width="220"><template #default="{ row }"><span class="jc-small">{{ summaryText(row.summary, 2) }}</span></template></el-table-column>
            <el-table-column label="文件" min-width="180"><template #default="{ row }"><div class="files"><FileLink v-for="f in row.files" :key="f.id" :file="f" /></div></template></el-table-column>
          </el-table>
        </Panel>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { money, percent, thousands, datetime, fromNow, wan } from '@/utils/format'

const TRIGGER = { schedule: '定时', manual: '手动', mcp: 'WorkBuddy' }
const auth = useAuth()
const route = useRoute()
const types = ref([])
const canGen = ref(false)
const loading = ref(false)
const regen = ref(false)
const list = ref({ items: [], total: 0 })
const versions = reactive({})
const projects = ref([])
const filter = reactive({ report_type: '', period: '', page: 1, page_size: 20 })
const gen = reactive({ show: false, loading: false, report_type: '', period: '', project_codes: [] })
const regenResult = reactive({ show: false, data: null })
const drawer = reactive({ show: false, loading: false, data: null })

const curType = computed(() => types.value.find((t) => t.report_type === filter.report_type))
const genType = computed(() => types.value.find((t) => t.report_type === gen.report_type))
const genKind = computed(() => genType.value?.period_kind || 'day')
const kindLabel = computed(() => ({ day: '统计日', week: '统计周', month: '统计月' })[genKind.value])
const future = (d) => d.getTime() > Date.now()

// ---------- 格式化
function fmt(v, kind) {
  if (v === null || v === undefined || v === '') return '—'
  if (kind === 'money') return Number.isNaN(Number(v)) ? v : money(v)
  if (kind === 'pct') return Number.isNaN(Number(v)) ? v : percent(v)
  if (kind === 'int') return thousands(v)
  return v
}
const fmtVal = (s) => (s.kind === 'money' && !Number.isNaN(Number(s.value)) ? wan(s.value) : fmt(s.value, s.kind))
const summaryText = (arr, n = 3) => (arr || []).slice(0, n).map((s) => `${s.label} ${fmtVal(s)}`).join(' · ')
function kpiVal(s) {
  if (s.kind === 'money' && !Number.isNaN(Number(s.value))) return (Number(s.value) / 1e4).toFixed(Math.abs(Number(s.value)) >= 1e5 ? 1 : 2)
  if (s.kind === 'pct') return s.value === null ? '—' : (Number(s.value) * 100).toFixed(1)
  return s.value ?? '—'
}
const kpiUnit = (s) => (s.kind === 'money' && !Number.isNaN(Number(s.value)) ? '万元' : s.kind === 'pct' && s.value !== null ? '%' : '')

// ---------- 详情分区（按报表类型取 metrics 的关键表）
const C = (key, label, kind = 'text', w) => ({ key, label, kind, w })
const sections = computed(() => {
  const d = drawer.data
  if (!d?.metrics) return []
  const m = d.metrics
  switch (d.report_type) {
    case 'daily_expense':
      return [
        { title: '按部门', rows: m.by_dept, cols: [C('name', '部门', 'text', 140), C('count', '笔数', 'int'), C('expense_amount', '报销', 'money'), C('flow_amount', '流水', 'money'), C('total', '合计', 'money')] },
        { title: '按项目', rows: m.by_project, alert: (r) => r.key === '待归集', cols: [C('name', '项目/归集', 'text', 200), C('count', '笔数', 'int'), C('total', '合计', 'money')] },
        { title: '支出明细', sub: `共 ${m.items.length} 笔`, rows: m.items, cols: [C('source', '来源', 'text', 60), C('ref', '单号', 'text', 140), C('who', '申请人/对方', 'text', 110), C('project_name', '项目/归集', 'text', 160), C('category', '类别'), C('amount', '金额', 'money')] },
      ]
    case 'daily_bankflow':
      return [
        { title: '账户汇总', rows: m.accounts, alert: (r) => r.check !== '一致', cols: [C('account', '账户', 'text', 200), C('opening', '期初', 'money', 120), C('income', '收入', 'money', 120), C('outflow', '支出', 'money', 120), C('closing', '期末', 'money', 120), C('check', '勾稽')] },
        { title: '未核对清单', sub: `待核对池累计 ${m.pool_count} 笔 / ${money(m.pool_amount)}`, rows: m.unmatched, cols: [C('account', '账户', 'text', 180), C('direction', '收支', 'text', 60), C('amount', '金额', 'money'), C('counterparty', '对方', 'text', 160), C('summary', '摘要', 'text', 160)] },
      ]
    case 'weekly_profit':
      return [
        { title: '项目利润', sub: `毛利率低于 ${percent(m.alert_margin)} 标红`, rows: m.rows, alert: (r) => r.low_margin, cols: [C('name', '项目', 'text', 190), C('income', '累计收入', 'money', 120), C('cost', '累计成本', 'money', 120), C('gross_profit', '毛利', 'money', 120), C('gross_margin', '毛利率', 'pct', 80), C('period_cost', '本周成本', 'money', 110)] },
        { title: '公共费用（不摊入项目）', rows: m.public_detail, cols: [C('category', '类别'), C('count', '笔数', 'int'), C('amount', '金额', 'money')] },
      ]
    case 'company_weekly':
      return [
        { title: '签约', rows: m.sign.items, cols: [C('contract_no', '合同编号', 'text', 120), C('customer_name', '客户', 'text', 180), C('sign_date', '签订日'), C('amount', '金额', 'money')] },
        { title: '回款', sub: `当月达成 ${percent(m.payment.month_rate)}`, rows: m.payment.items, cols: [C('payment_date', '到账日'), C('customer_name', '客户', 'text', 180), C('line_name', '业务线'), C('amount', '金额', 'money')] },
        { title: '重点项目进展', rows: m.key_projects, alert: (r) => !!r.delay_nodes, cols: [C('name', '项目', 'text', 180), C('current_node', '当前节点'), C('feedback', '进展', 'text', 260)] },
        { title: '周报提交率', rows: m.submission.rows, cols: [C('dept', '部门'), C('must', '应交', 'int'), C('submitted', '已交', 'int'), C('rate', '提交率', 'pct'), C('missing', '未交', 'text', 200)] },
      ]
    case 'company_monthly':
      return [
        { title: '环比', sub: `对比 ${m.prev_period}`, rows: m.mom.map((r) => (r.metric.includes('率') ? { ...r, current: percent(r.current), previous: percent(r.previous) } : r)), cols: [C('metric', '指标', 'text', 200), C('current', '本月', 'money', 130), C('previous', '上月', 'money', 130), C('mom', '环比', 'pct')] },
        { title: '柠檬云凭证汇总', sub: m.lemon.available ? `凭证 ${m.lemon.voucher_count} 张` : '数据缺失', rows: m.lemon.by_class, cols: [C('class', '科目大类'), C('debit', '借方', 'money', 130), C('credit', '贷方', 'money', 130)] },
        { title: '税务申报', sub: m.tax.available ? '' : '数据缺失', rows: m.tax.rows, cols: [C('tax_type', '税种'), C('payable_amount', '应纳税额', 'money'), C('paid_amount', '已缴', 'money'), C('filing_date', '申报日')] },
        { title: '月报提交率', rows: m.submission.rows, cols: [C('dept', '部门'), C('must', '应交', 'int'), C('submitted', '已交', 'int'), C('rate', '提交率', 'pct')] },
      ]
    case 'project_operation':
      return [
        { title: '在建 / 运营项目', rows: m.rows, alert: (r) => r.delay_count > 0, cols: [C('name', '项目', 'text', 180), C('group', '类别', 'text', 60), C('contract_amount', '合同额', 'money', 120), C('received', '累计回款', 'money', 120), C('receive_rate', '回款率', 'pct', 80), C('gross_margin', '毛利率', 'pct', 80), C('current_node', '当前节点')] },
        { title: '延期节点', rows: m.delays, cols: [C('project_name', '项目', 'text', 180), C('node', '节点'), C('plan_date', '计划日'), C('delay_days', '延期天数', 'int'), C('owner', '负责人')] },
      ]
    default:
      return []
  }
})

// ---------- 数据
async function loadTypes() {
  const r = await api.get('/reports/types')
  types.value = r.types
  canGen.value = r.can_generate
}
async function load(page) {
  if (typeof page === 'number') filter.page = page
  loading.value = true
  try {
    list.value = await api.get('/reports', { ...filter })
  } finally {
    loading.value = false
  }
}
function pickType(t) {
  filter.report_type = t
  load(1)
}
async function loadVersions(row, expanded) {
  if (!expanded.some((r) => r.id === row.id)) return
  versions[row.id] = await api.get(`/reports/${row.id}/versions`)
}
async function openDetail(row) {
  drawer.show = true
  drawer.loading = true
  drawer.data = { ...row, summary: row.summary || [] }
  try {
    drawer.data = await api.get(`/reports/${row.id}`)
  } finally {
    drawer.loading = false
  }
}

function defaultPeriod(t) {
  const kind = types.value.find((x) => x.report_type === t)?.period_kind
  if (kind === 'day') return dayjs().subtract(1, 'day').format('YYYY-MM-DD')
  if (kind === 'week') return dayjs().subtract(7, 'day').format('YYYY-MM-DD')
  return dayjs().subtract(1, 'month').format('YYYY-MM')
}
async function openGen(t) {
  gen.report_type = t || filter.report_type || types.value[0]?.report_type || ''
  gen.period = defaultPeriod(gen.report_type)
  gen.project_codes = []
  gen.show = true
  if (!projects.value.length && (auth.hasMenu('profit') || auth.hasMenu('board'))) {
    try {
      const r = await api.get('/reports/profit', null, { silent: true })
      projects.value = r.rows.map((x) => ({ project_code: x.project_code, name: x.name }))
    } catch { /* 无利润权限时不提供项目选择 */ }
  }
}
async function doGenerate() {
  gen.loading = true
  try {
    // 周：传所选周内任一日期，后端换算为 ISO 周号
    const body = { report_type: gen.report_type, period: gen.period }
    if (gen.project_codes.length) body.project_codes = gen.project_codes
    const r = await api.post('/reports/generate', body)
    ElMessage.success(`已生成 ${r.title} v${r.version}（${r.cost_ms} ms）`)
    gen.show = false
    await Promise.all([load(), loadTypes()])
    openDetail(r)
  } finally {
    gen.loading = false
  }
}
async function regenerateAll() {
  regen.value = true
  try {
    regenResult.data = await api.post('/reports/regenerate-all', null, { timeout: 120000 })
    regenResult.show = true
    await Promise.all([load(), loadTypes()])
  } finally {
    regen.value = false
  }
}

watch(() => route.query.id, (id) => id && openDetail({ id: Number(id) }))
onMounted(async () => {
  await loadTypes()
  if (route.query.type) filter.report_type = String(route.query.type)
  await load(1)
  if (route.query.id) openDetail({ id: Number(route.query.id) })
})

const reportMenu = rowDeletion(row => auth.canDelete('report:generate') && recordRemoval('report', row.id, row.title || '报表 V' + row.version, () => {
  drawer.show = false
  for (const id of Object.keys(versions)) delete versions[id]
  return load()
}, { title: '删除报表' }))
</script>

<style scoped>
.layout { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 16px; align-items: start; }
.cat { gap: 10px; }
.type { padding: 12px 14px; cursor: pointer; transition: border-color 0.15s; }
.type:hover { border-color: var(--jc-brand-100); }
.type.on { border-color: var(--jc-brand); box-shadow: inset 3px 0 0 var(--jc-brand); }
.type b { font-size: 14px; font-weight: 600; color: var(--jc-ink); }
.type .meta { display: flex; justify-content: space-between; margin-top: 6px; color: var(--jc-ink-2); }
.type .last { margin-top: 4px; }
.cal { font-size: 11px; color: var(--jc-brand); border: 1px solid var(--jc-brand-100); border-radius: 4px; padding: 0 5px; cursor: help; }
.rows :deep(.el-table__row) { cursor: pointer; }
.t1 { font-weight: 600; color: var(--jc-ink); }
.sm { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; }
.sm i { font-style: normal; color: var(--jc-ink-3); margin-right: 4px; }
.sm b { font-weight: 600; color: var(--jc-ink); }
.files { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.files.box { flex-direction: row; flex-wrap: wrap; align-items: center; gap: 14px; padding: 10px 14px; border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); background: var(--jc-surface-2); }
.hist { padding: 6px 16px 12px 48px; }
.pg { padding: 12px 16px; display: flex; justify-content: flex-end; }
.gen-cal { line-height: 1.6; padding-left: 84px; }
.regen-sum { gap: 18px; margin-bottom: 12px; font-size: 13px; }
.cal-list { margin: 0; padding-left: 18px; color: var(--jc-ink-2); font-size: 12.5px; line-height: 1.75; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
</style>

<style>
.jc-caliber-tip { max-width: 440px; }
.jc-caliber-tip .tip-t { font-weight: 600; margin-bottom: 6px; }
.jc-caliber-tip .tip-l { margin: 0; padding-left: 16px; line-height: 1.6; font-size: 12px; }
</style>
