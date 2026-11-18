<template>
  <div v-if="form" v-loading="loading" class="controls">
    <h3>{{ sections.includes('reports') ? '提醒与汇报对象' : '项目提醒' }}</h3>
    <el-form label-position="top">
      <el-form-item label="提醒方式"><el-switch v-model="form.wecom_reminders" active-text="同时发送企业微信通知" /><p class="jc-small jc-muted">新建项目或交接时通知项目负责人。未完成的任务和阶段节点每天 09:00、15:00（北京时间）提醒任务负责人。关闭企业微信后仍有站内通知；可在任务详情查看企业微信投递情况。</p></el-form-item>
      <template v-if="sections.includes('reports')">
        <el-form-item label="自动个人周报/月报对象"><el-radio-group v-model="form.report_audience"><el-radio value="all">全部执行成员</el-radio><el-radio value="selected">指定关键成员</el-radio></el-radio-group></el-form-item>
        <el-form-item v-if="form.report_audience === 'selected'" label="关键成员"><el-select v-model="form.report_user_ids" multiple filterable aria-label="关键汇报成员"><el-option v-for="m in members.filter(m => m.active && m.role !== 'viewer')" :key="m.user_id" :value="m.user_id" :label="m.name" /></el-select></el-form-item>
        <p class="jc-muted jc-small">项目总报告仍生成；成员需具有本项目报告可见权限。生成周期在“周报/月报 → 自动生成安排”管理。</p>
      </template>
      <el-form-item v-if="sections.includes('funds')" label="关联已有业务项目（经费与审批）"><el-select v-model="form.business_project_code" clearable filterable aria-label="关联经费项目"><el-option v-for="p in projects" :key="p.code" :value="p.code" :label="`${p.name} · ${p.code}`" /></el-select><p class="jc-small jc-muted">关联后读取原有审批归集记录；查看者仍需原有财务与项目权限。</p></el-form-item>
      <el-button type="primary" :loading="saving" @click="save">保存项目工作设置</el-button>
    </el-form>
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
const props=defineProps({projectId:Number,active:Boolean,sections:{type:Array,default:()=>[]}})
const form=ref(null),members=ref([]),projects=ref([]),loading=ref(false),saving=ref(false)
async function load(){if(!props.active)return;loading.value=true;try{const r=await api.get(`/operations/${props.projectId}/controls`);members.value=r.members;projects.value=r.business_projects;form.value={wecom_reminders:r.wecom_reminders,business_project_code:r.business_project_code,report_audience:r.report_audience,report_user_ids:r.report_user_ids,expected_revision:r.revision}}catch{}finally{loading.value=false}}
async function save(){saving.value=true;try{const r=await api.put(`/operations/${props.projectId}/controls`,form.value);form.value.expected_revision=r.revision;ElMessage.success('项目工作设置已保存')}catch{}finally{saving.value=false}}
watch(()=>[props.projectId,props.active],load,{immediate:true})
</script>
<style scoped>.controls { max-width: 760px; padding: 10px 0; }.el-select { width: 100%; }.el-form-item p { width: 100%; line-height: 1.7; }</style>
