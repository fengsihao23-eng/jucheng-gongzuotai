<template>
  <div v-loading="loading" class="warehouse">
    <div class="jc-row between tools"><div><h3>{{ scrapOnly ? '设备报废' : '仓库物料' }}</h3><p class="jc-small jc-muted">{{ data?.note }}</p></div><el-button v-if="data?.can_manage" type="primary" @click="openForm">{{ scrapOnly ? '登记设备报废' : '登记出入库' }}</el-button></div>
    <div class="jc-grid c4 stock-stats" aria-label="设备数量对账">
      <Kpi label="设备总数" :value="totals.total" /><Kpi label="在库设备" :value="totals.warehouse" /><Kpi label="在运营设备" :value="totals.operating" /><Kpi label="已报废设备" :value="totals.scrapped" />
    </div>
    <el-table :data="data?.items || []" empty-text="暂无库存，请从学校采购登记或手工入库" class="mt-16">
      <el-table-column prop="name" label="设备 / 物料" min-width="160" /><el-table-column prop="model" label="型号" min-width="130" />
      <el-table-column prop="product_id" label="产品编号" width="95" /><el-table-column prop="unit" label="单位" width="65" />
      <el-table-column prop="total" label="总数" width="90" align="right" /><el-table-column prop="warehouse" label="在库" width="90" align="right" />
      <el-table-column prop="operating" label="在运营" width="90" align="right" /><el-table-column prop="scrapped" label="已报废" width="90" align="right" />
    </el-table>
    <p v-if="!scrapOnly" class="jc-small jc-muted mt-16">{{ data?.approval_note }}。运营项目编号：{{ project.id }}；产品编号见上表。</p>
    <h3 class="mt-16">{{ scrapOnly ? '报废记录' : '出入库及报废记录' }}<span class="jc-small jc-muted"> · 共 {{ data?.movement_count || 0 }} 条{{ data?.movement_count > 200 ? '，显示最近 200 条' : '' }}</span></h3>
    <el-table :data="data?.movements || []" empty-text="暂无相关记录" @row-contextmenu="movementMenu">
      <el-table-column prop="occurred_on" label="业务日期" width="115" /><el-table-column prop="product_name" label="设备" min-width="150" />
      <el-table-column prop="action_name" label="操作" width="145" /><el-table-column prop="quantity" label="数量" width="85" align="right" />
      <el-table-column prop="school" label="学校 / 使用地点" min-width="150" /><el-table-column prop="note" label="说明" min-width="220" show-overflow-tooltip />
      <el-table-column label="来源" min-width="140"><template #default="{ row }">{{ { purchase: '学校采购', manual: '工作台登记', wecom: '企业微信审批' }[row.source_type] }}<div class="jc-small jc-muted">{{ row.reference || row.operator }}</div></template></el-table-column>
    </el-table>
    <el-dialog v-model="formOpen" :title="scrapOnly ? '登记设备报废' : '登记出入库'" width="min(560px, 94vw)" append-to-body destroy-on-close>
      <el-form ref="formRef" :model="form" label-position="top">
        <el-form-item label="操作类型"><el-select v-model="form.action" aria-label="库存操作"><el-option v-for="(name, key) in actions" :key="key" :value="key" :label="name" /></el-select></el-form-item>
        <el-form-item label="设备产品" prop="product_id" :rules="[{ required: true, message: '选择设备产品' }]"><el-select v-model="form.product_id" filterable aria-label="库存设备"><el-option v-for="p in products" :key="p.id" :value="p.id" :label="`${p.name} · #${p.id}`" /></el-select></el-form-item>
        <div class="two"><el-form-item label="数量"><el-input-number v-model="form.quantity" :min="1" :max="1000000" :precision="0" aria-label="库存数量" /></el-form-item><el-form-item label="业务日期" prop="occurred_on" :rules="[{ required: true, message: '选择日期' }]"><el-date-picker v-model="form.occurred_on" type="date" value-format="YYYY-MM-DD" aria-label="出入库日期" /></el-form-item></div>
        <el-form-item label="学校 / 使用地点（可选）"><el-select v-model="form.school_id" clearable filterable aria-label="库存使用学校"><el-option v-for="s in project.schools || []" :key="s.id" :value="s.id" :label="s.name" /></el-select></el-form-item>
        <el-form-item label="依据 / 原因" prop="note" :rules="[{ required: true, whitespace: true, message: '填写出入库或报废依据' }]"><el-input v-model="form.note" type="textarea" :rows="3" maxlength="2000" aria-label="库存操作说明" /></el-form-item>
        <p class="jc-small jc-muted">{{ scrapOnly ? '报废会减少所选来源数量，并计入已报废设备。' : '出库后计入在运营设备；退库从在运营设备转回仓库。' }}</p>
      </el-form>
      <template #footer><el-button @click="formOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">确认登记</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { announceChange } from './shared'
const props = defineProps({ project: Object, active: Boolean, scrapOnly: Boolean })
const data = ref(null), loading = ref(false), saving = ref(false), formOpen = ref(false), formRef = ref(), products = ref([]), form = reactive({})
const actions = computed(() => props.scrapOnly ? { scrap_warehouse: '在库设备报废', scrap_operating: '运营设备报废' } : { inbound: '设备入库', outbound: '出库投入运营', return: '退回仓库' })
const totals = computed(() => (data.value?.items || []).reduce((sum, r) => { for (const k of Object.keys(sum)) sum[k] += r[k]; return sum }, { total: 0, warehouse: 0, operating: 0, scrapped: 0 }))
let version = 0
async function load(quiet = false) {
  if (!props.active) return
  const id = ++version; if (!quiet) loading.value = true
  try { const r = await api.get(`/operations/${props.project.id}/warehouse`, { scrap: props.scrapOnly }); if (id === version) data.value = r }
  catch { if (id === version) data.value = null } finally { if (id === version) loading.value = false }
}
async function openForm() {
  Object.assign(form, { action: props.scrapOnly ? 'scrap_warehouse' : 'outbound', product_id: null, quantity: 1, occurred_on: data.value.today, school_id: null, note: '', request_key: crypto.randomUUID() })
  const library = await api.get('/operations/products')
  const choices = new Map(library.items.map(p => [p.id, p]))
  for (const p of data.value.items) choices.set(p.product_id, { id: p.product_id, name: p.name })
  products.value = [...choices.values()]; formOpen.value = true
}
async function save() {
  if (saving.value || !await formRef.value.validate().catch(() => false)) return
  saving.value = true
  try { await api.post(`/operations/${props.project.id}/warehouse/movements`, form); formOpen.value = false; ElMessage.success('库存记录已登记'); await load(); announceChange() }
  catch {} finally { saving.value = false }
}
watch(() => [props.project.id, props.active, props.scrapOnly], () => load(), { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => props.active && !formOpen.value })

const movementMenu = rowDeletion(row => props.project.can_manage && recordRemoval('asset-movement', row.id, row.product_name, () => load(), { title: '删除设备流转记录' }))
</script>
<style scoped>
.tools { gap:12px; flex-wrap:wrap; }.tools h3 { margin:0; }.tools p { margin:6px 0; line-height:1.6; }.warehouse .el-select { width:100%; }.two { display:grid; grid-template-columns:1fr 1fr; gap:14px; }.two .el-date-editor { width:100%; }.stock-stats { margin-top:14px; }@media(max-width:600px) { .stock-stats { grid-template-columns:repeat(2,minmax(0,1fr)); }.two { grid-template-columns:1fr; gap:0; } }
</style>
