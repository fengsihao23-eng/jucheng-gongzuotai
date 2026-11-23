<template>
  <div>
    <div class="jc-row between"><p class="jc-muted">产品资料可在有权限的运营项目之间复用，实际投入按学校设备清单中的采购单价计算。</p><div class="jc-row wrap"><CampusProposal :project="project" /><el-button v-if="project.can_manage" type="primary" @click="edit()">新增产品</el-button></div></div>
    <el-table :data="project.products">
      <el-table-column prop="name" label="产品名称" min-width="200" />
      <el-table-column prop="brand" label="品牌" width="140" />
      <el-table-column prop="model" label="型号 / 规格" min-width="180" show-overflow-tooltip />
      <el-table-column prop="unit" label="单位" width="80" />
      <el-table-column label="参考单价（元）" width="160" align="right"><template #default="{ row }">{{ unitPrice(row.reference_price) }}</template></el-table-column>
      <el-table-column prop="note" label="来源与备注" min-width="180" show-overflow-tooltip />
      <el-table-column v-if="project.can_manage" width="90"><template #default="{ row }"><el-button link type="primary" @click="edit(row)">编辑</el-button></template></el-table-column>
      <template #empty><el-empty description="暂无产品，新增后可在学校设备清单中选用" /></template>
    </el-table>
    <RecordDetailDialog v-model="visible" :dialog-title="id ? '产品详情' : '新增产品'" :heading="form.name"
      :target="{ kind: 'operation-product', id }" progress-hint="记录产品核对、设备采购或维护进展。">
      <el-form ref="formRef" :model="form" label-position="top">
        <el-form-item label="产品名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写产品名称' }]"><el-input v-model="form.name" maxlength="128" /></el-form-item>
        <div class="two"><el-form-item label="品牌"><el-input v-model="form.brand" maxlength="64" /></el-form-item><el-form-item label="型号 / 规格"><el-input v-model="form.model" type="textarea" maxlength="6000" /></el-form-item></div>
        <div class="two"><el-form-item label="单位"><el-input v-model="form.unit" maxlength="16" /></el-form-item><el-form-item label="参考单价（元，未标价可留空）"><el-input-number v-model="form.reference_price" :min="0" :max="999999999999.99" :precision="6" /></el-form-item></div>
        <el-form-item label="备注"><el-input v-model="form.note" type="textarea" maxlength="6000" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存产品</el-button></template>
    </RecordDetailDialog>
  </div>
</template>
<script setup>
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import CampusProposal from './CampusProposal.vue'
import { unitPrice, announceChange } from './shared'
const props = defineProps({ project: Object }), emit = defineEmits(['saved'])
const visible = ref(false), saving = ref(false), formRef = ref(), id = ref(null), form = reactive({})
function edit(row) { id.value = row?.id || null; Object.assign(form, { name: row?.name || '', brand: row?.brand || '', model: row?.model || '', unit: row?.unit || '台', reference_price: row?.reference_price == null ? null : Number(row.reference_price), note: row?.note || '' }); visible.value = true }
async function save() {
  if (!await formRef.value.validate().catch(() => false)) return
  saving.value = true
  try { const base = `/operations/${props.project.id}/products`; if (id.value) await api.put(`${base}/${id.value}`, form); else await api.post(base, form); visible.value = false; ElMessage.success('产品已保存'); emit('saved'); announceChange() } catch {} finally { saving.value = false }
}
</script>
<style scoped>.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; } @media(max-width: 500px) { .two { grid-template-columns: 1fr; gap: 0; } }</style>
