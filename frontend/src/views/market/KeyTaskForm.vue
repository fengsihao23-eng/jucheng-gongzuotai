<template>
  <el-dialog v-model="visible" :title="initial ? '调整任务安排' : '安排任务'" width="min(700px, 94vw)" class="key-task-form" append-to-body destroy-on-close>
    <el-form ref="formRef" :model="form" label-position="top">
      <el-form-item label="任务名称" prop="title" :rules="[{required:true,whitespace:true,message:'填写任务名称'}]"><el-input v-model="form.title" aria-label="重点任务名称" maxlength="64" /></el-form-item>
      <el-form-item label="工作内容 / 要求"><el-input v-model="form.requirements" type="textarea" :rows="3" maxlength="6000" aria-label="重点任务要求" /></el-form-item>
      <el-form-item label="任务负责人" prop="owner_id" :rules="[{required:true,message:'选择负责人'}]"><el-select v-model="form.owner_id" filterable aria-label="重点任务负责人"><el-option v-for="u in users" :key="u.id" :value="u.id" :label="u.name" /></el-select></el-form-item>
      <div class="two"><el-form-item label="准备日期" prop="preparation_on" :rules="[{required:true,message:'选择准备日期'}]"><el-date-picker v-model="form.preparation_on" type="date" value-format="YYYY-MM-DD" aria-label="任务准备日期" /></el-form-item><el-form-item label="完成节点" prop="due_on" :rules="[{required:true,message:'选择完成日期'}]"><el-date-picker v-model="form.due_on" type="date" value-format="YYYY-MM-DD" aria-label="任务完成日期" /></el-form-item></div>
      <el-form-item label="进展反馈频率"><el-radio-group v-model="form.cadence" aria-label="重点任务反馈频率"><el-radio value="daily">每日</el-radio><el-radio value="weekly">每周</el-radio><el-radio value="node">按节点</el-radio></el-radio-group></el-form-item>
      <div class="node-heading"><b>阶段节点</b><el-button link type="primary" :disabled="form.nodes.length>=30" @click="form.nodes.push({name:'',due_on:form.due_on})">增加阶段节点</el-button></div>
      <div v-for="(node,index) in form.nodes" :key="node.id||index" class="node-edit"><el-input v-model="node.name" :aria-label="`阶段名称${index+1}`" placeholder="例如：准备资料、提交方案" maxlength="128" :disabled="completed.has(node.id)" /><el-date-picker v-model="node.due_on" type="date" value-format="YYYY-MM-DD" :aria-label="`阶段日期${index+1}`" :disabled="completed.has(node.id)" /><el-button link :disabled="completed.has(node.id)" @click="form.nodes.splice(index,1)">移除</el-button></div>
      <p class="jc-small jc-muted">未完成任务按所选频率和阶段节点提醒负责人，进展填写后保留历史记录。</p>
    </el-form>
    <template #footer><el-button @click="visible=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存安排</el-button></template>
  </el-dialog>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
const props=defineProps({modelValue:Boolean,project:Object,initial:Object}),emit=defineEmits(['update:modelValue','saved'])
const visible=computed({get:()=>props.modelValue,set:v=>emit('update:modelValue',v)}),form=reactive({nodes:[]}),formRef=ref(),saving=ref(false),users=ref([])
const completed=computed(()=>new Set((props.initial?.nodes||[]).filter(n=>n.completed_on).map(n=>n.id)))
watch(visible,async v=>{if(!v)return;const t=props.initial;Object.assign(form,{title:t?.title||'',requirements:t?.requirements||'',owner_id:t?.owner_id||props.project.owner_id,preparation_on:t?.preparation_on||props.project.start_date||props.project.today,due_on:t?.plan_date||props.project.plan_end_date||null,cadence:t?.cadence||'weekly',nodes:(t?.nodes||[]).map(n=>({id:n.id,name:n.name,due_on:n.due_on}))});try{users.value=(await api.get('/market/key-options')).users}catch{}})
async function save(){if(saving.value||!await formRef.value.validate().catch(()=>false))return;if(form.preparation_on>form.due_on||form.nodes.some(n=>!n.name.trim()||!n.due_on||n.due_on<form.preparation_on||n.due_on>form.due_on))return ElMessage.warning('填写阶段名称与日期，日期须在任务准备和完成日期之间');saving.value=true;try{const path=`/market/projects/${props.project.project_code}/tasks`;const result=props.initial?await api.put(`${path}/${props.initial.id}`,{...form,expected_revision:props.initial.revision}):await api.post(path,form);visible.value=false;ElMessage.success('任务安排已保存');announceBusinessChange();emit('saved',result)}catch{}finally{saving.value=false}}
</script>
<style scoped>
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;}.el-select,.el-date-editor{width:100%;}.node-heading{display:flex;justify-content:space-between;align-items:center;margin:6px 0 10px;}.node-edit{display:grid;grid-template-columns:minmax(0,1fr) 165px 40px;gap:8px;margin-bottom:10px;}:global(.key-task-form > .el-dialog__body){max-height:70dvh;overflow:auto;}@media(max-width:560px){.two{grid-template-columns:1fr;gap:0;}.node-edit{grid-template-columns:minmax(0,1fr) 40px;}.node-edit .el-date-editor{grid-row:2;}.node-edit .el-button{grid-column:2;grid-row:1;}}
</style>
