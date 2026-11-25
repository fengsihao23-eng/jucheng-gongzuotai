<template>
  <div v-loading="loading">
    <div class="tools">
      <h3>{{ label }}</h3>
      <el-input v-model="keyword" clearable :placeholder="searchPlaceholder" aria-label="维护记录搜索" />
      <el-select v-model="status" clearable placeholder="跟进状态" aria-label="维护记录状态">
        <el-option v-for="(name, key) in statuses" :key="key" :value="key" :label="name" />
      </el-select>
      <span class="jc-spacer" />
      <el-button v-if="data?.can_manage" type="primary" @click="edit()">新增{{ label }}</el-button>
    </div>
    <AlarmMaintenanceLedger v-if="hasWorkbook" :kind="kind" :rows="filtered" :keyword="keyword" :status="status"
      :row-class-name="rowClassName" @row-contextmenu="maintenanceMenu">
      <template #action="{ row }">
        <el-button link type="primary" @click="edit(row)">{{ row.can_edit ? '查看 / 更新' : '查看详情' }}</el-button>
      </template>
    </AlarmMaintenanceLedger>
    <template v-else>
      <p v-if="['arrears', 'collection_maintenance'].includes(kind)" class="jc-small jc-muted">按项目维护欠费与回款跟进记录。登记金额用于运营跟踪，财务到账及合同统计仍以原财务来源为准。</p>
      <el-table :data="filtered" empty-text="暂无维护记录" :row-class-name="rowClassName" @row-contextmenu="maintenanceMenu">
      <el-table-column prop="title" label="事项 / 客户资料" min-width="180" /><el-table-column prop="customer_name" label="客户" min-width="130" />
      <el-table-column v-if="kind === 'device_maintenance'" prop="device_name" label="设备" min-width="130" />
      <el-table-column v-if="kind === 'customers'" prop="contact_name" label="联系人" min-width="105" /><el-table-column v-if="kind === 'customers'" prop="phone" label="联系电话" min-width="135" />
      <el-table-column v-if="['arrears','collection_maintenance'].includes(kind)" label="金额（元）" min-width="130" align="right"><template #default="{row}">{{ row.amount == null ? '未登记' : money(row.amount) }}</template></el-table-column>
      <el-table-column prop="owner_name" label="负责人" width="105" /><el-table-column prop="due_on" label="下次处理日期" width="120" />
      <el-table-column label="状态" width="95"><template #default="{row}">{{ statuses[row.status] }}</template></el-table-column>
      <el-table-column prop="problems" label="存在问题" min-width="170" show-overflow-tooltip />
      <el-table-column width="110" fixed="right"><template #default="{row}"><el-button link type="primary" @click="edit(row)">{{ row.can_edit ? '查看 / 更新' : '查看详情' }}</el-button></template></el-table-column>
      </el-table>
    </template>
    <el-dialog v-model="opened" :title="`${label} · ${form.id ? '详情与跟进' : '新增记录'}`" width="min(760px, 94vw)" class="ops-maintenance-dialog" append-to-body destroy-on-close>
      <section v-if="sourceRow" class="workbook-source" aria-label="工作簿原始数据">
        <h3>工作簿原始数据 <small>{{ sourceRow.sheet }} · 第 {{ sourceRow.row_number }} 行</small></h3>
        <dl>
          <div v-for="column in alarmColumns[kind]" :key="column.key">
            <dt>{{ column.label }}</dt>
            <dd>{{ formatAlarmValue(sourceRow.values[column.key], column) }}</dd>
          </div>
        </dl>
      </section>
      <el-form ref="formRef" :model="form" :disabled="readOnly" label-position="top">
        <el-form-item label="事项名称 / 资料标题" prop="title" :rules="[{ required:true, whitespace:true, message:'填写名称' }]"><el-input aria-label="维护事项" v-model="form.title" maxlength="160" /></el-form-item>
        <div class="two"><el-form-item label="客户名称"><el-input aria-label="维护客户" v-model="form.customer_name" maxlength="128" /></el-form-item><el-form-item label="负责人" prop="owner_id" :rules="[{required:true,message:'选择负责人'}]"><el-select v-model="form.owner_id" :disabled="!data?.can_manage" filterable><el-option v-for="m in data?.members || []" :key="m.user_id" :value="m.user_id" :label="m.name" /></el-select></el-form-item></div>
        <template v-if="kind === 'customers'"><div class="two"><el-form-item label="联系人"><el-input v-model="form.contact_name" maxlength="64" /></el-form-item><el-form-item label="联系电话"><el-input v-model="form.phone" maxlength="64" /></el-form-item></div><el-form-item label="地址"><el-input v-model="form.address" maxlength="500" /></el-form-item></template>
        <el-form-item v-if="kind === 'device_maintenance'" label="设备名称 / 编号"><el-input aria-label="设备名称编号" v-model="form.device_name" maxlength="128" /></el-form-item>
        <el-form-item v-if="['arrears','collection_maintenance'].includes(kind)" :label="amountLabel"><el-input-number v-model="form.amount" :min="0" :max="999999999999.99" :precision="2" :controls="false" /></el-form-item>
        <div class="two"><el-form-item label="处理状态"><el-select v-model="form.status"><el-option v-for="(v,k) in statuses" :key="k" :value="k" :label="v" /></el-select></el-form-item><el-form-item label="下次处理日期"><el-date-picker v-model="form.due_on" type="date" value-format="YYYY-MM-DD" /></el-form-item></div>
        <el-form-item :label="kind === 'customers' ? '客户资料 / 维护说明' : '处理情况 / 最新跟进'"><el-input aria-label="维护处理情况" v-model="form.content" type="textarea" :rows="3" maxlength="6000" /></el-form-item>
        <el-form-item label="存在问题"><el-input aria-label="维护存在问题" v-model="form.problems" type="textarea" :rows="2" maxlength="6000" /></el-form-item><el-form-item label="下一步"><el-input aria-label="维护下一步" v-model="form.next_step" type="textarea" :rows="2" maxlength="6000" /></el-form-item>
      </el-form>
      <div v-if="history.length" class="history"><h3>过程记录</h3><article v-for="h in history" :key="h.id"><small class="jc-muted">{{ datetime(h.created_at) }} · {{ h.author }} · {{ statuses[h.status] }}</small><p>{{ h.content || '更新基础资料' }}</p><p v-if="h.problems">存在问题：{{ h.problems }}</p><p v-if="h.next_step">下一步：{{ h.next_step }}</p></article></div>
      <template #footer><el-button @click="opened=false">关闭</el-button><el-button v-if="!readOnly" type="primary" :loading="saving" @click="save">保存维护记录</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { useAuth } from '@/store/auth'
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { money, datetime } from '@/utils/format'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import AlarmMaintenanceLedger from './AlarmMaintenanceLedger.vue'
import { alarmColumns, formatAlarmValue } from './alarmMaintenance'
import { announceChange } from './shared'
const removalAuth = useAuth()

const props=defineProps({project:Object,active:Boolean,kind:String,label:String})
const statuses={open:'待处理',processing:'处理中',done:'已完成'}
const data=ref(null),keyword=ref(''),status=ref(''),loading=ref(false),saving=ref(false),opened=ref(false),formRef=ref(),form=reactive({}),history=ref([]),readOnly=ref(false)
const sourceRow = ref(null)
const hasWorkbook = computed(() => data.value?.items.some(row => row.workbook_row))
const searchPlaceholder = computed(() => {
  if (!hasWorkbook.value) return '搜索事项 / 客户 / 负责人'
  if (props.kind === 'collection_maintenance') return '搜索对方 / 摘要'
  return props.kind === 'device_maintenance' ? '搜索学校 / 区域 / IP' : '搜索学校 / 区域 / 分组'
})
const amountLabel = computed(() => {
  if (!sourceRow.value) return '金额（元，可选）'
  return props.kind === 'arrears' ? '应补缴金额（元）' : '到账金额（元）'
})
const filtered = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  const rows = (data.value?.items || []).filter(row => {
    if (status.value && row.status !== status.value) return false
    if (!query) return true
    const fields = [row.title, row.customer_name, row.owner_name, row.device_name]
    fields.push(...Object.values(row.workbook_row?.values || {}))
    return fields.some(value => value != null && String(value).toLowerCase().includes(query))
  })
  if (hasWorkbook.value) {
    rows.sort((a, b) => (a.workbook_row?.row_number ?? 0) - (b.workbook_row?.row_number ?? 0))
  }
  return rows
})
const rowClassName = ({ row }) => row.status !== 'done' && row.due_on && row.due_on < props.project.today ? 'is-alert' : ''
let version=0
async function load(quiet=false){if(!props.active)return;const v=++version;if(!quiet)loading.value=true;try{const r=await api.get(`/operations/${props.project.id}/maintenance`,{kind:props.kind});if(v===version)data.value=r}catch{if(v===version)data.value=null}finally{if(v===version)loading.value=false}}
async function edit(row){
  Object.assign(form,{id:row?.id||null,kind:props.kind,title:row?.title||'',customer_name:row?.customer_name||'',contact_name:row?.contact_name||'',phone:row?.phone||'',address:row?.address||'',device_name:row?.device_name||'',amount:row?.amount==null?null:Number(row.amount),owner_id:row?.owner_id||props.project.owner_id,status:row?.status||'open',due_on:row?.due_on||null,content:row?.content||'',problems:row?.problems||'',next_step:row?.next_step||'',expected_revision:row?.revision||null})
  sourceRow.value = row?.workbook_row || null
  readOnly.value=row?!row.can_edit:!data.value?.can_manage;history.value=[];opened.value=true
  if(row){const r=await api.get(`/operations/${props.project.id}/maintenance/${row.id}/history`);if(opened.value&&form.id===row.id)history.value=r.items}
}
async function save(){if(saving.value||!await formRef.value.validate().catch(()=>false))return;saving.value=true;try{const {id,...body}=form;if(id)await api.put(`/operations/${props.project.id}/maintenance/${id}`,body);else await api.post(`/operations/${props.project.id}/maintenance`,body);opened.value=false;ElMessage.success('维护记录已保存');await load();announceChange()}catch{}finally{saving.value=false}}
watch(()=>[props.project.id,props.active,props.kind],()=>load(),{immediate:true})
useBusinessRefresh(()=>load(true),{interval:12000,enabled:()=>props.active&&!opened.value})

const maintenanceMenu = rowDeletion(row => (removalAuth.hasDeleteGrant || row.can_edit || data.value?.can_manage) && recordRemoval('maintenance', row.id, row.title, () => load(), { title: '删除维护记录' }))
</script>
<style scoped>
.tools { display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px; }.tools h3 { margin:0; }.tools .el-input { width:240px; }.tools .el-select { width:130px; }.two { display:grid;grid-template-columns:1fr 1fr;gap:14px; }.el-form .el-select,.el-form .el-date-editor { width:100%; }.history article { border-top:1px solid var(--jc-line);padding:12px 0;white-space:pre-wrap;overflow-wrap:anywhere; }.history p { margin:5px 0; }:global(.ops-maintenance-dialog > .el-dialog__body) { max-height:70dvh;overflow:auto; }@media(max-width:560px) { .two { grid-template-columns:1fr;gap:0; }.tools .el-input { width:100%; } }
.workbook-source { padding:14px; margin-bottom:16px; border:1px solid var(--jc-line); border-radius:8px; }
.workbook-source h3 { margin:0 0 12px; font-size:14px; }
.workbook-source small { margin-left:6px; color:var(--jc-ink-3); font-weight:400; }
.workbook-source dl { display:grid; grid-template-columns:repeat(auto-fit,minmax(176px,1fr)); gap:12px; margin:0; }
.workbook-source dt { color:var(--jc-ink-3); font-size:12px; }
.workbook-source dd { margin:4px 0 0; overflow-wrap:anywhere; }
</style>
