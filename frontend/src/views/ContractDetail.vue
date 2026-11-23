<!-- 合同详情：基本信息、审批溯源、回款计划进度、回款记录、归档上传 → 解析结果确认 → 保存、版本列表 -->
<template>
  <div class="jc-page" v-loading="loading">
    <template v-if="c">
      <PageHeader eyebrow="合同台账" :title="c.name || c.contract_no" :desc="`${c.contract_no} · ${c.customer_name}`">
        <template #meta>
          <div class="jc-row mt-8">
            <StatusPill :text="c.status_name" :tone="c.status === 2 ? 'up' : c.status === 3 ? 'down' : 'info'" />
            <StatusPill :text="c.source_name" :tone="c.source_type === 1 ? 'brand' : ''" />
            <StatusPill v-if="c.archived" text="原件已归档" tone="up" dot />
          </div>
        </template>
        <el-button @click="$router.push('/contracts')">返回台账</el-button>
        <RecordFeedbackButton :target="{ kind: 'contract', id: c.id }" :heading="c.name || c.contract_no"
          progress-hint="说明签署、履约或交付目前推进到哪一步。" />
        <el-button v-if="auth.hasMenu('receivables')" @click="$router.push({ path: '/receivables', query: { keyword: c.contract_no, contract_no: c.contract_no } })">查看开票与收款</el-button>
        <el-button v-if="c.perms?.payment && !auth.can('receivables:receipt')" @click="payOpen = true">登记回款</el-button>
        <el-button v-if="c.perms?.write" @click="editOpen = true">编辑</el-button>
        <el-button v-if="c.perms?.write" @click="approvalOpen = true">发起合同审批</el-button>
        <el-button v-if="c.perms?.archive" type="primary" @click="archiveOpen = true">{{ c.archived ? '上传新版本' : '合同归档' }}</el-button>
      </PageHeader>

      <div class="jc-grid c4">
        <Kpi label="合同金额" :value="wanNum(c.amount)" unit="万" :hint="`签订 ${c.sign_date}`" />
        <Kpi label="已回款" :value="wanNum(c.received)" unit="万" :progress="c.received_rate" :hint="`回款率 ${percent(c.received_rate)}`" />
        <Kpi label="未回款" :value="wanNum(c.receivable)" unit="万" :hint="overdueHint" />
        <Kpi label="回款计划" :value="c.plans.length" unit="期" :hint="c.plans.length ? `已完成 ${c.plans.filter((p) => p.status === 4).length} 期` : '归档后自动读取付款条款'" />
      </div>

      <ProjectReportSources :contract-id="c.id" />
      <Panel v-if="c.workbook_sources?.length" class="mt-16" title="企业微信原表对账">
        <div v-for="src in c.workbook_sources" :key="src.key" class="jc-stack">
          <router-link :to="{ path: '/contracts', query: { workbook: src.key, sheet: src.sheet_id, row: src.row } }">
            {{ src.sheet_name }} · 第 {{ src.row }} 行
          </router-link>
          <span v-if="!src.present" class="jc-muted jc-small">该行已不在当前原表，工作台合同保留。</span>
          <div v-else-if="src.data?.receipts" class="jc-row wrap jc-small">
            <span>原表累计回款 {{ money(src.data.receipts.source) }}</span>
            <span>实际已登记 {{ money(src.data.receipts.registered) }}</span>
            <span>差额 {{ money(src.data.receipts.difference) }}</span>
            <span v-if="src.data.record?.paid_on">原表到账日期 {{ src.data.record.paid_on }}</span>
            <router-link v-if="auth.hasMenu('receivables')" :to="{ path: '/receivables', query: { tab: 'receipts', keyword: c.contract_no } }">
              核对到账记录
            </router-link>
          </div>
          <span v-if="src.data?.issues?.length" class="jc-warn jc-small">{{ src.data.issues.join('；') }}</span>
        </div>
      </Panel>

      <div class="jc-grid c2 mt-16">
        <Panel title="基本信息">
          <el-descriptions :column="2" size="small">
            <el-descriptions-item label="合同编号"><span class="jc-mono">{{ c.contract_no }}</span></el-descriptions-item>
            <el-descriptions-item label="业务线">{{ c.business_line_name }}</el-descriptions-item>
            <el-descriptions-item label="客户">{{ c.customer_name }}</el-descriptions-item>
            <el-descriptions-item label="销售负责人">{{ c.owner_name || '—' }}</el-descriptions-item>
            <el-descriptions-item label="签订日期">{{ c.sign_date }}</el-descriptions-item>
            <el-descriptions-item label="归档时间">{{ datetime(c.archived_at) }}</el-descriptions-item>
            <el-descriptions-item label="关联项目" :span="2">
              <router-link v-if="c.project" :to="`/projects/${c.project.project_code}`">{{ c.project.project_code }} {{ c.project.name }}</router-link>
              <span v-if="c.project" class="jc-muted jc-small">（{{ c.project.status_text }} · 项目经理 {{ c.project.owner_name || '—' }}）</span>
              <span v-else class="jc-muted">未关联</span>
            </el-descriptions-item>
            <el-descriptions-item label="备注" :span="2">{{ c.remark || '—' }}</el-descriptions-item>
          </el-descriptions>
        </Panel>
        <Panel title="来源审批单溯源" :sub="c.approval ? `${c.approval.sp_name} · ${c.approval.status_name}` : ''">
          <template v-if="c.approval">
            <el-descriptions :column="2" size="small">
              <el-descriptions-item label="审批单号"><span class="jc-mono">{{ c.approval.sp_no }}</span></el-descriptions-item>
              <el-descriptions-item label="申请人">{{ c.approval.applicant_name || c.approval.applicant_userid }}</el-descriptions-item>
              <el-descriptions-item label="提交时间">{{ datetime(c.approval.apply_time) }}</el-descriptions-item>
              <el-descriptions-item label="通过时间">{{ datetime(c.approval.approved_time) }}</el-descriptions-item>
              <el-descriptions-item label="接收方式">{{ VIA[c.approval.received_via] || c.approval.received_via }}</el-descriptions-item>
              <el-descriptions-item label="登记时间">{{ datetime(c.approval.dispatched_at) }}</el-descriptions-item>
            </el-descriptions>
            <div class="sub-t">企微审批原始字段</div>
            <table class="raw">
              <tr v-for="(v, k) in c.approval.fields" :key="k"><th>{{ k }}</th><td>{{ v || '—' }}</td></tr>
            </table>
          </template>
          <div v-else class="jc-muted jc-small">{{ c.source_type === 2 ? '人工录入，无审批单' : c.source_type === 3 ? 'Excel 清洗导入' : c.source_type === 4 ? '企业微信表格同步' : '未找到审批单记录' }}</div>
        </Panel>
      </div>

      <Panel class="mt-16" title="回款计划" :padding="false" sub="付款条款原文来自归档合同；阶段按顺序冲抵（预付→进度→验收→质保）">
        <template #extra><el-button v-if="c.perms?.archive && c.plans.length" link type="primary" @click="editPlans">调整计划</el-button></template>
        <el-table :data="c.plans" :row-class-name="({ row }) => (row.overdue ? 'is-alert' : '')" empty-text="暂无回款计划，上传合同原件后自动读取">
          <el-table-column label="#" width="44" prop="seq" />
          <el-table-column label="阶段" width="100" prop="stage_name" />
          <el-table-column label="比例" width="80"><template #default="{ row }">{{ percent(row.ratio, 0) }}</template></el-table-column>
          <el-table-column label="应收" width="130" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.amount, 0) }}</span></template></el-table-column>
          <el-table-column label="计划日期" width="110" prop="due_date" />
          <el-table-column label="回款进度" width="200">
            <template #default="{ row }">
              <div class="pg"><i :style="{ width: Math.min(100, (row.progress || 0) * 100) + '%' }" :class="{ done: row.status === 4 }" /></div>
              <span class="jc-small jc-muted">{{ money(row.received_amount, 0) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="125"><template #default="{ row }"><StatusPill :text="row.overdue && row.status === 3 ? '部分已收，逾期' : row.status_name" :tone="row.overdue ? 'down' : PLAN_TONE[row.status]" /></template></el-table-column>
          <el-table-column label="条款原文" min-width="280"><template #default="{ row }"><span class="jc-small jc-muted">{{ row.condition_text }}</span></template></el-table-column>
        </el-table>
      </Panel>

      <div class="jc-grid c2 mt-16">
        <Panel title="回款记录" :padding="false" :sub="`${c.payments.length} 笔`">
          <el-table :data="c.payments" max-height="320" empty-text="暂无回款">
            <el-table-column label="回款 ID" width="86" prop="id" />
            <el-table-column label="到账日" width="100" prop="payment_date" />
            <el-table-column label="金额" width="130" align="right"><template #default="{ row }"><span class="jc-num">{{ money(row.amount, 0) }}</span></template></el-table-column>
            <el-table-column label="阶段" width="80"><template #default="{ row }">{{ row.plan_stage || '—' }}</template></el-table-column>
            <el-table-column label="来源" width="110"><template #default="{ row }"><StatusPill :text="row.source_name" :tone="row.source_type === 3 ? 'brand' : row.source_type === 1 ? 'info' : ''" /></template></el-table-column>
            <el-table-column label="说明" min-width="160"><template #default="{ row }"><span class="jc-small">{{ row.counterparty || row.remark || '—' }}</span></template></el-table-column>
          </el-table>
        </Panel>
        <Panel title="归档文件版本" :padding="false">
          <el-table :data="c.files" empty-text="尚未归档原件">
            <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version }}
              <StatusPill v-if="row.current" text="当前" tone="up" /></template></el-table-column>
            <el-table-column label="文件" min-width="200"><template #default="{ row }"><FileLink :file="row" /></template></el-table-column>
            <el-table-column label="大小" width="80"><template #default="{ row }">{{ filesize(row.size) }}</template></el-table-column>
            <el-table-column label="上传时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
          </el-table>
        </Panel>
      </div>
    </template>

    <!-- 归档上传 → 解析结果确认 -->
    <el-dialog v-model="archiveOpen" title="合同归档" width="920px" @closed="resetArchive">
      <template v-if="!parsed">
        <UploadBox ref="ub" accept=".docx,.txt,.pdf,.doc" title="拖拽合同原件到此处，或点击选择"
                   tip="支持 docx / txt（自动读取付款条款）；pdf / doc 仅归档，需另存为 docx 才能解析" @change="(f) => (file = f)" />
        <div class="jc-muted jc-small mt-8">归档后：原件按版本保存 → 规则识别付款条款生成回款计划 → 自动匹配该客户未核对的到账流水并通知财务与市场负责人。</div>
      </template>
      <template v-else>
        <el-alert v-for="w in parsed.parsed.warnings" :key="w" :title="w" type="warning" :closable="false" show-icon class="mb-8" />
        <el-alert v-if="parsed.linked.length" type="success" :closable="false" show-icon class="mb-8"
                  :title="`已自动关联到账流水 ${parsed.linked.length} 笔：` + parsed.linked.map((x) => `${x.trade_date} ${money(x.amount, 0)} → ${x.stage_name}`).join('；')" />
        <div class="jc-row between mb-8">
          <span>识别到 <b>{{ planDraft.length }}</b> 个付款阶段，比例合计
            <b :class="ratioTotal === 100 ? 'jc-up' : 'jc-warn'">{{ ratioTotal }}%</b>，金额合计
            <b :class="amountTotal === num(c.amount) ? 'jc-up' : 'jc-warn'">{{ money(amountTotal, 0) }}</b>（合同 {{ money(c.amount, 0) }}）</span>
          <el-button size="small" @click="addStage">添加阶段</el-button>
        </div>
        <el-table :data="planDraft" size="small">
          <el-table-column label="#" width="40" type="index" />
          <el-table-column label="阶段" width="120"><template #default="{ row }"><el-input v-model="row.stage_name" size="small" /></template></el-table-column>
          <el-table-column label="比例 %" width="110">
            <template #default="{ row }"><el-input-number v-model="row.pct" size="small" :min="0" :max="100" :precision="2" :controls="false" style="width: 90px" @change="onPct(row)" /></template>
          </el-table-column>
          <el-table-column label="金额" width="150">
            <template #default="{ row }"><el-input-number v-model="row.amount" size="small" :min="0" :precision="2" :controls="false" style="width: 130px" /></template>
          </el-table-column>
          <el-table-column label="计划日期" width="150">
            <template #default="{ row }"><el-date-picker v-model="row.due_date" size="small" type="date" value-format="YYYY-MM-DD" style="width: 130px" /></template>
          </el-table-column>
          <el-table-column label="条款原文 / 日期依据" min-width="240">
            <template #default="{ row }"><div class="jc-small">{{ row.condition_text }}</div><div class="jc-small jc-muted" v-if="row.due_basis">日期估算：{{ row.due_basis }}</div></template>
          </el-table-column>
          <el-table-column width="50"><template #default="{ $index }"><el-button link type="danger" @click="planDraft.splice($index, 1)">删</el-button></template></el-table-column>
        </el-table>
        <details v-if="parsed.text_excerpt" class="mt-16"><summary class="jc-muted jc-small">原文摘录</summary>
          <pre class="excerpt">{{ parsed.text_excerpt }}</pre></details>
      </template>
      <template #footer>
        <template v-if="!parsed">
          <el-button @click="archiveOpen = false">取消</el-button>
          <el-button type="primary" :disabled="!file" :loading="uploading" @click="doArchive">上传并解析</el-button>
        </template>
        <template v-else>
          <el-button @click="archiveOpen = false">关闭</el-button>
          <el-button type="primary" :disabled="!planDraft.length" :loading="savingPlans" @click="savePlans">确认回款计划</el-button>
        </template>
      </template>
    </el-dialog>

    <!-- 登记回款 -->
    <el-dialog v-model="payOpen" title="手工登记回款" width="420px">
      <el-form label-width="80px">
        <el-form-item label="金额"><el-input-number v-model="pay.amount" :min="0.01" :precision="2" controls-position="right" style="width: 100%" /></el-form-item>
        <el-form-item label="到账日期"><el-date-picker v-model="pay.payment_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="pay.remark" placeholder="如：承兑汇票 / 现金" /></el-form-item>
      </el-form>
      <div class="jc-muted jc-small">有银行流水的回款请在「流水核对」中关联；此处用于无流水的回款。</div>
      <template #footer>
        <el-button @click="payOpen = false">取消</el-button>
        <el-button type="primary" :loading="paying" :disabled="!pay.amount || !pay.payment_date" @click="savePay">登记</el-button>
      </template>
    </el-dialog>

    <ContractForm v-model="editOpen" :contract="c" @saved="load" />

    <WecomApprovalDialog v-model="approvalOpen" kind="contract" title="合同审批" :defaults="approvalDefaults" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import ProjectReportSources from '@/components/ProjectReportSources.vue'
import RecordFeedbackButton from '@/components/records/RecordFeedbackButton.vue'
import { useAuth } from '@/store/auth'
import { datetime, filesize, money, num, percent, wanNum } from '@/utils/format'
import ContractForm from './contract-form.vue'
import WecomApprovalDialog from '@/components/WecomApprovalDialog.vue'

const auth = useAuth()

const VIA = { callback: '企微回调', reconcile: '兜底对账', mock: '模拟', manual: '人工重试' }
const PLAN_TONE = { 1: '', 2: 'warn', 3: 'info', 4: 'up', 5: 'down' }
const route = useRoute()
const c = ref(null)
const loading = ref(false)
const editOpen = ref(false)
const approvalOpen = ref(false)
const approvalDefaults = computed(() => ({
  customer: c.value?.customer_name || '',
  name: c.value?.name || c.value?.contract_no || '',
  amount: c.value?.amount || '',
  date: c.value?.sign_date || '',
  project: c.value?.project?.name || '',
  owner: c.value?.owner_name || '',
}))

async function load() {
  loading.value = true
  try { c.value = await api.get(`/contracts/${route.params.id}`) } finally { loading.value = false }
}
const overdueHint = computed(() => {
  const o = (c.value?.plans || []).filter((p) => p.status === 5)
  return o.length ? `逾期 ${o.length} 期 · ${money(o.reduce((s, p) => s + num(p.amount) - num(p.received_amount), 0), 0)}` : '无逾期'
})

// ---------- 归档 ----------
const archiveOpen = ref(false); const file = ref(null); const uploading = ref(false); const ub = ref()
const parsed = ref(null); const planDraft = ref([]); const savingPlans = ref(false)
const toDraft = (s) => ({ stage_name: s.stage_name, pct: s.ratio != null ? +(num(s.ratio) * 100).toFixed(2) : null,
                          amount: num(s.amount), due_date: s.due_date, condition_text: s.condition_text, due_basis: s.due_basis })
async function doArchive() {
  uploading.value = true
  try {
    const r = await api.upload(`/contracts/${route.params.id}/archive`, file.value)
    parsed.value = r
    planDraft.value = (r.parsed.stages.length ? r.parsed.stages : r.plans).map(toDraft)
    ElMessage.success(`已归档（v${r.file.version}）`)
    load()
  } finally { uploading.value = false }
}
function editPlans() {
  parsed.value = { parsed: { warnings: [], stages: [] }, linked: [], text_excerpt: '' }
  planDraft.value = c.value.plans.map(toDraft)
  archiveOpen.value = true
}
function resetArchive() { parsed.value = null; file.value = null; planDraft.value = []; ub.value?.reset?.() }
const ratioTotal = computed(() => +planDraft.value.reduce((s, r) => s + num(r.pct), 0).toFixed(2))
const amountTotal = computed(() => +planDraft.value.reduce((s, r) => s + num(r.amount), 0).toFixed(2))
function onPct(row) { row.amount = +(num(c.value.amount) * num(row.pct) / 100).toFixed(2) }
function addStage() { planDraft.value.push({ stage_name: '付款', pct: 0, amount: 0, due_date: dayjs().format('YYYY-MM-DD'), condition_text: '人工补充' }) }
async function savePlans() {
  savingPlans.value = true
  try {
    const stages = planDraft.value.map((r) => ({ stage_name: r.stage_name, ratio: r.pct != null ? r.pct / 100 : null,
                                                 amount: String(r.amount), due_date: r.due_date, condition_text: r.condition_text }))
    const r = await api.put(`/contracts/${route.params.id}/plans`, { stages })
    r.warnings.forEach((w) => ElMessage.warning(w))
    if (r.linked.length) ElMessage.success(`自动关联到账流水 ${r.linked.length} 笔`)
    ElMessage.success('回款计划已保存')
    archiveOpen.value = false
    load()
  } finally { savingPlans.value = false }
}

// ---------- 手工回款 ----------
const payOpen = ref(false); const paying = ref(false)
const pay = reactive({ amount: null, payment_date: dayjs().format('YYYY-MM-DD'), remark: '' })
async function savePay() {
  paying.value = true
  try {
    const r = await api.post(`/contracts/${route.params.id}/payments`, { ...pay, amount: String(pay.amount) })
    c.value = { ...r.detail, perms: c.value.perms }
    payOpen.value = false
    ElMessage.success('回款已登记')
  } finally { paying.value = false }
}

watch(() => route.params.id, (v) => v && load())
onMounted(load)
</script>

<style scoped>
.sub-t { margin: 14px 0 6px; font-size: 12px; color: var(--jc-ink-3); }
.raw { width: 100%; border-collapse: collapse; font-size: 13px; }
.raw th { width: 110px; text-align: left; font-weight: 400; color: var(--jc-ink-3); padding: 5px 8px; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); }
.raw td { padding: 5px 8px; border: 1px solid var(--jc-line-2); }
.pg { height: 5px; background: var(--jc-line-2); border-radius: 3px; overflow: hidden; margin-bottom: 3px; }
.pg i { display: block; height: 100%; background: var(--jc-brand); }
.pg i.done { background: var(--jc-up); }
.excerpt { white-space: pre-wrap; font-size: 12px; background: var(--jc-surface-2); padding: 10px; border-radius: 6px; max-height: 240px; overflow: auto; }
</style>
