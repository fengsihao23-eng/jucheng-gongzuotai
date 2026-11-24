<template>
  <div class="jc-page">
    <PageHeader eyebrow="财务运营" title="流水核对" desc="上传网银流水自动解析入库（指纹去重、余额连续性校验），系统给出与合同回款、报销单、项目成本的匹配建议，确认后自动登记回款与出纳日记账。">
      <el-button v-if="can('flows:match')" :disabled="!wb.high_confidence" @click="previewAuto">自动核对高置信（{{ wb.high_confidence || 0 }}）</el-button>
      <el-button v-if="can('flows:import')" type="primary" @click="up.open = true">上传流水</el-button>
    </PageHeader>

    <el-tabs v-model="tab" @tab-change="onTab">
      <!-- ================= 核对工作台 ================= -->
      <el-tab-pane label="核对工作台" name="workbench">
        <div class="wb flow-records">
          <Panel class="left" :padding="false" :loading="wbLoading">
            <template #extra>
              <span class="jc-muted jc-small">未核对 {{ wb.unmatched || 0 }} 笔</span>
            </template>
            <div class="filters">
              <el-select v-model="wf.account" placeholder="账户" clearable style="width: 190px" @change="loadWb">
                <el-option v-for="a in accounts" :key="a.account" :label="`${a.account}（${a.unmatched}）`" :value="a.account" />
              </el-select>
              <el-segmented v-model="wf.direction" :options="[{ label: '全部', value: 0 }, { label: '收入', value: 1 }, { label: '支出', value: 2 }]" size="small" @change="loadWb" />
              <el-input v-model="wf.keyword" placeholder="对方 / 摘要" clearable style="width: 140px" @change="loadWb" />
            </div>
            <div class="flow-list">
              <div v-for="r in wb.items" :key="r.id" v-record-delete="flowRemoval(r)" class="flow-item" :class="{ on: cur?.id === r.id }" @click="showFlow(r)">
                <div class="jc-row between">
                  <span class="jc-small jc-muted">{{ r.trade_date }} · {{ shortAcct(r.bank_account) }}</span>
                  <span class="jc-num amt" :class="r.direction === 1 ? 'jc-up' : ''">{{ r.direction === 1 ? '+' : '−' }}{{ money(r.amount) }}</span>
                </div>
                <div class="jc-row between mt-4">
                  <span class="jc-ellipsis cp">{{ r.counterparty || '—' }} <span class="jc-muted">{{ r.summary }}</span></span>
                  <StatusPill v-if="r.top" :text="`${r.top.kind_name} ${r.top.score}`" :tone="r.top.score >= wb.threshold ? 'up' : r.top.score >= 70 ? 'brand' : 'warn'" />
                  <StatusPill v-else text="无建议" />
                </div>
              </div>
              <div v-if="!wb.items?.length" class="empty jc-muted">没有待核对流水</div>
            </div>
          </Panel>

          <RecordDetailDialog v-model="flowOpen" dialog-title="流水详情" :heading="cur?.counterparty || cur?.summary || '流水核对'"
            :target="{ kind: 'bank-flow', id: cur?.id }" progress-hint="记录对账差异、关联单据和待核对事项。">
            <template v-if="cur" #actions>
              <span class="jc-muted jc-small">来源 {{ cur.source_file }} 第 {{ cur.source_row }} 行</span>
            </template>
            <div v-if="!cur" class="empty jc-muted">左侧选择一笔未核对流水，查看系统匹配建议并一键确认</div>
            <div v-else v-loading="detLoading" class="jc-stack">
              <div class="flow-head">
                <div>
                  <div class="jc-muted jc-small">{{ cur.bank_account }} · {{ cur.trade_date }} {{ time(cur.trade_time) }}</div>
                  <div class="big"><span :class="cur.direction === 1 ? 'jc-up' : ''">{{ cur.direction === 1 ? '收入' : '支出' }}</span> <b class="jc-num">{{ money(cur.amount) }}</b></div>
                  <div>{{ cur.counterparty || '（无对方户名）' }} <span class="jc-muted">{{ cur.counterparty_account }}</span></div>
                  <div class="jc-muted">{{ cur.summary }}</div>
                </div>
                <div class="jc-muted jc-small" style="text-align:right">余额<br /><span class="jc-num">{{ money(cur.balance) }}</span></div>
              </div>

              <div v-if="!detail.suggestions?.length" class="jc-muted jc-small">未找到自动匹配，可在下方手工指定。</div>
              <div v-for="(s, i) in detail.suggestions" :key="i" class="sug" :class="{ top: i === 0 }">
                <div class="jc-row between">
                  <div>
                    <StatusPill :text="s.kind_name" tone="brand" />
                    <b class="ttl">{{ s.title }}</b>
                  </div>
                  <div class="score" :class="s.score >= wb.threshold ? 'hi' : s.score >= 70 ? 'mid' : 'lo'">
                    <span class="jc-num">{{ s.score }}</span><span class="jc-small">置信 {{ s.confidence }}</span>
                  </div>
                </div>
                <ul class="reasons">
                  <li v-for="(x, j) in s.reasons" :key="j">{{ x }}</li>
                </ul>
                <div v-if="s.extra" class="jc-small jc-muted extra">
                  <template v-if="s.kind === 'payment'">合同额 {{ money(s.extra.contract_amount) }} · 未回款 {{ money(s.extra.balance) }} · {{ s.extra.project_code }} {{ s.extra.project_name }}</template>
                  <template v-else-if="s.kind === 'expense'">{{ s.extra.applicant }} · {{ s.extra.category }} · {{ s.extra.project_code || '公共费用' }} · 审批通过 {{ datetime(s.extra.approved_at) }}</template>
                  <template v-else-if="s.kind === 'project_cost'">项目 {{ s.extra.project_code }} · {{ s.extra.cost_type }}</template>
                </div>
                <div class="jc-row" style="justify-content:flex-end">
                  <el-button v-if="can('flows:match')" :type="i === 0 ? 'primary' : 'default'" size="small" :loading="saving" @click="confirmSug(s)">确认{{ s.kind === 'project_cost' && !s.target_id ? '登记' : '核对' }}</el-button>
                </div>
              </div>

              <div v-if="can('flows:match')" class="manual">
                <div class="sec">手工核对</div>
                <el-radio-group v-model="man.kind" size="small" @change="loadCands">
                  <template v-if="cur.direction === 1">
                    <el-radio-button value="payment">合同回款</el-radio-button>
                    <el-radio-button value="other_income">其他收入</el-radio-button>
                  </template>
                  <template v-else>
                    <el-radio-button value="expense">报销单</el-radio-button>
                    <el-radio-button value="project_cost">项目成本</el-radio-button>
                    <el-radio-button value="public">公户支出</el-radio-button>
                  </template>
                </el-radio-group>
                <div v-if="['payment', 'expense', 'project_cost'].includes(man.kind)" class="mt-8 jc-row wrap">
                  <el-input v-model="man.keyword" placeholder="搜索客户/合同号/申请人/单号" clearable style="width: 220px" @change="loadCands" />
                  <el-select v-model="man.target" placeholder="选择关联单据" filterable clearable style="flex:1; min-width: 240px">
                    <el-option v-for="c in man.cands" :key="c.id" :label="`${c.label}  ${money(c.balance ?? c.amount)}`" :value="c.id">
                      <span>{{ c.label }}</span><span class="jc-muted" style="float:right">{{ money(c.balance ?? c.amount) }}</span>
                    </el-option>
                  </el-select>
                </div>
                <div v-if="man.kind === 'project_cost' && !man.target" class="mt-8 jc-row wrap">
                  <span class="jc-small jc-muted">或新登记：</span>
                  <el-select v-model="man.project" placeholder="项目" filterable style="width: 260px">
                    <el-option v-for="p in projects" :key="p.code" :label="`${p.code} ${p.name}`" :value="p.code" />
                  </el-select>
                  <el-select v-model="man.cost_type" style="width: 90px"><el-option v-for="t in ['采购', '分包', '人工', '其他']" :key="t" :value="t" /></el-select>
                </div>
                <el-input v-model="man.remark" class="mt-8" placeholder="备注（可选）" maxlength="60" />
                <div class="jc-row mt-8" style="justify-content:flex-end">
                  <el-button type="primary" size="small" :disabled="!manReady" :loading="saving" @click="confirmManual">确认</el-button>
                </div>
              </div>
            </div>
          </RecordDetailDialog>
        </div>
      </el-tab-pane>

      <!-- ================= 全部流水 ================= -->
      <el-tab-pane label="全部流水" name="list">
        <Panel :padding="false" :loading="lLoading">
          <div class="filters">
            <el-select v-model="lf.account" placeholder="账户" clearable style="width: 200px" @change="loadList(1)">
              <el-option v-for="a in accounts" :key="a.account" :label="a.account" :value="a.account" />
            </el-select>
            <el-select v-model="lf.direction" placeholder="方向" clearable style="width: 90px" @change="loadList(1)">
              <el-option label="收入" :value="1" /><el-option label="支出" :value="2" />
            </el-select>
            <el-select v-model="lf.status" style="width: 130px" @change="loadList(1)">
              <el-option label="全部状态" value="all" /><el-option label="已核对" value="matched" />
              <el-option v-for="(v, k) in FLOW_MATCH" :key="k" :label="v" :value="String(k)" />
            </el-select>
            <el-date-picker v-model="lf.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="交易日期" end-placeholder="至" style="width: 240px" @change="loadList(1)" />
            <el-input-number v-model="lf.min" :controls="false" placeholder="最小金额" style="width: 100px" @change="loadList(1)" />
            <el-input-number v-model="lf.max" :controls="false" placeholder="最大金额" style="width: 100px" @change="loadList(1)" />
            <el-input v-model="lf.keyword" placeholder="对方 / 摘要 / 单号" clearable style="width: 170px" @change="loadList(1)" />
          </div>
          <div class="sumline jc-small">
            <span>共 <b class="jc-num">{{ list.total }}</b> 笔</span>
            <span>收入 <b class="jc-num jc-up">{{ money(list.summary?.income) }}</b></span>
            <span>支出 <b class="jc-num">{{ money(list.summary?.expense) }}</b></span>
          </div>
          <el-table :data="list.items" @row-click="showFlow" :row-class-name="({ row }) => (row.match_status === 0 ? 'is-alert' : '')" @row-contextmenu="flowMenu">
            <el-table-column label="交易日期" width="105" prop="trade_date" />
            <el-table-column label="账户" width="190" prop="bank_account" show-overflow-tooltip />
            <el-table-column label="收入" width="130" align="right"><template #default="{ row }"><span v-if="row.direction === 1" class="jc-num jc-up">{{ money(row.amount) }}</span></template></el-table-column>
            <el-table-column label="支出" width="130" align="right"><template #default="{ row }"><span v-if="row.direction === 2" class="jc-num">{{ money(row.amount) }}</span></template></el-table-column>
            <el-table-column label="余额" width="140" align="right"><template #default="{ row }"><span class="jc-num jc-muted">{{ money(row.balance) }}</span></template></el-table-column>
            <el-table-column label="对方户名" min-width="170" prop="counterparty" show-overflow-tooltip />
            <el-table-column label="摘要" min-width="180" prop="summary" show-overflow-tooltip />
            <el-table-column label="核对状态" width="120"><template #default="{ row }"><StatusPill :text="row.match_status_name" :tone="row.match_status ? 'up' : 'warn'" dot /></template></el-table-column>
            <el-table-column label="关联单号" width="160"><template #default="{ row }"><span class="jc-mono">{{ row.biz_ref || '' }}</span></template></el-table-column>
            <el-table-column width="80" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.match_status === 0" link type="primary" @click.stop="gotoWb(row)">核对</el-button>
                <el-button v-else-if="can('flows:match')" link type="danger" @click.stop="undo(row)">撤销</el-button>
              </template>
            </el-table-column>
          </el-table>
        </Panel>
        <el-pagination v-model:current-page="lPage" layout="total, prev, pager, next" :total="list.total || 0" :page-size="20" @current-change="loadList" />
      </el-tab-pane>

      <!-- ================= 导入记录 ================= -->
      <el-tab-pane label="导入记录" name="imports">
        <Panel :padding="false" :loading="iLoading">
          <el-table :data="imports.items" @row-click="(r) => showReport(r)" @row-contextmenu="bankImportMenu">
            <el-table-column label="导入时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
            <el-table-column label="文件" min-width="220"><template #default="{ row }"><FileLink :file="row.file" /></template></el-table-column>
            <el-table-column label="银行模板" width="100" prop="template_name" />
            <el-table-column label="来源" width="90"><template #default="{ row }">{{ SOURCE[row.source] || row.source }}</template></el-table-column>
            <el-table-column label="总行数" width="80" align="right" prop="total_rows" />
            <el-table-column label="新增" width="80" align="right"><template #default="{ row }"><b class="jc-up">{{ row.inserted_rows }}</b></template></el-table-column>
            <el-table-column label="重复" width="80" align="right" prop="duplicate_rows" />
            <el-table-column label="错误" width="80" align="right"><template #default="{ row }"><span :class="{ 'jc-down': row.error_rows }">{{ row.error_rows }}</span></template></el-table-column>
            <el-table-column label="余额校验" width="100"><template #default="{ row }"><StatusPill v-if="row.balance_check" :text="row.balance_check.ok ? '连续' : '有断点'" :tone="row.balance_check.ok ? 'up' : 'warn'" /></template></el-table-column>
            <el-table-column label="耗时" width="80" align="right"><template #default="{ row }">{{ row.cost_ms }} ms</template></el-table-column>
            <el-table-column label="上传人" width="90" prop="uploader" />
          </el-table>
        </Panel>
        <el-pagination v-model:current-page="iPage" layout="total, prev, pager, next" :total="imports.total || 0" :page-size="20" @current-change="loadImports" />
      </el-tab-pane>
    </el-tabs>

    <!-- 上传 -->
    <el-dialog v-model="up.open" title="上传网银流水" width="520px">
      <div class="jc-stack">
        <UploadBox ref="ub" accept=".xlsx,.csv" tip="支持招商 / 工商 / 建设银行网银导出 xlsx、csv；自动识别表头" @change="(f) => (up.file = f)" />
        <div class="jc-row">
          <el-select v-model="up.template" placeholder="银行模板（默认自动识别）" clearable style="flex:1">
            <el-option v-for="t in templates" :key="t.key" :label="t.name" :value="t.key" />
          </el-select>
          <el-select v-model="up.account" placeholder="本方账户（文件无账号时选择）" clearable style="flex:1">
            <el-option v-for="a in tplAccounts" :key="a.name" :label="a.name" :value="a.name" />
          </el-select>
        </div>
        <div class="jc-muted jc-small">去重口径：账号 + 日期 + 方向 + 金额 + 摘要 + 对方 + 交易流水号/余额；重复导入不会产生重复记录。</div>
      </div>
      <template #footer>
        <el-button @click="up.open = false">取消</el-button>
        <el-button type="primary" :disabled="!up.file" :loading="up.saving" @click="doUpload">解析入库</el-button>
      </template>
    </el-dialog>

    <!-- 导入报告 -->
    <RecordDetailDialog v-model="rep.open" dialog-title="银行流水导入详情" :heading="rep.data?.filename"
      :target="{ kind: 'bank-import', id: rep.data?.id }" progress-hint="说明导入错误、重复记录或余额差异的核对进展。">
      <div v-if="rep.data" class="jc-stack">
        <div class="jc-muted jc-small">{{ rep.data.filename }} · {{ rep.data.template_name }} · 耗时 {{ rep.data.cost_ms }} ms</div>
        <div class="jc-grid c4">
          <div class="stat"><span>总行数</span><b class="jc-num">{{ rep.data.total_rows }}</b></div>
          <div class="stat"><span>新增</span><b class="jc-num jc-up">{{ rep.data.inserted_rows }}</b></div>
          <div class="stat"><span>重复跳过</span><b class="jc-num">{{ rep.data.duplicate_rows }}</b></div>
          <div class="stat"><span>错误</span><b class="jc-num" :class="{ 'jc-down': rep.data.error_rows }">{{ rep.data.error_rows }}</b></div>
        </div>
        <div v-if="rep.data.suggest_summary" class="jc-small">
          新增流水中 <b>{{ rep.data.suggest_summary.high_confidence }}</b> 笔有高置信匹配建议，可在核对工作台一键确认或批量自动核对。
        </div>
        <div v-for="a in rep.data.balance_check?.accounts || []" :key="a.account" class="jc-card bc jc-small">
          <div class="jc-row between">
            <b>{{ a.account }}</b>
            <StatusPill :text="a.breaks.length ? `余额断点 ${a.breaks.length}` : '余额连续'" :tone="a.breaks.length ? 'warn' : 'up'" />
          </div>
          <div class="jc-muted mt-4">
            {{ a.rows }} 行 · {{ a.order === 'desc' ? '倒序' : '正序' }} · 期初 {{ money(a.opening) }} → 期末 {{ money(a.closing) }}
            <template v-if="a.db_continuity !== null"> · 与库内上一笔{{ a.db_continuity ? '衔接' : `不衔接（库内 ${money(a.db_previous_balance)}）` }}</template>
          </div>
        </div>
        <el-table v-if="allErrors.length" :data="allErrors" size="small" max-height="260">
          <el-table-column label="行号" width="70" prop="row" />
          <el-table-column label="字段" width="140" prop="field" />
          <el-table-column label="说明" prop="message" />
        </el-table>
      </div>
      <template #footer>
        <el-button @click="rep.open = false">关闭</el-button>
        <el-button type="primary" @click="rep.open = false; tab = 'workbench'; loadWb()">去核对</el-button>
      </template>
    </RecordDetailDialog>

    <!-- 自动核对预览 -->
    <el-dialog v-model="auto.open" title="自动核对高置信建议" width="640px">
      <div class="jc-small jc-muted">阈值 {{ auto.data?.threshold }} 分且领先其他候选 ≥ 10 分的建议将直接确认入账，其余保留人工处理。</div>
      <el-table :data="auto.data?.items || []" size="small" max-height="320" class="mt-8">
        <el-table-column label="流水" width="80" prop="flow_id" />
        <el-table-column label="金额" width="120" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
        <el-table-column label="核对为" prop="title" />
        <el-table-column label="置信" width="70" prop="score" />
      </el-table>
      <template #footer>
        <el-button @click="auto.open = false">取消</el-button>
        <el-button type="primary" :disabled="!auto.data?.matched" :loading="auto.saving" @click="runAuto">确认核对 {{ auto.data?.matched || 0 }} 笔</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { FLOW_MATCH, datetime, money, time } from '@/utils/format'

const auth = useAuth()
const route = useRoute()
const can = (b) => auth.can(b)
const flowRemoval = row => auth.canDelete('flows:import', 'flows:match') &&
  recordRemoval('bank-flow', row.id, row.counterparty || row.summary || '银行流水', async () => {
    if (cur.value?.id === row.id) cur.value = null
    await Promise.all([loadWb(), loadList()])
  }, { title: '删除银行流水' })
const flowMenu = rowDeletion(flowRemoval)
const bankImportMenu = rowDeletion(row => auth.canDelete('flows:import') && recordRemoval('bank-import', row.id, row.filename,
  () => loadImports(), { title: '删除流水导入记录', description: `确认移除「${row.filename}」的导入记录？银行流水可在流水核对中逐笔处理。` }))
const SOURCE = { upload: '上传', auto_dir: '目录自动', mcp: 'WorkBuddy' }
const tab = ref('workbench')
const accounts = ref([]); const templates = ref([]); const tplAccounts = ref([]); const projects = ref([])
const shortAcct = (s) => (s || '').replace(/(基本户|一般户)/, '')

// 工作台
const wf = reactive({ account: '', direction: 0, keyword: '' })
const wb = ref({ items: [] }); const wbLoading = ref(false)
const flowOpen = ref(false)
const cur = ref(null); const detail = ref({}); const detLoading = ref(false); const saving = ref(false)
const man = reactive({ kind: '', keyword: '', target: null, cands: [], project: '', cost_type: '采购', remark: '' })

async function loadWb() {
  wbLoading.value = true
  try {
    wb.value = await api.get('/flows/workbench', { account: wf.account, direction: wf.direction || undefined, keyword: wf.keyword })
    if (cur.value && !wb.value.items.find((x) => x.id === cur.value.id)) { cur.value = null; flowOpen.value = false }
  } finally { wbLoading.value = false }
}
function showFlow(row) { flowOpen.value = true; pick(row) }
async function pick(r) {
  cur.value = r; detLoading.value = true
  Object.assign(man, { kind: r.direction === 1 ? 'payment' : 'expense', keyword: '', target: null, cands: [], project: '', remark: '' })
  try {
    detail.value = await api.get(`/flows/${r.id}`)
    cur.value = { ...r, ...detail.value }
    loadCands()
  } finally { detLoading.value = false }
}
async function loadCands() {
  man.target = null
  if (!cur.value || !['payment', 'expense', 'project_cost'].includes(man.kind)) { man.cands = []; return }
  man.cands = await api.get(`/flows/${cur.value.id}/candidates`, { kind: man.kind, keyword: man.keyword })
}
const manReady = computed(() => {
  if (['public', 'other_income'].includes(man.kind)) return true
  if (man.kind === 'project_cost') return !!(man.target || man.project)
  return !!man.target
})
async function submit(body) {
  saving.value = true
  try {
    await api.post(`/flows/${cur.value.id}/match`, body)
    ElMessage.success('核对成功，已登记日记账')
    const idx = wb.value.items.findIndex((x) => x.id === cur.value.id)
    cur.value = null
    await loadWb()
    const next = wb.value.items[Math.min(idx, wb.value.items.length - 1)]
    if (next) pick(next)
    loadAccounts()
  } finally { saving.value = false }
}
function confirmSug(s) {
  submit({ kind: s.kind, target_id: s.target_id, project_code: s.extra?.project_code, cost_type: s.extra?.cost_type })
}
function confirmManual() {
  submit({ kind: man.kind, target_id: man.target || null, project_code: man.project || null, cost_type: man.cost_type, remark: man.remark || null })
}

// 自动核对
const auto = reactive({ open: false, data: null, saving: false })
async function previewAuto() {
  auto.data = await api.post('/flows/auto-match', { dry_run: true }); auto.open = true
}
async function runAuto() {
  auto.saving = true
  try {
    const r = await api.post('/flows/auto-match', { flow_ids: auto.data.items.map((x) => x.flow_id) })
    ElMessage.success(`已自动核对 ${r.matched} 笔`)
    auto.open = false; cur.value = null; loadWb(); loadAccounts()
  } finally { auto.saving = false }
}

// 全部流水
const lf = reactive({ account: '', direction: null, status: 'all', range: null, min: null, max: null, keyword: '' })
const list = ref({ items: [] }); const lPage = ref(1); const lLoading = ref(false)
async function loadList(p = lPage.value) {
  lPage.value = p; lLoading.value = true
  try {
    list.value = await api.get('/flows', { account: lf.account, direction: lf.direction || undefined, status: lf.status, start: lf.range?.[0], end: lf.range?.[1],
      min_amount: lf.min ?? undefined, max_amount: lf.max ?? undefined, keyword: lf.keyword, page: p, page_size: 20 })
  } finally { lLoading.value = false }
}
function gotoWb(row) { tab.value = 'workbench'; wf.account = row.bank_account; loadWb().then(() => showFlow(row)) }
async function undo(row) {
  await ElMessageBox.confirm(`撤销该流水的核对（${row.match_status_name}）？将回滚关联的回款/报销付款状态/项目成本与日记账。`, '撤销核对', { type: 'warning' })
  await api.post(`/flows/${row.id}/unmatch`)
  ElMessage.success('已撤销'); loadList(); loadAccounts()
}

// 导入
const up = reactive({ open: false, file: null, template: '', account: '', saving: false })
const ub = ref()
const rep = reactive({ open: false, data: null })
const allErrors = computed(() => [...(rep.data?.errors || []), ...(rep.data?.balance_check?.accounts || []).flatMap((a) => a.breaks)].sort((a, b) => a.row - b.row))
async function doUpload() {
  up.saving = true
  try {
    rep.data = await api.upload('/flows/import', up.file, { template: up.template, account: up.account })
    up.open = false; up.file = null; ub.value?.reset(); rep.open = true
    loadAccounts(); loadWb(); if (tab.value === 'imports') loadImports(1)
  } finally { up.saving = false }
}
async function showReport(row) { rep.data = await api.get(`/flows/imports/${row.id}`); rep.open = true }
const imports = ref({ items: [] }); const iPage = ref(1); const iLoading = ref(false)
async function loadImports(p = iPage.value) {
  iPage.value = p; iLoading.value = true
  try { imports.value = await api.get('/flows/imports', { page: p, page_size: 20 }) } finally { iLoading.value = false }
}

async function loadAccounts() { accounts.value = await api.get('/flows/accounts') }
function onTab(t) { if (t === 'list') loadList(1); if (t === 'imports') loadImports(1); if (t === 'workbench') loadWb() }

onMounted(async () => {
  const rq = route.query
  if (rq.match_status !== undefined || rq.date || rq.direction) {
    if (rq.match_status !== undefined) lf.status = String(rq.match_status)
    if (rq.direction) lf.direction = Number(rq.direction)
    if (rq.date) lf.range = [String(rq.date), String(rq.date)]
    tab.value = 'list'; loadList(1)
  }
  loadAccounts(); loadWb()
  const t = await api.get('/flows/templates'); templates.value = t.templates; tplAccounts.value = t.accounts
  try { projects.value = (await api.get('/expenses/options', null, { silent: true })).projects } catch { projects.value = [] }
})
</script>

<style scoped>
.wb.flow-records { grid-template-columns: minmax(0, 1fr); }
.wb { display: grid; grid-template-columns: minmax(340px, 5fr) 7fr; gap: 16px; align-items: start; }
@media (max-width: 1100px) { .wb { grid-template-columns: 1fr; } }
.filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--jc-line-2); align-items: center; }
.sumline { display: flex; gap: 20px; padding: 8px 18px; color: var(--jc-ink-3); border-bottom: 1px solid var(--jc-line-2); }
.sumline b { color: var(--jc-ink); }
.flow-list { max-height: 640px; overflow: auto; }
.flow-item { padding: 10px 16px; border-bottom: 1px solid var(--jc-line-2); cursor: pointer; border-left: 2px solid transparent; }
.flow-item:hover { background: var(--jc-surface-2); }
.flow-item.on { background: var(--jc-brand-50); border-left-color: var(--jc-brand); }
.flow-item .amt { font-weight: 600; }
.flow-item .cp { max-width: 70%; font-size: 13px; }
.mt-4 { margin-top: 4px; }
.empty { padding: 48px 16px; text-align: center; }
.flow-head { display: flex; justify-content: space-between; gap: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--jc-line-2); }
.flow-head .big { font-size: 22px; margin: 4px 0; }
.sug { border: 1px solid var(--jc-line); border-radius: var(--jc-radius-sm); padding: 12px 14px; }
.sug.top { border-color: var(--jc-brand-100); background: #fbfbfd; }
.sug .ttl { margin-left: 8px; font-weight: 600; }
.score { display: flex; flex-direction: column; align-items: flex-end; line-height: 1.2; }
.score .jc-num { font-size: 22px; font-weight: 600; }
.score.hi .jc-num { color: var(--jc-up); } .score.mid .jc-num { color: var(--jc-brand); } .score.lo .jc-num { color: var(--jc-warn); }
.reasons { margin: 8px 0 6px; padding-left: 18px; font-size: 12.5px; color: var(--jc-ink-2); }
.extra { margin-bottom: 6px; }
.manual { border-top: 1px dashed var(--jc-line); padding-top: 12px; }
.sec { font-size: 13px; font-weight: 600; color: var(--jc-ink-2); margin-bottom: 8px; }
.stat { border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
.stat span { font-size: 12px; color: var(--jc-ink-3); }
.stat b { font-size: 22px; font-weight: 600; }
.bc { padding: 10px 12px; box-shadow: none; background: var(--jc-surface-2); }
</style>
