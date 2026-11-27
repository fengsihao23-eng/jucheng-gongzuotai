<template>
  <div class="jc-page">
    <PageHeader eyebrow="项目管理" :title="pageTitle"
      :desc="scopeDesc">
      <el-button v-if="hasLedgerMenu && (auth.can('project:weekly') || auth.hasMenu('market'))" @click="exportWeekly">导出周进度</el-button>
      <el-button v-if="canCreate && projectMode !== 'history'" type="primary" @click="openCreate()">新建项目</el-button>
    </PageHeader>

    <div class="jc-grid c4 mb-16">
      <Kpi label="项目数" :value="items.length" unit="个" :hint="`在建 ${countBy(2)} · 已验收 ${countBy(3)}`" />
      <Kpi label="重点项目" :value="items.filter((x) => x.is_key).length" unit="个" hint="市场重点跟进" />
      <Kpi label="延期节点" :value="totalDelayed" unit="个" :hint="`${items.filter((x) => x.delayed_nodes).length} 个项目存在延期`" />
      <Kpi label="平均进度" :value="avgProgress" unit="%" hint="已完成节点 / 节点总数" />
    </div>

    <Panel :padding="false" :loading="loading">
      <template #extra>
        <el-input v-model="q.keyword" placeholder="项目名称 / 编码 / 客户" clearable style="width: 220px" @change="load" />
        <el-select v-model="q.status" placeholder="状态" clearable style="width: 110px" @change="load">
          <el-option v-for="(v, k) in listStatuses" :key="k" :label="v" :value="Number(k)" />
        </el-select>
        <el-select v-model="q.business_line" placeholder="业务线" clearable style="width: 140px" @change="load">
          <el-option v-for="(v, k) in BUSINESS_LINES" :key="k" :label="v" :value="k" />
        </el-select>
        <el-checkbox v-if="projectMode !== 'general'" v-model="q.is_key" @change="load">仅重点</el-checkbox>
        <el-checkbox v-model="q.mine" @change="load">我负责的</el-checkbox>
        <el-checkbox v-model="q.delayed" @change="load">仅延期</el-checkbox>
        <el-segmented v-model="view" :options="[{ label: '卡片', value: 'card' }, { label: '表格', value: 'table' }]" />
      </template>

      <div v-if="view === 'card'" class="cards">
        <div v-for="p in items" :key="p.project_code" v-record-delete="projectRemoval(p)" class="pcard" :class="{ alert: p.delayed_nodes }" @click="go(p)">
          <div class="jc-row between">
            <span class="jc-mono jc-muted">{{ p.project_code }}</span>
            <span class="jc-row" style="gap: 6px">
              <StatusPill v-if="p.is_key" text="重点" tone="brand" />
              <StatusPill :text="p.status_text" :tone="statusTone(p.status)" />
            </span>
          </div>
          <h4 class="jc-serif">{{ p.name }}</h4>
          <div class="jc-small jc-muted">{{ p.customer_name }} · {{ BUSINESS_LINES[p.business_line] }}</div>
          <div class="prog">
            <div class="bar"><i :style="{ width: p.progress * 100 + '%' }" /></div>
            <span class="jc-num jc-small">{{ p.done_nodes }}/{{ p.total_nodes }}</span>
          </div>
          <div class="jc-row between jc-small">
            <span>当前：<b :class="{ 'jc-down': p.current_node_delayed }">{{ p.current_node || '—' }}</b></span>
            <span v-if="p.delayed_nodes" class="jc-down">延期 {{ p.delayed_nodes }}</span>
            <span v-else class="jc-muted">无延期</span>
          </div>
          <div v-if="projectMode === 'pipeline'" class="jc-small jc-muted mt-8">
            招标截止：{{ p.bid_deadline || '待填写' }}
            <template v-if="p.opportunities?.length"> · 来源商机 {{ p.opportunities.map(o => o.no).join('、') }}</template>
          </div>
          <div class="jc-divider" />
          <div class="jc-row between jc-small jc-muted">
            <span>项目经理 {{ p.owner_name || '—' }}</span>
            <span v-if="hasLedgerMenu">回款 {{ percent(p.payment_rate) }}</span>
          </div>
        </div>
        <el-empty v-if="!items.length && !loading" description="暂无项目" style="grid-column: 1 / -1" />
      </div>

      <el-table v-else :data="items" :row-class-name="({ row }) => (row.delayed_nodes ? 'is-alert' : '')" @row-click="go" @row-contextmenu="projectMenu">
        <el-table-column prop="project_code" label="编码" width="150"><template #default="{ row }"><span class="jc-mono">{{ row.project_code }}</span></template></el-table-column>
        <el-table-column label="项目名称" min-width="220">
          <template #default="{ row }">{{ row.name }} <StatusPill v-if="row.is_key" text="重点" tone="brand" /></template>
        </el-table-column>
        <el-table-column label="业务线" width="110"><template #default="{ row }">{{ BUSINESS_LINES[row.business_line] }}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }"><StatusPill :text="row.status_text" :tone="statusTone(row.status)" /></template></el-table-column>
        <el-table-column prop="owner_name" label="项目经理" width="90" />
        <el-table-column v-if="projectMode === 'pipeline'" label="招标截止" prop="bid_deadline" width="113" />
        <el-table-column label="进度" width="150">
          <template #default="{ row }">
            <div class="prog"><div class="bar"><i :style="{ width: row.progress * 100 + '%' }" /></div><span class="jc-num jc-small">{{ row.done_nodes }}/{{ row.total_nodes }}</span></div>
          </template>
        </el-table-column>
        <el-table-column label="当前节点" width="110"><template #default="{ row }"><span :class="{ 'jc-down': row.current_node_delayed }">{{ row.current_node || '—' }}</span></template></el-table-column>
        <el-table-column label="延期" width="70" align="center"><template #default="{ row }"><b v-if="row.delayed_nodes" class="jc-down jc-num">{{ row.delayed_nodes }}</b><span v-else class="jc-muted">0</span></template></el-table-column>
        <el-table-column v-if="hasLedgerMenu" label="合同额" width="110" align="right"><template #default="{ row }"><span class="jc-num">{{ wan(row.contract_total) }}</span></template></el-table-column>
        <el-table-column v-if="hasLedgerMenu" label="回款率" width="80" align="right"><template #default="{ row }"><span class="jc-num">{{ percent(row.payment_rate) }}</span></template></el-table-column>
      </el-table>
    </Panel>

    <el-dialog v-model="dlg" title="新建项目" width="600px">
      <el-form :model="form" label-width="92px">
        <el-form-item label="项目名称" required><el-input v-model="form.name" /></el-form-item>
        <el-form-item v-if="linkedOpportunity" label="来源商机"><span>{{ linkedOpportunity.opp_no }} · {{ linkedOpportunity.name }}</span></el-form-item>
        <el-form-item label="客户" required><el-input v-model="form.customer_name" :disabled="!!linkedOpportunity" /></el-form-item>
        <el-form-item label="业务线" required>
          <el-radio-group v-model="form.business_line" :disabled="!!linkedOpportunity" @change="(v) => (form.milestone_template = v === 'KP' ? 'software' : 'standard')">
            <el-radio-button v-for="(v, k) in BUSINESS_LINES" :key="k" :value="k">{{ v }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <div class="jc-grid c2" style="gap: 0 16px">
          <el-form-item label="项目经理">
            <el-select v-model="form.owner_id" filterable clearable placeholder="选择"><el-option v-for="u in xmUsers" :key="u.id" :label="u.name" :value="u.id" /></el-select>
          </el-form-item>
          <el-form-item label="市场负责人">
            <el-select v-model="form.market_owner_id" filterable clearable placeholder="选择"><el-option v-for="u in scUsers" :key="u.id" :label="u.name" :value="u.id" /></el-select>
          </el-form-item>
          <el-form-item v-if="projectMode === 'pipeline' || form.status === 1" label="招标截止"><el-date-picker v-model="form.bid_deadline" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
          <el-form-item label="开工日期"><el-date-picker v-model="form.start_date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
          <el-form-item label="预算(元)"><el-input-number v-model="form.budget_amount" :min="0" :step="10000" controls-position="right" style="width: 100%" /></el-form-item>
          <el-form-item label="状态">
            <el-select v-model="form.status"><el-option v-for="(v, k) in PROJECT_STATUS" :key="k" :label="v" :value="Number(k)" /></el-select>
          </el-form-item>
          <el-form-item label="重点项目"><el-switch v-model="form.is_key" :disabled="!auth.can('project:key')" /></el-form-item>
        </div>
        <el-form-item label="流程模板">
          <el-select v-model="form.milestone_template" style="width: 100%">
            <el-option v-for="t in opts.templates" :key="t.key" :label="tplLabel(t)" :value="t.key" />
          </el-select>
        </el-form-item>
        <div v-if="curTpl" class="tpl jc-small">
          <div v-for="(n, i) in curTpl.nodes" :key="i" class="jc-row between">
            <span><b>{{ i + 1 }}. {{ n.node }}</b> <span class="jc-muted">{{ n.duty }}</span></span>
            <span class="jc-num jc-muted">{{ n.days }} 天</span>
          </div>
          <div class="jc-muted mt-8">计划日按开工日期 + 各节点工期依次推算，创建后可在项目详情中增删、排序、修改。</div>
        </div>
        <el-form-item label="备注" class="mt-16"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { BUSINESS_LINES, PROJECT_STATUS, percent, wan } from '@/utils/format'

const auth = useAuth()
const hasLedgerMenu = computed(() => auth.hasMenu('projects'))
const projectRemoval = row => {
  if (!hasLedgerMenu.value) return
  const admin = auth.profile?.is_admin
  const allowed = row.is_key ? admin || auth.canDelete('project:key')
    : admin || auth.canDelete('milestone:write') || row.owner_id === auth.profile?.id
  return allowed && recordRemoval('project', row.project_code, row.name, load, { title: '删除项目' })
}
const projectMenu = rowDeletion(projectRemoval)
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const items = ref([])
const view = ref(localStorage.getItem('jc_projects_view') || 'card')
const canCreate = ref(false)
const scope = ref('all')
const projectMode = computed(() => route.meta.projectMode || 'all')
const pageTitle = computed(() => ({ all: '项目台账', pipeline: '招标与在建', general: '一般项目', history: '历史项目' })[projectMode.value])
const listStatuses = computed(() => Object.fromEntries(Object.entries(PROJECT_STATUS).filter(([k]) =>
  projectMode.value === 'pipeline' ? [1, 2].includes(Number(k)) : projectMode.value === 'history' ? Number(k) === 5 : projectMode.value !== 'general' || Number(k) !== 5)))
const descriptions = {
  pipeline: '查看招标中和在建项目。可由已有商机转入，也可直接登记项目。',
  general: '查看一般项目，打开项目可安排任务、更新进展并查看流程节点。',
  history: '查看已经结项的项目，保留历史流程、合同和费用记录。',
  all: '查看项目及其生命周期节点、责任人与进度。',
}
const q = reactive({ keyword: '', status: route.query.status ? Number(route.query.status) : null, business_line: route.query.business_line || null,
  is_key: route.query.is_key === '1', mine: route.query.mine === '1', delayed: route.query.delayed === '1' })

const scopeHints = { self: ' 仅显示您负责或担任节点责任人的项目。', team: ' 仅显示你参与的项目。' }
const scopeDesc = computed(() => descriptions[projectMode.value] + (scopeHints[scope.value] || ''))
const countBy = (s) => items.value.filter((x) => x.status === s).length
const totalDelayed = computed(() => items.value.reduce((a, x) => a + x.delayed_nodes, 0))
const avgProgress = computed(() => {
  const xs = items.value.filter((x) => x.total_nodes)
  return xs.length ? ((xs.reduce((a, x) => a + x.progress, 0) / xs.length) * 100).toFixed(0) : '—'
})
const statusTone = (s) => ({ 1: 'info', 2: 'brand', 3: 'up', 4: '', 5: '' })[s]

async function load() {
  loading.value = true
  try {
    const r = await api.get('/projects', {
      keyword: q.keyword || undefined, status: q.status || undefined, business_line: q.business_line || undefined,
      is_key: q.is_key || undefined, mine: q.mine || undefined, scope: projectMode.value,
    })
    items.value = q.delayed ? r.items.filter((x) => x.delayed_nodes) : r.items
    canCreate.value = r.can_create
    scope.value = r.data_scope
  } finally {
    loading.value = false
  }
}
watch(view, (v) => localStorage.setItem('jc_projects_view', v))
watch(projectMode, () => { q.status = null; q.is_key = false; load() })
const go = (p) => router.push({ name: 'project-detail', params: { code: p.project_code }, query: { back: route.fullPath } })

const exportWeekly = () => api.download('/projects/weekly/export')

// 新建
const dlg = ref(false)
const saving = ref(false)
const opts = reactive({ users: [], templates: [] })
const form = reactive({}), linkedOpportunity = ref(null)
const xmUsers = computed(() => opts.users.filter((u) => u.dept_code === 'XM'))
const scUsers = computed(() => opts.users.filter((u) => u.dept_code === 'SC'))
const curTpl = computed(() => opts.templates.find((t) => t.key === form.milestone_template))
const tplLabel = (t) => ({ standard: '系统集成标准流程', software: '平台软件交付流程' })[t.key] || t.key
async function openCreate(source = null) {
  if (!opts.templates.length) Object.assign(opts, await api.get('/projects/options'))
  linkedOpportunity.value = source
  Object.assign(form, {
    name: source?.name || '', customer_name: source?.customer_name || '', business_line: source?.business_line || 'XY',
    owner_id: null, market_owner_id: source?.owner_id || null, status: projectMode.value === 'pipeline' ? 1 : 2,
    is_key: false, budget_amount: source?.amount ? Number(source.amount) : null, bid_deadline: null,
    opportunity_id: source?.id || null, start_date: null, milestone_template: source?.business_line === 'KP' ? 'software' : 'standard', remark: '',
  })
  dlg.value = true
}
async function create() {
  if (!form.name || !form.customer_name) return ElMessage.warning('请填写项目名称与客户')
  saving.value = true
  try {
    const r = await api.post('/projects', form)
    ElMessage.success(`已创建 ${r.project_code}`)
    dlg.value = false
    go(r)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await load()
  if (canCreate.value && projectMode.value === 'pipeline' && /^[1-9][0-9]*$/.test(String(route.query.opportunity))) {
    try {
      const source = await api.get('/opportunities/' + route.query.opportunity)
      if (source.project_code) await router.push('/projects/' + source.project_code)
      else await openCreate(source)
    } catch {}
  }
})
</script>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 14px; padding: 16px; }
.pcard { border: 1px solid var(--jc-line); border-radius: var(--jc-radius); padding: 14px 16px; background: var(--jc-surface); cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s; }
.pcard:hover { border-color: var(--jc-brand-100); box-shadow: var(--jc-shadow-pop); }
.pcard.alert { border-left: 3px solid var(--jc-down); }
.pcard h4 { margin: 8px 0 4px; font-size: 15px; line-height: 1.4; }
.prog { display: flex; align-items: center; gap: 8px; margin: 12px 0 8px; }
.prog .bar { flex: 1; height: 4px; background: var(--jc-line-2); border-radius: 2px; overflow: hidden; }
.prog .bar i { display: block; height: 100%; background: var(--jc-brand); border-radius: 2px; }
.tpl { background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); padding: 10px 12px; margin-left: 92px; display: flex; flex-direction: column; gap: 6px; }
</style>
