<template>
  <div>
    <div class="jc-grid c3">
      <Kpi label="项目学校" :value="project.schools.length" unit="所" :hint="`高意向 ${project.schools.filter(s => s.potential === 'high' && !['lost', 'operating', 'won'].includes(s.stage)).length} 所`" />
      <Kpi label="合计投入" :value="money(project.financials.total_investment)" unit="元" hint="设备 + 人工 + 其他投入" />
      <Kpi label="累计登记收入" :value="money(project.financials.total_income)" unit="元" hint="按学校、日期登记的收入合计" />
    </div>
    <div class="toolbar"><el-input v-model="search" placeholder="搜索学校" clearable style="width: 220px" /><el-select v-model="stage" placeholder="全部阶段" clearable style="width: 150px"><el-option v-for="(v, k) in STAGES" :key="k" :value="k" :label="v" /></el-select><el-checkbox v-model="highOnly">仅高意向</el-checkbox><el-button v-if="project.can_manage" type="primary" @click="editSchool()">新增学校</el-button></div>
    <el-table :data="schools" @row-click="s => selectedId = s.id">
      <el-table-column prop="name" label="学校" min-width="190" />
      <el-table-column label="阶段 / 意向" width="150"><template #default="{ row }">{{ STAGES[row.stage] }}<div class="jc-small jc-muted">意向 · {{ POTENTIAL[row.potential] }}</div></template></el-table-column>
      <el-table-column label="下一步 / 跟进日期" min-width="200"><template #default="{ row }">{{ row.next_action || '待补充' }}<div class="jc-small" :class="row.next_contact_date && row.next_contact_date < project.today && !['lost', 'operating', 'won'].includes(row.stage) ? 'jc-down' : 'jc-muted'">{{ row.next_contact_date || '' }}</div></template></el-table-column>
      <el-table-column label="合计投入（元）" width="160" align="right"><template #default="{ row }">{{ money(row.total_investment) }}</template></el-table-column>
      <el-table-column label="收入（元）" width="150" align="right"><template #default="{ row }">{{ money(row.total_income) }}</template></el-table-column>
      <el-table-column width="100"><template #default="{ row }"><el-button link type="primary" @click.stop="selectedId = row.id">查看清单</el-button></template></el-table-column>
    </el-table>
    <p class="jc-muted jc-small">{{ project.financials.note }}</p>
    <Panel v-if="project.financials.daily.length" title="每日收入" class="mt-16"><Chart :option="incomeChart" height="240px" /></Panel>

    <el-drawer :model-value="!!selected" @update:model-value="v => { if (!v) selectedId = null }" :title="selected?.name || '学校资料'" size="min(900px, 100vw)" destroy-on-close>
      <template v-if="selected">
        <div class="jc-row between"><p>合计投入 <b>{{ money(selected.total_investment) }} 元</b> · 累计收入 <b>{{ money(selected.total_income) }} 元</b></p><el-button v-if="project.can_manage" @click="editSchool(selected)">编辑学校资料</el-button></div>
        <p class="jc-muted">{{ STAGES[selected.stage] }} · 下一步：{{ selected.next_action || '待补充' }}</p>
        <p v-if="selected.note" class="plain">{{ selected.note }}</p>
        <el-tabs>
          <el-tab-pane label="客户跟进过程">
            <h4>关联重点任务</h4>
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
          <el-tab-pane label="设备建设清单">
            <div class="jc-row between section-head"><span class="jc-muted jc-small">设备 {{ money(selected.equipment_cost) }} 元 · 人工 {{ money(selected.labor_cost) }} 元 · 其他 {{ money(selected.other_cost) }} 元</span><el-button v-if="project.can_manage" type="primary" @click="editEquipment()">添加设备</el-button></div>
            <el-table :data="selected.equipment">
              <el-table-column label="设备 / 品牌型号" min-width="210"><template #default="{ row }">{{ row.product_name }}<div class="jc-small jc-muted">{{ row.brand }} {{ row.model }}</div></template></el-table-column>
              <el-table-column prop="quantity" label="数量" width="80" />
              <el-table-column label="采购单价" width="130"><template #default="{ row }">{{ money(row.unit_price) }}</template></el-table-column>
              <el-table-column label="金额" width="140"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
              <el-table-column v-if="project.can_manage" width="80"><template #default="{ row }"><el-button link type="primary" @click="editEquipment(row)">编辑</el-button></template></el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="每日收入">
            <div class="jc-row between section-head"><span class="jc-muted jc-small">每个学校每日一条合计；更正金额请编辑原记录。</span><el-button v-if="project.can_manage" type="primary" @click="editIncome()">登记收入</el-button></div>
            <el-table :data="selected.incomes"><el-table-column prop="income_date" label="收入日期" width="140" /><el-table-column label="金额（元）" width="160"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column><el-table-column prop="source" label="数据来源" min-width="140" /><el-table-column prop="note" label="备注" min-width="140" /><el-table-column v-if="project.can_manage" width="80"><template #default="{ row }"><el-button link type="primary" @click="editIncome(row)">编辑</el-button></template></el-table-column></el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="dialog" :title="dialogTitle" width="min(620px, 94vw)" destroy-on-close>
      <el-form ref="formRef" :model="form" label-position="top">
        <template v-if="kind === 'school'">
          <el-form-item label="学校名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写学校名称' }]"><el-input v-model="form.name" maxlength="128" /></el-form-item>
          <div class="two"><el-form-item label="当前阶段"><el-select v-model="form.stage"><el-option v-for="(v, k) in STAGES" :key="k" :label="v" :value="k" /></el-select></el-form-item><el-form-item label="意向程度"><el-select v-model="form.potential"><el-option v-for="(v, k) in POTENTIAL" :key="k" :label="v" :value="k" /></el-select></el-form-item></div>
          <el-form-item label="下一步跟进"><el-input v-model="form.next_action" type="textarea" maxlength="6000" /></el-form-item>
          <el-form-item label="下次跟进日期"><el-date-picker v-model="form.next_contact_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
          <div class="two"><el-form-item label="人工投入（元）"><el-input-number v-model="form.labor_cost" :min="0" :precision="2" :max="999999999999.99" /></el-form-item><el-form-item label="其他投入（元）"><el-input-number v-model="form.other_cost" :min="0" :precision="2" :max="999999999999.99" /></el-form-item></div>
        </template>
        <template v-else-if="kind === 'equipment'">
          <el-form-item label="选择设备产品" prop="product_id" :rules="[{ required: true, message: '选择产品' }]"><el-select v-model="form.product_id" filterable placeholder="可使用有权限项目中的产品" @change="chooseProduct"><el-option v-for="p in library" :key="p.id" :value="p.id" :label="`${p.name} · ${p.brand} ${p.model} · ${p.source_project}`" /></el-select></el-form-item>
          <el-alert v-if="!library.length" type="info" :closable="false" title="请先在设备产品库中新增产品" />
          <div class="two"><el-form-item label="数量"><el-input-number v-model="form.quantity" :min="1" :max="1000000" :precision="0" /></el-form-item><el-form-item label="实际采购单价（元）"><el-input-number v-model="form.unit_price" :min="0" :max="999999999999.99" :precision="2" /></el-form-item></div>
          <p>设备金额：{{ money((form.quantity || 0) * (form.unit_price || 0)) }} 元</p>
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
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { money, datetime } from '@/utils/format'
import { STAGES, POTENTIAL, TASK_STATUS, announceChange } from './shared'
const props = defineProps({ project: Object }), emit = defineEmits(['saved', 'open-task'])
const search = ref(''), stage = ref(''), highOnly = ref(false), selectedId = ref(null)
const schools = computed(() => props.project.schools.filter(s => s.name.includes(search.value) && (!stage.value || stage.value === s.stage) && (!highOnly.value || s.potential === 'high')))
const selected = computed(() => props.project.schools.find(s => s.id === selectedId.value))
const schoolTasks = computed(() => props.project.tasks.filter(t => t.school_id === selectedId.value))
const history = ref([])
let historyVersion = 0
async function loadHistory() {
  const id = selectedId.value, version = ++historyVersion
  if (!id) { history.value = []; return }
  try { const r = await api.get(`/operations/${props.project.id}/schools/${id}/history`); if (version === historyVersion) history.value = r.items } catch {}
}
watch(selectedId, loadHistory)
const incomeChart = computed(() => ({ tooltip: { trigger: 'axis' }, grid: { left: 70, right: 25, top: 20, bottom: 35 }, xAxis: { type: 'category', data: props.project.financials.daily.map(d => d.date) }, yAxis: { type: 'value', name: '元' }, series: [{ type: 'bar', data: props.project.financials.daily.map(d => Number(d.amount)), itemStyle: { color: '#1B3275' }, barMaxWidth: 28 }] }))
const dialog = ref(false), kind = ref('school'), editId = ref(null), saving = ref(false), formRef = ref(), form = reactive({}), library = ref([])
const dialogTitle = computed(() => `${editId.value ? '编辑' : '新增'}${{ school: '学校资料', equipment: '学校设备', income: '每日收入' }[kind.value]}`)
function reset(k, row, values) { Object.keys(form).forEach(k => delete form[k]); Object.assign(form, values); kind.value = k; editId.value = row?.id || null; dialog.value = true }
function editSchool(s) { reset('school', s, { name: s?.name || '', stage: s?.stage || 'contact', potential: s?.potential || 'medium', next_action: s?.next_action || '', next_contact_date: s?.next_contact_date || null, labor_cost: Number(s?.labor_cost || 0), other_cost: Number(s?.other_cost || 0), note: s?.note || '' }) }
async function editEquipment(e) { try { library.value = (await api.get('/operations/products')).items; reset('equipment', e, { product_id: e?.product_id || null, quantity: e?.quantity || 1, unit_price: Number(e?.unit_price || 0), note: e?.note || '' }) } catch {} }
function chooseProduct(id) { form.unit_price = Number(library.value.find(p => p.id === id)?.reference_price || 0) }
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
@media(max-width: 500px) { .two { grid-template-columns: 1fr; gap: 0; } }
</style>
