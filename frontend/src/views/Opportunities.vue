<!-- 商机信息表（FR-05-02）：漏斗 + 阶段卡片、列表筛选、新增编辑、阶段推进与历史、赢单转合同 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="营销管控部 · 台账中心" title="商机信息"
      desc="企微商机工单审批通过后自动登记，支持人工补录与 Excel 清洗导入；阶段推进全程留痕。">
      <el-button @click="exportXlsx">导出 xlsx</el-button>
      <el-button v-if="auth.hasMenu('market') || auth.hasMenu('opportunities')" @click="openWecom">企业微信客户</el-button>
      <el-button v-if="auth.can('opp:write')" type="primary" @click="openEdit()">新增商机</el-button>
    </PageHeader>

    <div class="jc-grid c5">
      <Kpi label="在途商机" :value="funnel.open?.count ?? '—'" unit="个" :hint="'金额 ' + wan(funnel.open?.amount)" />
      <Kpi label="赢单金额" :value="wanNum(funnel.won?.amount)" unit="万" :hint="`${funnel.won?.count ?? 0} 个赢单`" />
      <Kpi label="赢单率" :value="funnel.win_rate == null ? '—' : (funnel.win_rate * 100).toFixed(1)" unit="%"
        :hint="`赢 ${funnel.won?.count ?? 0} / 丢 ${funnel.lost?.count ?? 0}`" :progress="funnel.win_rate" />
      <Kpi label="本周新增" :value="funnel.new_week?.count ?? '—'" unit="个" :hint="wan(funnel.new_week?.amount)" />
      <Kpi label="本月新增" :value="funnel.new_month?.count ?? '—'" unit="个" :hint="wan(funnel.new_month?.amount)" />
    </div>

    <div class="jc-grid c3 mt-16">
      <Panel title="商机漏斗" sub="线索 → 赢单（按金额）" :loading="loadingFunnel">
        <Chart :option="funnelOption" height="260px" :empty="!funnel.stages?.length" @click="(p) => pickStage(p.data?.key)" />
      </Panel>
      <Panel class="jc-span-2" title="阶段分布" sub="点击卡片按阶段筛选列表" :loading="loadingFunnel">
        <div class="stage-cards">
          <button v-for="s in funnel.stages || []" :key="s.stage" class="stage-card"
            :class="{ active: filters.stage === s.stage, closed: ['won', 'lost'].includes(s.stage) }" @click="pickStage(s.stage)">
            <span class="jc-muted jc-small">{{ s.name }}</span>
            <b class="jc-num">{{ s.count }}</b>
            <span class="jc-num jc-small">{{ wan(s.amount) }}</span>
          </button>
        </div>
      </Panel>
    </div>

    <Panel class="mt-16" :padding="false" title="商机列表" :sub="`共 ${list.total || 0} 条 · 合计 ${money(list.totals?.amount, 0)}`">
      <template #extra>
        <el-input v-model="filters.keyword" placeholder="名称 / 编号 / 审批单号" clearable style="width: 200px" @change="search" />
      </template>
      <div class="filters">
        <el-select v-model="filters.stage" placeholder="阶段" clearable style="width: 120px" @change="search">
          <el-option v-for="s in opts.stages" :key="s.key" :label="s.name" :value="s.key" />
        </el-select>
        <el-select v-model="filters.business_line" placeholder="业务线" clearable style="width: 140px" @change="search">
          <el-option v-for="(n, k) in BUSINESS_LINES" :key="k" :label="n" :value="k" />
        </el-select>
        <el-select v-model="filters.owner_id" placeholder="负责人" clearable filterable style="width: 120px" @change="search">
          <el-option v-for="u in opts.owners" :key="u.id" :label="u.name" :value="u.id" />
        </el-select>
        <el-select v-model="filters.source" placeholder="来源" clearable style="width: 170px" @change="search">
          <el-option v-for="s in opts.sources" :key="s.key" :label="s.name" :value="s.key" />
        </el-select>
        <el-input v-model="filters.customer" placeholder="客户" clearable style="width: 160px" @change="search" />
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="登记起"
          end-placeholder="登记止" style="width: 240px" @change="search" />
        <el-button link @click="reset">重置</el-button>
      </div>
      <el-table :data="list.items" v-loading="loading" :row-class-name="rowClass" @row-click="openDetail">
        <el-table-column prop="opp_no" label="编号" width="124" />
        <el-table-column label="商机名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }"><span class="link">{{ row.name }}</span></template>
        </el-table-column>
        <el-table-column prop="customer_name" label="客户" min-width="170" show-overflow-tooltip />
        <el-table-column label="业务线" width="110"><template #default="{ row }">{{ BUSINESS_LINES[row.business_line] || row.business_line }}</template></el-table-column>
        <el-table-column label="阶段" width="96">
          <template #default="{ row }"><StatusPill :text="row.stage_name" :tone="stageTone(row.stage)" /></template>
        </el-table-column>
        <el-table-column label="预计金额" width="130" align="right">
          <template #default="{ row }"><span class="jc-num">{{ money(row.amount, 0) }}</span></template>
        </el-table-column>
        <el-table-column label="预计签约" width="106"><template #default="{ row }">{{ date(row.expected_sign_date) }}</template></el-table-column>
        <el-table-column prop="owner_name" label="负责人" width="80" />
        <el-table-column label="来源" width="150">
          <template #default="{ row }">
            <StatusPill :text="row.source_name" :tone="row.source_type === 1 ? 'brand' : row.source_type === 3 ? 'info' : ''" dot />
          </template>
        </el-table-column>
        <el-table-column label="登记时间" width="110"><template #default="{ row }">{{ date(row.created_at) }}</template></el-table-column>
        <el-table-column v-if="auth.can('opp:write')" label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button v-if="!['won', 'lost'].includes(row.stage)" link type="primary" @click.stop="openAdvance(row)">推进</el-button>
            <el-button v-if="row.stage === 'won'" link type="primary" @click.stop="toContract(row)">转合同</el-button>
            <el-button link @click.stop="openEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="list.total || 0"
          :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" @change="load" />
      </div>
    </Panel>

    <!-- 详情 + 阶段历史 -->
    <el-drawer v-model="detailOpen" size="520px" :title="detail?.name">
      <template v-if="detail">
        <div class="jc-row wrap" style="gap: 8px">
          <StatusPill :text="detail.stage_name" :tone="stageTone(detail.stage)" />
          <StatusPill :text="detail.source_name" :tone="detail.source_type === 1 ? 'brand' : 'info'" dot />
          <span class="jc-muted jc-small">{{ detail.opp_no }}</span>
        </div>
        <dl class="kv mt-16">
          <dt>客户</dt><dd>{{ detail.customer_name }}</dd>
          <dt>业务线</dt><dd>{{ BUSINESS_LINES[detail.business_line] }}</dd>
          <dt>预计金额</dt><dd class="jc-num">{{ money(detail.amount) }}</dd>
          <dt>预计签约</dt><dd>{{ date(detail.expected_sign_date) }}</dd>
          <dt>负责人</dt><dd>{{ detail.owner_name || '—' }}</dd>
          <dt>来源审批</dt><dd>{{ detail.source_approval_no || '—' }}</dd>
          <dt>关联项目</dt><dd>{{ detail.project_code || '—' }}</dd>
          <dt v-if="detail.lost_reason">丢单原因</dt><dd v-if="detail.lost_reason" class="jc-down">{{ detail.lost_reason }}</dd>
          <dt>备注</dt><dd style="white-space: pre-line">{{ detail.remark || '—' }}</dd>
        </dl>
        <div v-if="auth.hasMenu('projects')" class="jc-row wrap mt-16">
          <el-button v-if="detail.project_code" @click="$router.push('/projects/' + detail.project_code)">查看已关联项目</el-button>
          <el-button v-else-if="auth.can('milestone:write') || auth.can('project:key')" @click="$router.push({ name: 'tender-projects', query: { opportunity: detail.id } })">登记为招标项目</el-button>
        </div>
        <div v-if="auth.can('opp:write')" class="jc-row wrap mt-16">
          <template v-if="!['won', 'lost'].includes(detail.stage)">
            <el-button v-if="detail.stage !== 'negotiation'" type="primary" @click="openAdvance(detail)">推进到下一阶段</el-button>
            <el-button @click="openAdvance(detail, 'won')">赢单</el-button>
            <el-button @click="openAdvance(detail, 'lost')">丢单</el-button>
          </template>
          <el-button v-else-if="detail.stage === 'won'" type="primary" @click="toContract(detail)">赢单转合同</el-button>
          <el-button @click="openEdit(detail)">编辑</el-button>
        </div>
        <h4 class="sec">阶段变更历史</h4>
        <el-timeline>
          <el-timeline-item v-for="(h, i) in detail.history" :key="i" :timestamp="datetime(h.at)"
            :color="h.stage === 'won' ? '#3b7a57' : h.stage === 'lost' ? '#a64b3c' : '#1B3275'">
            <div>{{ h.title }}</div>
            <div class="jc-muted jc-small">
              {{ h.operator || '—' }}<template v-if="h.note"> · {{ h.note }}</template>
              <template v-if="h.lost_reason"> · 原因：{{ h.lost_reason }}</template>
            </div>
          </el-timeline-item>
        </el-timeline>
      </template>
    </el-drawer>

    <!-- 新增 / 编辑 -->
    <el-drawer v-model="editOpen" size="460px" :title="form.id ? '编辑商机' : '新增商机'">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="96px">
        <el-form-item label="商机名称" prop="name"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="客户名称" prop="customer_name"><el-input v-model="form.customer_name" /></el-form-item>
        <el-form-item label="业务线" prop="business_line">
          <el-select v-model="form.business_line" style="width: 100%">
            <el-option v-for="(n, k) in BUSINESS_LINES" :key="k" :label="n" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="阶段">
          <el-select v-model="form.stage" style="width: 100%">
            <el-option v-for="s in opts.stages" :key="s.key" :label="s.name" :value="s.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="预计金额" prop="amount">
          <el-input-number v-model="form.amount" :min="0" :step="10000" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="预计签约"><el-date-picker v-model="form.expected_sign_date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item label="负责人">
          <el-select v-model="form.owner_id" filterable clearable style="width: 100%">
            <el-option v-for="u in opts.owners" :key="u.id" :label="u.name" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联项目"><el-input v-model="form.project_code" placeholder="JC-XY-2026-001" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-drawer>

    <!-- 阶段推进 -->
    <el-dialog v-model="advOpen" title="阶段推进" width="420px">
      <el-form label-width="80px">
        <el-form-item label="当前阶段">{{ advRow?.stage_name }}</el-form-item>
        <el-form-item label="推进到">
          <el-select v-model="adv.to_stage" style="width: 100%">
            <el-option v-for="s in nextStages" :key="s.key" :label="s.name" :value="s.key" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="adv.to_stage === 'lost'" label="丢单原因" required>
          <el-input v-model="adv.lost_reason" type="textarea" :rows="2" placeholder="如：价格、预算取消、竞品" />
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="adv.note" placeholder="选填" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="advOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doAdvance">确认</el-button>
      </template>
    </el-dialog>

    <!-- 赢单转合同 -->
    <el-dialog v-model="draftOpen" title="赢单转合同草稿" width="520px">
      <p class="jc-muted jc-small" style="margin-top: 0">以下信息由商机预填，确认后登记到合同台账（来源：人工录入，备注记录来源商机）。</p>
      <el-form v-if="draft" label-width="90px">
        <el-form-item label="合同名称"><el-input v-model="draft.name" /></el-form-item>
        <el-form-item label="客户名称"><el-input v-model="draft.customer_name" /></el-form-item>
        <el-form-item label="合同金额"><el-input-number v-model="draft.amount" :min="0" :controls="false" style="width: 100%" /></el-form-item>
        <el-form-item label="签订日期"><el-date-picker v-model="draft.sign_date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item label="业务线">
          <el-select v-model="draft.business_line" style="width: 100%">
            <el-option v-for="(n, k) in BUSINESS_LINES" :key="k" :label="n" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联项目"><el-input v-model="draft.project_code" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="draft.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="draftOpen = false">取消</el-button>
        <el-button v-if="auth.can('contracts:write')" type="primary" :loading="saving" @click="createContract">登记到合同台账</el-button>
        <el-button v-else type="primary" @click="goContracts">前往合同台账</el-button>
      </template>
    </el-dialog>

    <!-- 企业微信客户（只读）→ 登记商机 -->
    <el-drawer v-model="wecomOpen" title="企业微信客户" size="600px" @open="loadWecom">
      <div v-loading="wecom.loading">
        <el-alert v-if="wecom.message" type="warning" :closable="false" show-icon :title="wecom.message" />
        <template v-else>
          <div class="jc-row mb-12">
            <el-select v-model="wecom.userid" placeholder="选择成员" filterable style="width: 220px" @change="loadCustomers">
              <el-option v-for="u in wecom.members" :key="u" :label="u" :value="u" />
            </el-select>
            <span class="jc-small jc-muted">共 {{ wecom.items.length }} 位客户 · 只读，登记后进入商机台账</span>
          </div>
          <el-table :data="wecom.items" size="small" max-height="520" empty-text="该成员暂无可服务的客户">
            <el-table-column label="客户" min-width="180">
              <template #default="{ row }">
                <b>{{ row.name }}</b>
                <div v-if="row.corp_name" class="jc-small jc-muted">{{ row.corp_name }}</div>
                <div v-if="row.tags?.length" class="jc-small jc-muted">{{ row.tags.join('、') }}</div>
              </template>
            </el-table-column>
            <el-table-column label="备注" min-width="140">
              <template #default="{ row }">{{ row.remark || row.description || '—' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="110" align="right">
              <template #default="{ row }">
                <el-button v-if="auth.can('opp:write')" size="small" link type="primary" @click="toOpportunity(row)">
                  登记商机
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import dayjs from 'dayjs'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { BUSINESS_LINES, date, datetime, money, wan, wanNum } from '@/utils/format'
import { PALETTE } from '@/utils/chart'

const auth = useAuth()
const route = useRoute()
const router = useRouter()

const opts = ref({ stages: [], owners: [], sources: [] })
const funnel = ref({})
const list = ref({ items: [], total: 0 })
const loading = ref(false)
const loadingFunnel = ref(false)
const page = ref(1)
const pageSize = ref(20)
const range = ref(null)
const blank = () => ({ stage: '', business_line: '', owner_id: null, customer: '', source: null, keyword: '' })
const filters = reactive(blank())

function fromQuery() {
  const q = route.query
  Object.assign(filters, blank(), {
    stage: q.stage || '', business_line: q.business_line || '', customer: q.customer || '', keyword: q.keyword || '',
    owner_id: q.owner_id ? Number(q.owner_id) : null, source: q.source ? Number(q.source) : null,
  })
  range.value = q.start && q.end ? [q.start, q.end] : q.created_from ? [q.created_from, q.created_to || dayjs().format('YYYY-MM-DD')] : null
}

const params = () => {
  const p = { ...filters }
  if (range.value) [p.start, p.end] = range.value
  Object.keys(p).forEach((k) => (p[k] === '' || p[k] === null || p[k] === undefined) && delete p[k])
  return p
}

async function loadFunnel() {
  loadingFunnel.value = true
  try { funnel.value = await api.get('/opportunities/funnel', params()) } finally { loadingFunnel.value = false }
}
async function load() {
  loading.value = true
  try { list.value = await api.get('/opportunities', { ...params(), page: page.value, page_size: pageSize.value }) } finally { loading.value = false }
}
function search() {
  page.value = 1
  router.replace({ query: params() })
}
function reset() {
  Object.assign(filters, blank())
  range.value = null
  search()
}
function pickStage(s) {
  if (!s) return
  filters.stage = filters.stage === s ? '' : s
  search()
}
watch(() => route.query, () => { fromQuery(); load(); loadFunnel() })

const stageTone = (s) => ({ won: 'up', lost: 'down', negotiation: 'brand', quote: 'warn' })[s] || 'info'
const rowClass = ({ row }) => (row.stage !== 'won' && row.stage !== 'lost' && row.expected_sign_date && row.expected_sign_date < today() ? 'is-alert' : '')
const today = () => new Date().toISOString().slice(0, 10)

const funnelOption = computed(() => {
  const rows = (funnel.value.stages || []).filter((s) => s.stage !== 'lost')
  return {
    tooltip: { trigger: 'item', formatter: (p) => `${p.name}<br/>${p.data.count} 个 · ${wan(p.data.amount)}` },
    series: [{
      type: 'funnel', sort: 'none', left: 8, right: 8, top: 8, bottom: 8, gap: 2, minSize: '24%',
      label: { show: true, position: 'inside', color: '#fff', fontSize: 12, formatter: (p) => `${p.name} ${p.data.count}` },
      data: rows.map((s, i) => ({ name: s.name, key: s.stage, value: 100 - i * 14, count: s.count, amount: s.amount,
        itemStyle: { color: s.stage === 'won' ? PALETTE[2] : PALETTE[0], opacity: 1 - i * 0.1 } })),
    }],
  }
})

// ---------- 详情 ----------
const detailOpen = ref(false)
const detail = ref(null)
async function openDetail(row) {
  detail.value = await api.get(`/opportunities/${row.id}`)
  detailOpen.value = true
}

// ---------- 编辑 ----------
const editOpen = ref(false)
const saving = ref(false)
const formRef = ref()
const form = ref({})
const rules = {
  name: [{ required: true, message: '请填写商机名称' }], customer_name: [{ required: true, message: '请填写客户' }],
  business_line: [{ required: true, message: '请选择业务线' }], amount: [{ required: true, message: '请填写金额' }],
}
function openEdit(row) {
  form.value = row
    ? { id: row.id, name: row.name, customer_name: row.customer_name, business_line: row.business_line, stage: row.stage,
        amount: Number(row.amount), expected_sign_date: row.expected_sign_date, owner_id: row.owner_id,
        project_code: row.project_code, remark: row.remark }
    : { stage: 'lead', business_line: 'XY', amount: null, owner_id: auth.profile?.id }
  editOpen.value = true
}
// ---------- 企业微信客户 ----------
const wecomOpen = ref(false)
const wecom = reactive({ configured: false, message: '', members: [], userid: '', items: [], loading: false })

async function openWecom() {
  wecomOpen.value = true
}

async function loadWecom() {
  wecom.loading = true
  try {
    const data = await api.get('/wecom-external/overview')
    wecom.members = data.members || []
    wecom.userid = data.default || wecom.members[0] || ''
    wecom.message = data.configured ? '' : data.message
    if (wecom.userid) await loadCustomers()
  } finally {
    wecom.loading = false
  }
}

async function loadCustomers() {
  if (!wecom.userid) return
  wecom.loading = true
  try {
    const data = await api.get('/wecom-external/customers', { userid: wecom.userid })
    wecom.items = data.items || []
  } finally {
    wecom.loading = false
  }
}

function toOpportunity(row) {
  wecomOpen.value = false
  openEdit()
  form.value.name = `${row.name} 商机`
  form.value.customer_name = row.name
  form.value.remark = `企业微信客户 ${row.external_userid}${row.remark ? ` · ${row.remark}` : ''}`
}

async function save() {  await formRef.value.validate()
  saving.value = true
  try {
    const { id, ...body } = form.value
    const r = id ? await api.put(`/opportunities/${id}`, body) : await api.post('/opportunities', body)
    ElMessage.success('已保存')
    editOpen.value = false
    if (detailOpen.value) detail.value = r
    load(); loadFunnel()
  } finally { saving.value = false }
}

// ---------- 推进 ----------
const advOpen = ref(false)
const advRow = ref(null)
const adv = reactive({ to_stage: '', note: '', lost_reason: '' })
const nextStages = computed(() => {
  const keys = opts.value.stages.map((s) => s.key)
  const i = keys.indexOf(advRow.value?.stage)
  return opts.value.stages.filter((s, j) => j > i || ['won', 'lost'].includes(s.key)).filter((s) => s.key !== advRow.value?.stage)
})
function openAdvance(row, to) {
  advRow.value = row
  const keys = opts.value.stages.map((s) => s.key)
  const nxt = keys[keys.indexOf(row.stage) + 1]
  Object.assign(adv, { to_stage: to || (row.stage === 'negotiation' ? 'won' : nxt), note: '', lost_reason: '' })
  advOpen.value = true
}
async function doAdvance() {
  if (adv.to_stage === 'lost' && !adv.lost_reason.trim()) return ElMessage.warning('请填写丢单原因')
  saving.value = true
  try {
    const r = await api.post(`/opportunities/${advRow.value.id}/advance`, { ...adv })
    ElMessage.success(`已推进到「${r.stage_name}」`)
    advOpen.value = false
    if (detailOpen.value) detail.value = r
    load(); loadFunnel()
  } finally { saving.value = false }
}

// ---------- 赢单转合同 ----------
const draftOpen = ref(false)
const draft = ref(null)
async function toContract(row) {
  const d = await api.post(`/opportunities/${row.id}/contract-draft`)
  draft.value = { ...d, amount: Number(d.amount) }
  draftOpen.value = true
}
async function createContract() {
  saving.value = true
  try {
    const d = draft.value
    const c = await api.post('/contracts', {
      name: d.name, customer_name: d.customer_name, amount: d.amount, sign_date: d.sign_date,
      business_line: d.business_line, project_code: d.project_code || null, owner_id: d.owner_id, remark: d.remark,
    })
    ElMessage.success(`已登记合同 ${c.contract_no}`)
    draftOpen.value = false
    router.push(`/contracts/${c.id}`)
  } finally { saving.value = false }
}
function goContracts() {
  const d = draft.value
  router.push({ path: '/contracts', query: { action: 'new', customer_name: d.customer_name, amount: d.amount,
    business_line: d.business_line, name: d.name, source_opp_no: d.source_opp_no } })
}

async function exportXlsx() {
  await api.download('/opportunities/export', params())
}

onMounted(async () => {
  fromQuery()
  opts.value = await api.get('/opportunities/options')
  load()
  loadFunnel()
})
</script>

<style scoped>
.stage-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.stage-card {
  display: flex; flex-direction: column; gap: 4px; align-items: flex-start; text-align: left; cursor: pointer;
  padding: 12px 14px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface);
  transition: border-color 0.15s; font: inherit; color: var(--jc-ink);
}
.stage-card b { font-size: 22px; font-weight: 600; }
.stage-card:hover { border-color: var(--jc-brand-100); }
.stage-card.active { border-color: var(--jc-brand); background: var(--jc-brand-50); }
.stage-card.closed { background: var(--jc-surface-2); }
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px 18px; border-bottom: 1px solid var(--jc-line-2); }
.pager { display: flex; justify-content: flex-end; padding: 12px 18px; }
.link { color: var(--jc-brand); cursor: pointer; }
.kv { display: grid; grid-template-columns: 84px 1fr; gap: 8px 12px; margin: 0; font-size: 13px; }
.kv dt { color: var(--jc-ink-3); }
.kv dd { margin: 0; }
.sec { margin: 24px 0 12px; font-size: 14px; font-weight: 600; }
.mt-16 { margin-top: 16px; }
</style>
