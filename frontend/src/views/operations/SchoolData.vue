<template>
  <div>
    <OutreachGoals v-if="mode === 'outreach' && can('tasks')" :project="project" @saved="emit('saved')" @open-task="t => emit('open-task',t)" />
    <IncomeOverview v-if="mode === 'income'" :project="project" @open-school="id => selectedId=id" />
    <div class="jc-grid c3">
      <Kpi label="项目学校" :value="project.schools.length" unit="所" :hint="can('outreach') ? `高意向 ${project.schools.filter(s => s.potential === 'high' && !['lost', 'operating', 'won'].includes(s.stage)).length} 所` : ''" />
      <Kpi v-if="can('procurement') && (mode === 'directory' || mode === 'procurement')" label="已登记投入" :value="money(project.financials.total_investment)" unit="元" :hint="`建设清单 + 人工 + 其他 · ${project.financials.unpriced_count || 0} 条未标价`" />
      <Kpi v-if="can('income') && (mode === 'directory' || mode === 'income')" label="全历史累计登记收入" :value="money(project.financials.total_income)" unit="元" hint="学校收入及未分摊的项目公共收入" />
    </div>
    <div class="toolbar"><el-input v-model="search" placeholder="搜索学校" clearable style="width: 220px" /><el-select v-if="can('outreach')" v-model="stage" placeholder="全部阶段" clearable style="width: 150px"><el-option v-for="(v, k) in STAGES" :key="k" :value="k" :label="v" /></el-select><el-checkbox v-if="can('outreach')" v-model="highOnly">仅高意向</el-checkbox><el-button v-if="project.can_manage" type="primary" @click="editSchool()">新增学校</el-button></div>
    <div v-if="mode === 'income' && project.can_manage" class="jc-row mt-8" style="margin-bottom: 12px"><IncomeFile :project-id="project.id" @saved="emit('saved')" /></div>
    <div v-if="mode === 'outreach'" class="toolbar"><el-radio-group v-model="lifecycle" aria-label="学校阶段分组"><el-radio-button value="prospect">待开拓学校</el-radio-button><el-radio-button value="existing">存量学校</el-radio-button><el-radio-button value="all">全部学校</el-radio-button></el-radio-group></div>
    <p v-if="mode === 'income'" class="jc-small jc-muted">最近登记收款日：{{ project.financials.last_income_date || '尚无记录' }}。{{ project.financials.income_note }}</p>
    <template v-if="mode === 'outreach' && lifecycle === 'prospect'">
      <div v-if="schools.length" class="prospect-cards" aria-label="待开拓学校任务卡">
        <button v-for="school in schools" :key="school.id" type="button" class="prospect-card"
          :aria-label="`查看学校跟进：${school.name}`" @click="selectedId = school.id" @contextmenu.prevent="openMenu(school, null, $event)">
          <span class="jc-row between"><strong>{{ school.name }}</strong><StatusPill :text="STAGES[school.stage]" tone="brand" /></span>
          <span class="jc-muted jc-small">{{ school.region }} · {{ POTENTIAL[school.potential] }}意向</span>
          <span>{{ school.latest_visit?.content || '尚未登记实际跟进' }}</span>
          <span v-if="school.next_action" class="jc-small">下一步：{{ school.next_action }}</span>
          <span class="card-footer jc-small"><span>{{ marketTasks(school.id).length ? [...new Set(marketTasks(school.id).map(t => t.owner_name))].join('、') : '待安排市场任务' }}</span>
            <time v-if="school.next_contact_date" :class="{ 'jc-down': school.next_contact_date < project.today }">{{ school.next_contact_date }} 跟进</time></span>
        </button>
      </div>
      <el-empty v-else description="没有符合条件的待开拓学校" :image-size="60" />
    </template>
    <el-table v-else :data="schools" @row-click="s => selectedId = s.id" @row-contextmenu="openMenu">
      <el-table-column prop="name" label="学校" min-width="190" />
      <el-table-column v-if="mode !== 'procurement'" prop="region" label="区域" width="100" />
      <el-table-column v-if="mode === 'directory' || mode === 'outreach'" label="阶段 / 意向" width="150"><template #default="{ row }">{{ STAGES[row.stage] }}<div class="jc-small jc-muted">意向 · {{ POTENTIAL[row.potential] }}</div></template></el-table-column>
      <el-table-column v-if="mode === 'directory' || mode === 'outreach'" label="下一步 / 跟进日期" min-width="200"><template #default="{ row }">{{ row.next_action || '待补充' }}<div class="jc-small" :class="row.next_contact_date && row.next_contact_date < project.today && !['lost', 'operating', 'won'].includes(row.stage) ? 'jc-down' : 'jc-muted'">{{ row.next_contact_date || '' }}</div></template></el-table-column>
      <el-table-column v-if="can('procurement') && (mode === 'directory' || mode === 'procurement')" label="合计投入（元）" width="160" align="right"><template #default="{ row }">{{ money(row.total_investment) }}</template></el-table-column>
      <el-table-column v-if="can('income') && (mode === 'directory' || mode === 'income')" label="累计收入（元）" width="150" align="right"><template #default="{ row }">{{ money(row.total_income) }}</template></el-table-column>
      <el-table-column v-if="mode === 'outreach'" label="市场任务 / 负责人" min-width="180">
        <template #default="{ row }">
          <template v-if="marketTasks(row.id).length"><b>{{ marketTasks(row.id).length }} 项</b><div class="jc-muted jc-small">{{ [...new Set(marketTasks(row.id).map(t => t.owner_name))].join('、') }}</div></template>
          <span v-else class="jc-muted">待安排</span>
        </template>
      </el-table-column>
      <el-table-column v-if="mode === 'outreach'" label="最近实际跟进" min-width="200"><template #default="{ row }"><template v-if="row.latest_visit">{{ row.latest_visit.occurred_on }} · {{ row.latest_visit.author }}<p class="jc-small">{{ row.latest_visit.content }}</p></template><span v-else class="jc-muted">尚未登记</span></template></el-table-column>
      <el-table-column width="105"><template #default="{ row }"><el-button link type="primary" @click.stop="selectedId = row.id">{{ labels[mode] }}</el-button></template></el-table-column>
    </el-table>
    <p v-if="mode !== 'outreach'" class="jc-muted jc-small">{{ project.financials.note }}</p>
    <el-alert v-if="mode === 'procurement' && (project.financials.unpriced_count || project.financials.review_count)" :title="`${project.financials.unpriced_count || 0} 条未标价，${project.financials.review_count || 0} 条需核对金额或设备去向，请打开学校清单查看说明。`" type="warning" :closable="false" />
    <el-collapse v-if="mode === 'directory' && project.import_batches?.length" class="mt-16"><el-collapse-item title="已导入资料与核对说明" name="sources"><div v-for="b in project.import_batches" :key="b.id"><b>{{ b.source_name }}</b><p class="jc-small">{{ b.result.rows }} 条记录 · 已登记金额 {{ money(b.result.amount) }}</p><p v-for="(n, i) in b.result.notes" :key="i" class="jc-muted jc-small">{{ n }}</p></div></el-collapse-item></el-collapse>
    <Panel v-if="mode === 'income' && project.financials.daily.length" title="每日收入" class="mt-16"><Chart :option="incomeChart" height="240px" /></Panel>
    <Panel v-if="mode === 'income' && project.unassigned_incomes?.length" title="项目公共收入（未分摊学校）" :sub="`合计 ${money(project.financials.unassigned_income)}，已计入累计登记收入`" class="mt-16"><el-table :data="project.unassigned_incomes" max-height="300" @row-contextmenu="projectIncomeMenu"><el-table-column prop="income_date" label="收款日期" width="130" /><el-table-column label="金额（元）" width="130"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column><el-table-column prop="note" label="来源与说明" min-width="300" show-overflow-tooltip /></el-table></Panel>

    <el-drawer :model-value="!!selected" @update:model-value="v => { if (!v) selectedId = null }" :title="selected?.name || '学校资料'" size="min(900px, 100vw)" destroy-on-close>
      <template v-if="selected">
        <div class="jc-row between"><p><span v-if="can('procurement')">合计投入 <b>{{ money(selected.total_investment) }} 元</b> </span><span v-if="can('income')">· 累计收入 <b>{{ money(selected.total_income) }} 元</b></span></p><el-button v-if="project.can_manage" @click="editSchool(selected)">编辑学校资料</el-button></div>
        <p v-if="can('outreach')" class="jc-muted">{{ STAGES[selected.stage] }} · 下一步：{{ selected.next_action || '待补充' }}</p>
        <div v-if="project.can_manage && mode !== 'procurement'" class="jc-row"><span>学校区域</span><el-select :model-value="selected.region || '待定'" aria-label="学校区域" style="width:160px" @change="saveRegion"><el-option v-for="r in REGIONS" :key="r" :label="r" :value="r" /></el-select></div>
        <p v-if="selected.note" class="plain">{{ selected.note }}</p>
        <el-tabs v-model="schoolTab">
          <el-tab-pane v-if="can('outreach')" label="客户跟进过程" name="outreach">
            <SchoolFollowup :project="project" :school="selected" @saved="emit('saved'); loadHistory()" />
            <div class="jc-row between"><h4>{{ mode === 'outreach' ? '关联市场开拓任务' : '关联任务' }}</h4>
              <el-button v-if="mode === 'outreach' && can('tasks') && project.can_manage && project.status === 'active'" type="primary" link @click="assignSchool">布置市场开拓任务</el-button>
            </div>
            <div v-for="t in schoolTasks" :key="t.id" class="follow-task" @click="emit('open-task', t); selectedId = null">
              <div class="jc-row between"><b>{{ t.title }}</b><el-button link type="primary">查看过程</el-button></div>
              <p class="jc-small jc-muted">{{ t.owner_name }} · {{ TASK_STATUS[t.status] }} {{ t.progress }}% · 截止 {{ t.due_date }}</p>
              <p class="plain">{{ t.latest_feedback || '尚未反馈进展' }}</p>
            </div>
            <p v-if="!schoolTasks.length" class="jc-muted jc-small">布置任务时选择本学校，反馈记录会集中到这里。</p>
            <h4>学校跟进记录</h4>
            <el-timeline><el-timeline-item v-for="h in history" :key="h.id" :timestamp="`${datetime(h.at)} · ${h.author}`" placement="top">
              <b>{{ h.before ? '更新学校资料' : '建立学校资料' }} · {{ STAGES[h.after?.stage] }}</b>
              <p v-if="h.before && h.before.stage !== h.after?.stage" class="jc-small">{{ STAGES[h.before.stage] }} → {{ STAGES[h.after?.stage] }}</p>
              <p class="plain">下一步：{{ h.after?.next_action || '待补充' }}</p><p v-if="h.after?.next_contact_date" class="jc-small">跟进日期：{{ h.after.next_contact_date }}</p>
              <p v-if="h.after?.note" class="plain">{{ h.after.note }}</p>
            </el-timeline-item></el-timeline>
          </el-tab-pane>
          <el-tab-pane v-if="can('procurement')" label="设备建设清单" name="equipment">
            <div class="jc-row between section-head"><span class="jc-muted jc-small">设备 {{ money(selected.equipment_cost) }} 元 · 人工 {{ money(selected.labor_cost) }} 元 · 其他 {{ money(selected.other_cost) }} 元</span><el-button v-if="project.can_manage" type="primary" @click="editEquipment()">添加设备</el-button></div>
            <el-table :data="selected.equipment" @row-contextmenu="equipmentMenu">
              <el-table-column label="设备 / 品牌型号" min-width="210"><template #default="{ row }">{{ row.product_name }}<div class="jc-small jc-muted">{{ row.brand }} {{ row.model }}</div></template></el-table-column>
              <el-table-column prop="quantity" label="数量" width="80" />
              <el-table-column label="单价" width="130"><template #default="{ row }">{{ unitPrice(row.unit_price) }}</template></el-table-column>
              <el-table-column label="清单金额" width="140"><template #default="{ row }">{{ row.amount == null ? '未标价' : money(row.amount) }}<div v-if="row.recorded_amount != null" class="jc-small jc-muted">按原表金额</div></template></el-table-column>
              <el-table-column label="备注 / 待核对" min-width="250"><template #default="{ row }"><p v-if="row.review_note" class="jc-down plain">{{ row.review_note }}</p><p class="jc-small plain">{{ row.note }}</p></template></el-table-column>
              <el-table-column v-if="project.can_manage" width="80"><template #default="{ row }"><el-button link type="primary" @click="editEquipment(row)">编辑</el-button></template></el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane v-if="can('income')" label="每日收入" name="income">
            <div class="jc-row between section-head"><span class="jc-muted jc-small">每个学校每日一条合计；更正金额请编辑原记录。</span><el-button v-if="project.can_manage" type="primary" @click="editIncome()">登记收入</el-button></div>
            <el-table :data="selected.incomes" @row-contextmenu="incomeMenu"><el-table-column prop="income_date" label="收入日期" width="140" /><el-table-column label="金额（元）" width="160"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column><el-table-column prop="source" label="数据来源" min-width="140" /><el-table-column prop="note" label="备注" min-width="140" /><el-table-column v-if="project.can_manage" width="80"><template #default="{ row }"><el-button link type="primary" @click="editIncome(row)">编辑</el-button></template></el-table-column></el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="dialog" :title="dialogTitle" width="min(620px, 94vw)" destroy-on-close>
      <el-form ref="formRef" :model="form" label-position="top">
        <template v-if="kind === 'school'">
          <el-form-item label="学校名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写学校名称' }]"><el-input v-model="form.name" maxlength="128" /></el-form-item>
          <template v-if="can('outreach')">
            <div class="two"><el-form-item label="当前阶段"><el-select v-model="form.stage"><el-option v-for="(v, k) in STAGES" :key="k" :label="v" :value="k" /></el-select></el-form-item><el-form-item label="意向程度"><el-select v-model="form.potential"><el-option v-for="(v, k) in POTENTIAL" :key="k" :label="v" :value="k" /></el-select></el-form-item></div>
            <el-form-item label="下一步跟进"><el-input v-model="form.next_action" type="textarea" maxlength="6000" /></el-form-item>
            <el-form-item label="下次跟进日期"><el-date-picker v-model="form.next_contact_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
          </template>
          <div v-if="can('procurement')" class="two"><el-form-item label="人工投入（元）"><el-input-number v-model="form.labor_cost" :min="0" :precision="2" :max="999999999999.99" /></el-form-item><el-form-item label="其他投入（元）"><el-input-number v-model="form.other_cost" :min="0" :precision="2" :max="999999999999.99" /></el-form-item></div>
        </template>
        <template v-else-if="kind === 'equipment'">
          <el-form-item label="选择设备产品" prop="product_id" :rules="[{ required: true, message: '选择产品' }]"><el-select v-model="form.product_id" filterable placeholder="选择可用的设备产品" @change="chooseProduct"><el-option v-for="p in library" :key="p.id" :value="p.id" :label="[p.name, [p.brand, p.model].filter(Boolean).join(' '), p.source_project].filter(Boolean).join(' · ')" /></el-select></el-form-item>
          <el-alert v-if="!library.length" type="info" :closable="false" title="请先在设备产品库中新增产品" />
          <div class="two"><el-form-item label="数量"><el-input-number v-model="form.quantity" :min="1" :max="1000000" :precision="0" /></el-form-item><el-form-item label="实际采购单价（元，未知可留空）"><el-input-number v-model="form.unit_price" :min="0" :max="999999999999.99" :precision="6" /></el-form-item></div>
          <el-form-item label="原表清单金额（可选；填写后按此金额汇总）"><el-input-number v-model="form.recorded_amount" :min="0" :max="999999999999.99" :precision="2" /></el-form-item>
          <el-form-item label="核对说明"><el-input v-model="form.review_note" type="textarea" maxlength="6000" placeholder="单价与金额不符、折扣、共用、拆回或调拨等情况" /></el-form-item>
          <p>数量 × 单价：{{ form.unit_price == null ? '未标价' : money(form.quantity * form.unit_price) }}</p>
        </template>
        <template v-else>
          <div class="two"><el-form-item label="收入日期" prop="income_date" :rules="[{ required: true, message: '选择日期' }]"><el-date-picker v-model="form.income_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item><el-form-item label="当日合计收入（元）"><el-input-number v-model="form.amount" :min="0" :precision="2" :max="999999999999.99" /></el-form-item></div>
          <el-form-item label="数据来源" prop="source" :rules="[{ required: true, whitespace: true, message: '填写数据来源' }]"><el-input v-model="form.source" maxlength="128" placeholder="财务收款表" /></el-form-item>
        </template>
        <el-form-item label="备注"><el-input v-model="form.note" type="textarea" maxlength="6000" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>

  </div>
</template>
<script setup>
import { useAuth } from '@/store/auth'
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import IncomeFile from './IncomeFile.vue'
import SchoolFollowup from './SchoolFollowup.vue'
import OutreachGoals from './OutreachGoals.vue'
import IncomeOverview from './IncomeOverview.vue'
import { money, datetime } from '@/utils/format'
import { STAGES, POTENTIAL, TASK_STATUS, unitPrice, announceChange } from './shared'
const removalAuth = useAuth()

const props = defineProps({ project: Object, mode: { type: String, default: 'directory' } }), emit = defineEmits(['saved', 'open-task', 'assign-school'])
const labels = { directory: '查看档案', procurement: '查看采购', income: '查看收入', outreach: '查看跟进' }
const can = section => (props.project.visible_sections || []).includes(section)
const localMenu = (kind, title, name) => rowDeletion(row => (props.project.can_manage || removalAuth.hasDeleteGrant) &&
  recordRemoval(kind, row.id, name(row), () => emit('saved'), { title }))
const equipmentMenu = localMenu('operation-equipment', '删除设备登记', row => row.product_name)
const incomeMenu = localMenu('operation-income', '删除学校收入', row => row.income_date)
const projectIncomeMenu = localMenu('operation-project-income', '删除项目收入', row => row.income_date)
const REGIONS = ['市直','江城','阳春','阳东','阳西','海陵湾','其他','待定']
async function saveRegion(region) { try { await api.put(`/operations/${props.project.id}/schools/${selectedId.value}/region`, { region }); emit('saved'); announceChange(); ElMessage.success('学校区域已保存') } catch {} }
const openMenu = rowDeletion(row => props.project.can_manage && ['procurement', 'outreach', 'income'].some(can) && {
  name: row.name, title: '删除学校', menuLabel: '删除学校',
  endpoint: `/operations/${props.project.id}/schools/${row.id}`,
  description: `确认删除「${row.name}」及其设备、收入和跟进记录？相关任务将解除学校绑定，操作留痕。`,
  onDeleted: () => {
    if (selectedId.value === row.id) selectedId.value = null
    emit('saved')
  },
})
const search = ref(''), stage = ref(''), highOnly = ref(false), selectedId = ref(null)
const lifecycle = ref('prospect')
const initialTab = () => ({ procurement: 'equipment', income: 'income', outreach: 'outreach', directory: 'outreach' })[props.mode]
const schoolTab = ref(initialTab())
// 待开拓只列已判定阶段的学校；来源为采购清单、阶段未判定的学校不进入开拓视图（在「全部学校」仍可查）。
const schools = computed(() => props.project.schools.filter(s => s.name.includes(search.value) && (!can('outreach') || ((!stage.value || stage.value === s.stage) && (!highOnly.value || s.potential === 'high'))) && (props.mode !== 'outreach' || lifecycle.value === 'all' || (lifecycle.value === 'existing' ? ['won', 'operating'].includes(s.stage) : !['won', 'operating', 'lost', 'unknown'].includes(s.stage)))))
const selected = computed(() => props.project.schools.find(s => s.id === selectedId.value))
const marketTasks = id => props.project.tasks.filter(t => t.school_id === id && t.kind === 'outreach')
const schoolTasks = computed(() => props.mode === 'outreach' ? marketTasks(selectedId.value) : props.project.tasks.filter(t => t.school_id === selectedId.value))
function assignSchool() { const row = selected.value; selectedId.value = null; emit('assign-school', row) }
const history = ref([])
let historyVersion = 0
async function loadHistory() {
  const id = selectedId.value, version = ++historyVersion
  if (!id || !can('outreach')) { history.value = []; return }
  try { const r = await api.get(`/operations/${props.project.id}/schools/${id}/history`); if (version === historyVersion) history.value = r.items } catch {}
}
watch(selectedId, () => { schoolTab.value = initialTab(); loadHistory() })
const incomeChart = computed(() => ({ tooltip: { trigger: 'axis' }, grid: { left: 70, right: 25, top: 20, bottom: 35 }, xAxis: { type: 'category', data: props.project.financials.daily.map(d => d.date) }, yAxis: { type: 'value', name: '元' }, series: [{ type: 'bar', data: props.project.financials.daily.map(d => Number(d.amount)), itemStyle: { color: '#1B3275' }, barMaxWidth: 28 }] }))
const dialog = ref(false), kind = ref('school'), editId = ref(null), saving = ref(false), formRef = ref(), form = reactive({}), library = ref([])
const dialogTitle = computed(() => `${editId.value ? '编辑' : '新增'}${{ school: '学校资料', equipment: '学校设备', income: '每日收入' }[kind.value]}`)
function reset(k, row, values) { Object.keys(form).forEach(k => delete form[k]); Object.assign(form, values); kind.value = k; editId.value = row?.id || null; dialog.value = true }
function editSchool(s) { reset('school', s, { name: s?.name || '', stage: s?.stage || 'contact', potential: s?.potential || 'medium', next_action: s?.next_action || '', next_contact_date: s?.next_contact_date || null, labor_cost: Number(s?.labor_cost || 0), other_cost: Number(s?.other_cost || 0), note: s?.note || '' }) }
async function editEquipment(e) {
  try {
    const { items } = await api.get(`/operations/${props.project.id}/product-choices`, { section: 'procurement' })
    if (e && !items.some(p => p.id === e.product_id)) {
      items.push({ id: e.product_id, name: e.product_name, brand: e.brand, model: e.model })
    }
    library.value = items
    reset('equipment', e, { product_id: e?.product_id || null, quantity: e?.quantity || 1,
      unit_price: e?.unit_price == null ? null : Number(e.unit_price), recorded_amount: e?.recorded_amount == null ? null : Number(e.recorded_amount),
      review_note: e?.review_note || '', note: e?.note || '' })
  } catch {}
}
function chooseProduct(id) { const price = library.value.find(p => p.id === id)?.reference_price; form.unit_price = price == null ? null : Number(price) }
function editIncome(i) { reset('income', i, { income_date: i?.income_date || props.project.today, amount: Number(i?.amount || 0), source: i?.source || '财务收款表', note: i?.note || '' }) }
async function save() {
  if (!await formRef.value.validate().catch(() => false)) return
  saving.value = true
  try {
    const base = `/operations/${props.project.id}/schools` + (kind.value === 'school' ? '' : `/${selectedId.value}/${kind.value}`)
    if (editId.value) await api.put(`${base}/${editId.value}`, form); else await api.post(base, form)
    dialog.value = false; ElMessage.success('资料已保存'); emit('saved'); announceChange(); if (kind.value === 'school') loadHistory()
  } catch {} finally { saving.value = false }
}
</script>
<style scoped>
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin: 22px 0 16px; }.section-head { margin: 12px 0 20px; gap: 16px; flex-wrap: wrap; }.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }.plain { white-space: pre-wrap; line-height: 1.8; }
.follow-task { border: 1px solid var(--jc-line); padding: 12px 16px; margin: 12px 0; cursor: pointer; border-radius: var(--jc-radius); }
.prospect-cards { display: grid; grid-template-columns: repeat(auto-fill,minmax(min(100%,270px),1fr)); gap: 14px; }
.prospect-card { display: flex; flex-direction: column; gap: 10px; min-width: 0; padding: 16px; text-align: left; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: var(--jc-radius); color: var(--jc-ink); font: inherit; cursor: pointer; overflow-wrap: anywhere; }
.prospect-card:hover, .prospect-card:focus-visible { border-color: var(--jc-brand); }
.prospect-card strong { font-family: var(--jc-font-serif); font-size: 16px; }.card-footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px; border-top: 1px solid var(--jc-line); padding-top: 9px; margin-top: auto; color: var(--jc-ink-3); }
.ctx-mask { position: fixed; inset: 0; z-index: 3000; }
.ctx-menu { position: fixed; min-width: 160px; padding: 6px; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 8px; box-shadow: 0 12px 32px rgba(0, 0, 0, .16); }
.ctx-title { padding: 6px 10px 8px; font-size: 12px; color: var(--jc-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 220px; border-bottom: 1px solid var(--jc-line-2); margin-bottom: 4px; }
.ctx-item { display: block; width: 100%; padding: 8px 10px; border: 0; border-radius: 6px; background: none; color: var(--jc-ink); font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.ctx-item:hover { background: var(--jc-surface-2); }
.ctx-item.danger { color: var(--el-color-danger, #d9534f); }
@media(max-width: 500px) { .two { grid-template-columns: 1fr; gap: 0; } }
</style>
