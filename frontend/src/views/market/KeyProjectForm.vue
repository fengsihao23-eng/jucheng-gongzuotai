<template>
  <el-dialog v-model="visible" :title="initial ? '编辑重点项目' : '新建重点项目'" width="min(620px, 94vw)" class="key-project-form" append-to-body destroy-on-close>
    <el-form ref="formRef" :model="form" label-position="top">
      <el-form-item label="项目名称" prop="name" :rules="[{required:true,whitespace:true,message:'填写项目名称'}]"><el-input v-model="form.name" aria-label="重点项目名称" maxlength="128" /></el-form-item>
      <el-form-item label="项目内容 / 需要完成什么"><el-input v-model="form.content" type="textarea" :rows="3" maxlength="6000" aria-label="重点项目内容" placeholder="说明项目目标、工作内容和要求" /></el-form-item>
      <el-form-item label="项目负责人" prop="owner_id" :rules="[{required:true,message:'选择项目负责人'}]"><el-select v-model="form.owner_id" filterable aria-label="重点项目负责人"><el-option v-for="u in users" :key="u.id" :value="u.id" :label="u.name" /></el-select></el-form-item>
      <div class="two"><el-form-item label="准备日期" prop="preparation_on" :rules="[{required:true,message:'选择准备日期'}]"><el-date-picker v-model="form.preparation_on" type="date" value-format="YYYY-MM-DD" aria-label="项目准备日期" /></el-form-item><el-form-item label="计划完成日期" prop="due_on" :rules="[{required:true,message:'选择完成日期'}]"><el-date-picker v-model="form.due_on" type="date" value-format="YYYY-MM-DD" aria-label="项目完成日期" /></el-form-item></div>
      <details class="additional"><summary>客户与业务信息</summary><div class="two mt-16"><el-form-item label="客户 / 合作单位（可选）"><el-input v-model="form.customer_name" maxlength="128" aria-label="重点项目客户" /></el-form-item><el-form-item label="业务线"><el-select v-model="form.business_line" aria-label="重点项目业务线"><el-option v-for="(name,key) in BUSINESS_LINES" :key="key" :label="name" :value="key" /></el-select></el-form-item></div></details>
      <el-form-item v-if="initial" label="项目状态"><el-select v-model="form.status" aria-label="重点项目状态"><el-option v-for="(v,k) in PROJECT_STATUS" :key="k" :value="Number(k)" :label="v" /></el-select></el-form-item>
    </el-form>
    <template #footer><el-button @click="visible=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ initial ? '保存项目' : '创建重点项目' }}</el-button></template>
  </el-dialog>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import { BUSINESS_LINES, PROJECT_STATUS } from '@/utils/format'
const props=defineProps({modelValue:Boolean,initial:Object}),emit=defineEmits(['update:modelValue','saved'])
const visible=computed({get:()=>props.modelValue,set:v=>emit('update:modelValue',v)}),auth=useAuth(),form=reactive({}),users=ref([]),formRef=ref(),saving=ref(false)
watch(visible,async v=>{if(!v)return;const r=props.initial;Object.assign(form,{name:r?.name||'',content:r?.content||'',owner_id:r?.owner_id||auth.profile?.id,preparation_on:r?.start_date||null,due_on:r?.plan_end_date||null,customer_name:r?.customer_name||'',business_line:r?.business_line||'TD',status:r?.status||2});try{const options=await api.get('/market/key-options');users.value=options.users;if(!form.preparation_on)form.preparation_on=options.today}catch{}})
async function save(){if(saving.value||!await formRef.value.validate().catch(()=>false))return;if(form.preparation_on>form.due_on)return ElMessage.warning('准备日期不能晚于完成日期');saving.value=true;try{const data=props.initial?await api.put(`/market/projects/${props.initial.project_code}`,{...form,expected_revision:props.initial.revision}):await api.post('/market/projects',form);visible.value=false;ElMessage.success(props.initial?'重点项目已保存':'重点项目已创建');announceBusinessChange();emit('saved',data)}catch{}finally{saving.value=false}}
</script>
<style scoped>
.two { display:grid;grid-template-columns:1fr 1fr;gap:14px; }.el-select,.el-date-editor { width:100%; }.additional { margin-bottom:18px; }.additional summary { cursor:pointer;color:var(--jc-ink-3);font-size:13px; }:global(.key-project-form > .el-dialog__body){max-height:70dvh;overflow:auto;}@media(max-width:560px){.two{grid-template-columns:1fr;gap:0;}}
</style>
