<template>
  <div class="jc-page">
    <PageHeader eyebrow="财务运营" title="财务数据" desc="柠檬云凭证/科目余额双通道接入（开放 API + 导出文件），同步后自动对账；税务申报表归集归档；项目采购成本人工登记。">
      <el-date-picker v-model="period" type="month" value-format="YYYY-MM" :clearable="false" style="width: 130px" @change="reloadTab" />
    </PageHeader>

    <el-tabs v-model="tab" @tab-change="reloadTab">
      <!-- ============ 柠檬云同步 ============ -->
      <el-tab-pane label="柠檬云同步" name="lemon">
        <div class="jc-grid c3">
          <div class="jc-card op">
            <div class="jc-row between"><b>API 通道</b><StatusPill :text="mode === 'live' ? '开放平台' : 'Mock 模式'" :tone="mode === 'live' ? 'up' : 'info'" /></div>
            <div class="jc-muted jc-small mt-8">每晚 23:00 自动增量同步当月凭证与科目余额并对账。也可手动同步所选期间。</div>
            <el-button v-if="canSync" class="mt-16" type="primary" :loading="syncing" @click="syncApi">同步 {{ period }}</el-button>
          </div>
          <div class="jc-card op">
            <div class="jc-row between"><b>文件通道</b><span class="jc-muted jc-small">柠檬云 → 凭证 → 导出</span></div>
            <UploadBox v-if="canSync" ref="lub" accept=".xlsx" title="上传柠檬云导出凭证 xlsx" tip="按模板列映射解析，行级错误定位" class="mt-8" @change="(f) => (lemonFile = f)" />
            <el-button v-if="canSync" class="mt-8" :disabled="!lemonFile" :loading="uploading" @click="uploadLemon">解析入库并对账</el-button>
          </div>
          <div class="jc-card op">
            <div class="jc-row between"><b>双通道一致性</b><StatusPill v-if="cons" :text="CONS[cons.status]" :tone="cons.status === 'pass' ? 'up' : cons.status === 'diff' ? 'down' : ''" /></div>
            <div v-if="cons && cons.api" class="jc-small mt-8 cons">
              <div><span>API</span><span>{{ cons.api.vouchers }} 张 · 借 {{ money(cons.api.debit) }}</span></div>
              <div><span>文件</span><span>{{ cons.file.vouchers }} 张 · 借 {{ money(cons.file.debit) }}</span></div>
              <div class="jc-muted">比对批次 {{ cons.file_batch }}，差异 {{ cons.diff_count }} 处</div>
            </div>
            <div v-else class="jc-muted jc-small mt-8">{{ cons?.message || '同周期 API 拉取结果与最新文件批次逐张比对' }}</div>
            <el-button class="mt-8" size="small" @click="loadCons">重新校验</el-button>
          </div>
        </div>

        <Panel class="mt-16" title="同步批次" :padding="false" :loading="bLoading">
          <el-table :data="batches" highlight-current-row @row-click="openBatch">
            <el-table-column label="批次号" width="190"><template #default="{ row }"><span class="jc-mono">{{ row.batch_no }}</span></template></el-table-column>
            <el-table-column label="通道" width="80"><template #default="{ row }">{{ row.channel === 'api' ? 'API' : '文件' }}</template></el-table-column>
            <el-table-column label="期间" width="90" prop="period" />
            <el-table-column label="凭证数" width="80" align="right" prop="voucher_count" />
            <el-table-column label="借方合计" width="150" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.debit_total) }}</span></template></el-table-column>
            <el-table-column label="贷方合计" width="150" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.credit_total) }}</span></template></el-table-column>
            <el-table-column label="来源（日记账）" width="170" align="right"><template #default="{ row }"><span class="jc-num jc-muted">{{ row.source_count ?? '—' }} 笔 / {{ money(row.source_total) }}</span></template></el-table-column>
            <el-table-column label="对账" width="110"><template #default="{ row }"><StatusPill :text="REC[row.reconcile_status] + (row.diff_count ? ` ${row.diff_count}` : '') + (row.error_count ? ` ${row.error_count}` : '')" :tone="REC_TONE[row.reconcile_status]" dot /></template></el-table-column>
            <el-table-column label="原件" min-width="160"><template #default="{ row }"><FileLink v-if="row.file" :file="row.file" /><span v-else class="jc-muted">—</span></template></el-table-column>
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
          </el-table>
        </Panel>
      </el-tab-pane>

      <!-- ============ 科目余额表 ============ -->
      <el-tab-pane label="科目余额表" name="balance">
        <Panel :title="`科目余额表 · ${period}`" :padding="false" :loading="balLoading">
          <template #extra>
            <StatusPill v-if="bal.items?.length" :text="bal.balanced ? '期末借贷平衡' : '期末借贷不平'" :tone="bal.balanced ? 'up' : 'down'" />
          </template>
          <el-table :data="bal.items || []" show-summary :summary-method="balSummary">
            <el-table-column label="科目编码" width="100" prop="subject_code" />
            <el-table-column label="科目名称" min-width="140" prop="subject_name" />
            <el-table-column label="期初余额" align="center">
              <el-table-column label="借方" width="140" align="right"><template #default="{ row }">{{ nz(row.opening_debit) }}</template></el-table-column>
              <el-table-column label="贷方" width="140" align="right"><template #default="{ row }">{{ nz(row.opening_credit) }}</template></el-table-column>
            </el-table-column>
            <el-table-column label="本期发生额" align="center">
              <el-table-column label="借方" width="140" align="right"><template #default="{ row }">{{ nz(row.debit_amount) }}</template></el-table-column>
              <el-table-column label="贷方" width="140" align="right"><template #default="{ row }">{{ nz(row.credit_amount) }}</template></el-table-column>
            </el-table-column>
            <el-table-column label="期末余额" align="center">
              <el-table-column label="借方" width="140" align="right"><template #default="{ row }">{{ nz(row.debit_balance) }}</template></el-table-column>
              <el-table-column label="贷方" width="140" align="right"><template #default="{ row }">{{ nz(row.credit_balance) }}</template></el-table-column>
            </el-table-column>
          </el-table>
          <div v-if="!bal.items?.length" class="empty jc-muted">该期间尚未同步科目余额，可在「柠檬云同步」中手动同步</div>
        </Panel>
        <Panel class="mt-16" title="项目辅助核算（供数）" :sub="`${period} · 凭证辅助核算汇总，供项目利润表/公司月报取数`" :padding="false">
          <el-table :data="supply.projects || []" size="small">
            <el-table-column label="项目编码" width="160" prop="project_code" />
            <el-table-column label="项目" min-width="200"><template #default="{ row }">{{ projName(row.project_code) }}</template></el-table-column>
            <el-table-column label="回款（应收贷方）" width="170" align="right"><template #default="{ row }">{{ money(row.income) }}</template></el-table-column>
            <el-table-column label="成本（合同履约成本）" width="180" align="right"><template #default="{ row }">{{ money(row.cost) }}</template></el-table-column>
          </el-table>
        </Panel>
      </el-tab-pane>

      <!-- ============ 税务申报 ============ -->
      <el-tab-pane label="税务申报" name="tax">
        <div class="jc-grid c3">
          <div v-if="tax.can_upload" class="jc-card op">
            <b>上传申报表</b>
            <div class="jc-muted jc-small mt-8">电子税务局导出的增值税/企业所得税/个税/附加税申报表 xlsx，自动识别所属期、税种与应纳税额并归档原件。</div>
            <UploadBox ref="tub" accept=".xlsx" class="mt-8" @change="(f) => (taxFile = f)" />
            <el-button class="mt-8" type="primary" :disabled="!taxFile" :loading="taxUploading" @click="uploadTax">解析登记</el-button>
          </div>
          <Panel class="jc-span-2" title="近期应纳税额" sub="按所属期汇总">
            <Chart :option="taxOption" height="220px" :empty="!tax.by_period?.length" />
          </Panel>
        </div>
        <Panel class="mt-16" :padding="false" :loading="taxLoading">
          <template #extra>
            <el-select v-model="taxType" placeholder="税种" clearable size="small" style="width: 130px" @change="loadTax">
              <el-option v-for="t in tax.tax_types" :key="t" :value="t" />
            </el-select>
          </template>
          <el-table :data="tax.items || []">
            <el-table-column label="所属期" width="100" prop="period" />
            <el-table-column label="税种" width="120" prop="tax_type" />
            <el-table-column label="计税依据" width="160" align="right"><template #default="{ row }">{{ money(row.taxable_amount) }}</template></el-table-column>
            <el-table-column label="应纳税额" width="150" align="right"><template #default="{ row }"><b class="jc-num">{{ money(row.payable_amount) }}</b></template></el-table-column>
            <el-table-column label="已缴" width="140" align="right"><template #default="{ row }">{{ money(row.paid_amount) }}</template></el-table-column>
            <el-table-column label="申报日期" width="110" prop="filing_date" />
            <el-table-column label="申报表原件" min-width="200"><template #default="{ row }"><FileLink :file="row.file" /></template></el-table-column>
            <el-table-column label="上传人" width="90" prop="uploader" />
          </el-table>
        </Panel>
      </el-tab-pane>

      <!-- ============ 项目成本登记 ============ -->
      <el-tab-pane label="项目成本登记" name="cost">
        <Panel :padding="false" :loading="cLoading">
          <div class="filters">
            <el-select v-model="cf.project" placeholder="项目" clearable filterable style="width: 260px" @change="loadCosts(1)">
              <el-option v-for="p in projects" :key="p.code" :label="`${p.code} ${p.name}`" :value="p.code" />
            </el-select>
            <el-select v-model="cf.cost_type" placeholder="类型" clearable style="width: 100px" @change="loadCosts(1)">
              <el-option v-for="t in costs.cost_types || []" :key="t" :value="t" />
            </el-select>
            <el-date-picker v-model="cf.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="成本日期" end-placeholder="至" style="width: 240px" @change="loadCosts(1)" />
            <el-input v-model="cf.keyword" placeholder="供应商 / 摘要" clearable style="width: 160px" @change="loadCosts(1)" />
            <span class="jc-spacer" />
            <span class="jc-small jc-muted">合计 <b class="jc-num">{{ money(costs.amount) }}</b></span>
            <el-button v-if="costs.can_write" type="primary" @click="openCost()">登记成本</el-button>
          </div>
          <el-table :data="costs.items || []">
            <el-table-column label="日期" width="105" prop="cost_date" />
            <el-table-column label="项目" min-width="240"><template #default="{ row }"><span class="jc-mono">{{ row.project_code }}</span> <span class="jc-muted">{{ row.project_name }}</span></template></el-table-column>
            <el-table-column label="类型" width="80" prop="cost_type" />
            <el-table-column label="金额" width="140" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.amount) }}</span></template></el-table-column>
            <el-table-column label="摘要" min-width="220" prop="summary" show-overflow-tooltip />
            <el-table-column label="付款" width="100"><template #default="{ row }"><StatusPill :text="row.bank_flow_id ? '已关联流水' : '待付款'" :tone="row.bank_flow_id ? 'up' : 'warn'" /></template></el-table-column>
            <el-table-column label="登记人" width="80" prop="created_by" />
            <el-table-column v-if="costs.can_write" width="100" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openCost(row)">编辑</el-button>
                <el-button link type="danger" :disabled="!!row.bank_flow_id" @click="delCost(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </Panel>
        <el-pagination v-model:current-page="cPage" layout="total, prev, pager, next" :total="costs.total || 0" :page-size="20" @current-change="loadCosts" />
      </el-tab-pane>
    </el-tabs>

    <!-- 批次详情 -->
    <el-drawer v-model="bd.open" :title="bd.data ? `同步批次 ${bd.data.batch_no}` : ''" size="640px">
      <div v-if="bd.data" class="jc-stack">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="通道">{{ bd.data.channel === 'api' ? 'API' : '文件' }}</el-descriptions-item>
          <el-descriptions-item label="期间">{{ bd.data.period }}</el-descriptions-item>
          <el-descriptions-item label="凭证数">{{ bd.data.voucher_count }}</el-descriptions-item>
          <el-descriptions-item label="对账">{{ REC[bd.data.reconcile_status] }}</el-descriptions-item>
          <el-descriptions-item label="借方合计">{{ money(bd.data.debit_total) }}</el-descriptions-item>
          <el-descriptions-item label="贷方合计">{{ money(bd.data.credit_total) }}</el-descriptions-item>
          <el-descriptions-item label="来源笔数">{{ bd.data.source_count }}</el-descriptions-item>
          <el-descriptions-item label="来源金额">{{ money(bd.data.source_total) }}</el-descriptions-item>
        </el-descriptions>
        <div class="jc-muted jc-small">对账口径：每张凭证借贷平衡；银行存款/库存现金科目按日发生额与工作台出纳日记账比对；文件通道另核对合计行。</div>
        <div v-if="bd.data.errors?.length">
          <div class="sec">解析错误（行级定位，批次未入库）</div>
          <el-table :data="bd.data.errors" size="small" max-height="260">
            <el-table-column label="行号" width="70" prop="row" />
            <el-table-column label="字段" width="140" prop="field" />
            <el-table-column label="说明" prop="message" />
          </el-table>
        </div>
        <div v-if="bd.data.diffs?.length">
          <div class="sec">对账差异清单</div>
          <el-table :data="bd.data.diffs" size="small" max-height="360">
            <el-table-column label="类型" width="110"><template #default="{ row }">{{ DIFF[row.type] || row.type }}</template></el-table-column>
            <el-table-column label="说明" prop="message" />
          </el-table>
        </div>
        <div v-if="!bd.data.errors?.length && !bd.data.diffs?.length" class="jc-up">对账通过，无差异。</div>
        <div v-if="cons?.diffs?.length && cons.file_batch === bd.data.batch_no">
          <div class="sec">与 API 通道的差异</div>
          <el-table :data="cons.diffs" size="small" max-height="240">
            <el-table-column label="凭证号" width="100" prop="voucher_no" />
            <el-table-column label="说明" prop="message" />
          </el-table>
        </div>
      </div>
      <template v-if="canSync && bd.data" #footer>
        <el-button @click="rerun">重新对账</el-button>
      </template>
    </el-drawer>

    <!-- 成本登记 -->
    <el-dialog v-model="cost.open" :title="cost.id ? '编辑项目成本' : '登记项目成本'" width="480px">
      <el-form label-width="80px">
        <el-form-item label="项目">
          <el-select v-model="cost.project_code" filterable style="width: 100%"><el-option v-for="p in projects" :key="p.code" :label="`${p.code} ${p.name}`" :value="p.code" /></el-select>
        </el-form-item>
        <el-form-item label="日期"><el-date-picker v-model="cost.cost_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="cost.cost_type"><el-radio v-for="t in costs.cost_types || []" :key="t" :value="t">{{ t }}</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item label="金额"><el-input-number v-model="cost.amount" :min="0.01" :precision="2" :controls="false" :disabled="cost.locked" style="width: 200px" /></el-form-item>
        <el-form-item label="摘要"><el-input v-model="cost.summary" placeholder="供应商 + 内容" maxlength="120" /></el-form-item>
      </el-form>
      <div class="jc-muted jc-small">登记后计入项目成本（PRD 3.3-4）；付款时出纳在流水核对中关联，已关联流水的成本不可改金额或删除。</div>
      <template #footer>
        <el-button @click="cost.open = false">取消</el-button>
        <el-button type="primary" :disabled="!cost.project_code || !cost.amount || !cost.cost_date" :loading="cost.saving" @click="saveCost">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime, money } from '@/utils/format'
import { baseGrid, wanAxis, wanTooltip } from '@/utils/chart'

const auth = useAuth()
const canSync = computed(() => auth.can('finance:sync'))
const REC = { pass: '通过', diff: '有差异', pending: '待对账', error: '解析失败' }
const REC_TONE = { pass: 'up', diff: 'warn', error: 'down' }
const CONS = { pass: '一致', diff: '不一致', no_file: '无文件批次' }
const DIFF = { unbalanced: '借贷不平', declared_total: '合计行不符', source_mismatch: '与日记账不符' }
const tab = ref('lemon')
const period = ref(dayjs().subtract(1, 'month').format('YYYY-MM'))
const projects = ref([])
const projName = (c) => projects.value.find((p) => p.code === c)?.name || ''
const nz = (v) => (Number(v) ? money(v) : '')

// 柠檬云
const batches = ref([]); const bLoading = ref(false); const mode = ref('mock')
const syncing = ref(false); const uploading = ref(false); const lemonFile = ref(null); const lub = ref()
const cons = ref(null)
const bd = reactive({ open: false, data: null })
async function loadBatches() {
  bLoading.value = true
  try { const r = await api.get('/finance-data/lemon/batches', { page_size: 50 }); batches.value = r.items; mode.value = r.mode } finally { bLoading.value = false }
}
async function loadCons() { cons.value = await api.get('/finance-data/lemon/consistency', { period: period.value }) }
async function syncApi() {
  syncing.value = true
  try {
    const r = await api.post('/finance-data/lemon/sync', { period: period.value })
    ElMessage.success(`同步完成：${r.voucher_count} 张凭证，对账${REC[r.reconcile_status]}`)
    loadBatches(); loadCons()
  } finally { syncing.value = false }
}
async function uploadLemon() {
  uploading.value = true
  try {
    const r = await api.upload('/finance-data/lemon/upload', lemonFile.value, {})
    if (r.reconcile_status === 'error') ElMessage.warning(`解析失败 ${r.errors.length} 处，请查看行级错误`)
    else ElMessage.success(`解析 ${r.parsed_rows}/${r.total_rows} 行（${(r.parse_rate * 100).toFixed(0)}%），对账${REC[r.reconcile_status]}`)
    lemonFile.value = null; lub.value?.reset()
    if (r.period) period.value = r.period
    await loadBatches(); loadCons()
    openBatch(r)
  } finally { uploading.value = false }
}
async function openBatch(row) { bd.data = await api.get(`/finance-data/lemon/batches/${row.id}`); bd.open = true }
async function rerun() { bd.data = await api.post(`/finance-data/lemon/batches/${bd.data.id}/reconcile`); loadBatches(); ElMessage.success('已重新对账') }

// 余额表
const bal = ref({}); const balLoading = ref(false); const supply = ref({})
async function loadBal() {
  balLoading.value = true
  try {
    bal.value = await api.get('/finance-data/balances', { period: period.value })
    supply.value = await api.get('/finance-data/lemon/supply', { period: period.value })
  } finally { balLoading.value = false }
}
function balSummary({ columns }) {
  const keys = [null, null, 'opening_debit', 'opening_credit', 'debit_amount', 'credit_amount', 'debit_balance', 'credit_balance']
  return columns.map((_, i) => (i === 0 ? '合计' : keys[i] ? money(bal.value.total?.[keys[i]]) : ''))
}

// 税务
const tax = ref({}); const taxLoading = ref(false); const taxType = ref(''); const taxFile = ref(null); const taxUploading = ref(false); const tub = ref()
async function loadTax() {
  taxLoading.value = true
  try { tax.value = await api.get('/finance-data/tax', { tax_type: taxType.value }) } finally { taxLoading.value = false }
}
async function uploadTax() {
  taxUploading.value = true
  try {
    const r = await api.upload('/finance-data/tax/upload', taxFile.value, {})
    if (r.registered.length) ElMessage.success(`已登记：${r.registered.map((x) => `${x.period} ${x.tax_type} ${money(x.payable_amount)}`).join('；')}`)
    if (r.errors.length) ElMessage.warning(r.errors.map((e) => `${e.sheet || ''} ${e.field}：${e.message}`).join('；'))
    taxFile.value = null; tub.value?.reset(); loadTax()
  } finally { taxUploading.value = false }
}
const taxOption = computed(() => {
  const d = tax.value.by_period || []
  return { grid: baseGrid(), tooltip: { trigger: 'axis', valueFormatter: wanTooltip },
    xAxis: { type: 'category', data: d.map((x) => x.period) }, yAxis: { type: 'value', ...wanAxis },
    series: [{ type: 'bar', data: d.map((x) => Number(x.payable)) }] }
})

// 成本
const cf = reactive({ project: '', cost_type: '', range: null, keyword: '' })
const costs = ref({}); const cPage = ref(1); const cLoading = ref(false)
const cost = reactive({ open: false, id: null, project_code: '', cost_date: '', cost_type: '采购', amount: null, summary: '', locked: false, saving: false })
async function loadCosts(p = cPage.value) {
  cPage.value = p; cLoading.value = true
  try {
    costs.value = await api.get('/finance-data/costs', { project: cf.project, cost_type: cf.cost_type, start: cf.range?.[0], end: cf.range?.[1], keyword: cf.keyword, page: p, page_size: 20 })
  } finally { cLoading.value = false }
}
function openCost(row) {
  Object.assign(cost, row
    ? { id: row.id, project_code: row.project_code, cost_date: row.cost_date, cost_type: row.cost_type, amount: Number(row.amount), summary: row.summary, locked: !!row.bank_flow_id }
    : { id: null, project_code: cf.project || '', cost_date: dayjs().format('YYYY-MM-DD'), cost_type: '采购', amount: null, summary: '', locked: false })
  cost.open = true
}
async function saveCost() {
  cost.saving = true
  const body = { project_code: cost.project_code, cost_date: cost.cost_date, cost_type: cost.cost_type, amount: String(cost.amount), summary: cost.summary }
  try {
    if (cost.id) await api.put(`/finance-data/costs/${cost.id}`, body)
    else await api.post('/finance-data/costs', body)
    ElMessage.success('已保存'); cost.open = false; loadCosts()
  } finally { cost.saving = false }
}
async function delCost(row) {
  await ElMessageBox.confirm(`删除 ${row.project_code} ${money(row.amount)} 的成本登记？`, '删除', { type: 'warning' })
  await api.del(`/finance-data/costs/${row.id}`); ElMessage.success('已删除'); loadCosts()
}

function reloadTab() {
  if (tab.value === 'lemon') { loadBatches(); loadCons() }
  if (tab.value === 'balance') loadBal()
  if (tab.value === 'tax') loadTax()
  if (tab.value === 'cost') loadCosts(1)
}
onMounted(async () => {
  reloadTab()
  try { projects.value = (await api.get('/expenses/options', null, { silent: true })).projects } catch { projects.value = [] }
})
</script>

<style scoped>
.op { padding: 16px 18px; }
.cons > div { display: flex; justify-content: space-between; padding: 3px 0; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 18px; border-bottom: 1px solid var(--jc-line-2); align-items: center; }
.sec { font-size: 13px; font-weight: 600; color: var(--jc-ink-2); margin-bottom: 8px; }
.empty { padding: 40px; text-align: center; }
</style>
