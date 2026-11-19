<template>
  <el-dialog v-model="visible" title="任务情况" width="min(900px, 94vw)" class="key-task-dialog" append-to-body destroy-on-close>
    <div v-loading="loading" v-if="item" class="task-layout">
      <div><h3>{{ item.title }}</h3><p class="jc-muted jc-small">{{ item.owner_name }} · 准备 {{ item.preparation_on || '未登记' }} · 完成节点 {{ item.plan_date || '未登记' }}</p><StatusPill :text="item.is_delayed ? '逾期未完成' : item.status_text" :tone="item.is_delayed ? 'down' : item.status===3 ? 'up':'brand'" />
        <p v-if="item.requirements" class="plain">{{ item.requirements }}</p>
        <h4 v-if="item.nodes.length">阶段节点</h4><ul class="node-list"><li v-for="n in item.nodes" :key="n.id"><span>{{ n.name }}</span><small :class="n.overdue?'jc-down':'jc-muted'">{{ n.due_on }} · {{ n.completed_on ? `已完成 ${n.completed_on}` : n.overdue?'已逾期':'待完成' }}</small></li></ul>
        <p v-if="item.problems" class="plain jc-down">存在问题：{{ item.problems }}</p><p v-if="item.next_step" class="plain">下一步：{{ item.next_step }}</p>
        <p v-if="item.feedback_due_on && item.status!==3" class="jc-small" :class="item.needs_feedback?'jc-warn':'jc-muted'">{{ item.cadence==='daily'?'每日':'每周' }}反馈 · 下次 {{ item.feedback_due_on }}</p>
        <el-button v-if="item.can_manage && item.status!==3 && !project.paused && project.status!==5" link type="primary" @click="emit('edit',item);visible=false">调整任务安排</el-button>
      </div>
      <div v-if="item.can_feedback && item.status!==3 && !project.paused && project.status!==5" class="feedback-area">
        <h3>填写进展</h3><el-form ref="formRef" :model="form" label-position="top">
          <el-form-item label="当前进展" prop="content" :rules="[{required:true,whitespace:true,message:'填写实际进展'}]"><el-input v-model="form.content" aria-label="重点任务进展" type="textarea" :rows="3" maxlength="6000" placeholder="已完成什么、当前进度如何" /></el-form-item>
          <el-form-item label="存在问题"><el-input v-model="form.problems" aria-label="重点任务问题" type="textarea" :rows="2" maxlength="6000" placeholder="没有问题可留空" /></el-form-item><el-form-item label="下一步"><el-input v-model="form.next_step" aria-label="重点任务下一步" type="textarea" :rows="2" maxlength="6000" /></el-form-item>
          <el-form-item v-if="item.nodes.some(n=>!n.completed_on)" label="本次完成的节点"><el-checkbox-group v-model="form.completed_node_ids"><el-checkbox v-for="n in item.nodes.filter(n=>!n.completed_on)" :key="n.id" :value="n.id">{{ n.name }}</el-checkbox></el-checkbox-group></el-form-item>
          <el-form-item label="任务状态"><el-radio-group v-model="form.status" aria-label="重点任务状态"><el-radio :value="2">进行中</el-radio><el-radio :value="3">已完成</el-radio></el-radio-group></el-form-item>
          <el-form-item v-if="form.status===3" label="实际完成日期"><el-date-picker v-model="form.completed_on" type="date" value-format="YYYY-MM-DD" aria-label="任务实际完成日期" /></el-form-item>
          <el-button type="primary" :loading="saving" @click="submit">提交进展</el-button>
        </el-form>
      </div>
      <section class="history"><h3>过程记录</h3><article v-for="h in item.history" :key="h.id"><small class="jc-muted">{{ datetime(h.feedback.created_at) }} · {{ h.feedback.reporter_name }}</small><p class="plain">{{ h.feedback.content }}</p></article><p v-if="!item.history.length" class="jc-small jc-muted">尚未填写进展</p></section>
    </div>
    <el-empty v-else-if="!loading" description="无法读取任务，请检查权限或重新打开" />
    <template #footer><el-button @click="visible=false">关闭</el-button></template>
  </el-dialog>
</template>
<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
const props=defineProps({modelValue:Boolean,taskId:Number,project:Object}),emit=defineEmits(['update:modelValue','saved','edit'])
const visible=computed({get:()=>props.modelValue,set:v=>emit('update:modelValue',v)}),item=ref(null),loading=ref(false),saving=ref(false),form=reactive({}),formRef=ref()
let version=0
async function load(){if(!visible.value||!props.taskId)return;const v=++version;loading.value=true;try{const r=await api.get(`/market/projects/${props.project.project_code}/tasks/${props.taskId}`);if(v!==version||!visible.value)return;item.value=r;Object.assign(form,{content:'',problems:r.problems||'',next_step:r.next_step||'',status:2,completed_on:props.project.today,completed_node_ids:[]});await nextTick();formRef.value?.clearValidate()}catch{if(v===version)item.value=null}finally{if(v===version)loading.value=false}}
async function submit(){if(saving.value||!await formRef.value.validate().catch(()=>false))return;if(form.status===3&&item.value.nodes.some(n=>!n.completed_on&&!form.completed_node_ids.includes(n.id)))return ElMessage.warning('请先确认所有阶段节点完成');saving.value=true;try{await api.post(`/market/projects/${props.project.project_code}/tasks/${props.taskId}/feedback`,{...form,completed_on:form.status===3?form.completed_on:null,expected_revision:item.value.revision});ElMessage.success('进展已保存');await load();announceBusinessChange();emit('saved')}catch{}finally{saving.value=false}}
watch(()=>[visible.value,props.taskId,props.project.project_code],()=>{if(visible.value)load();else version++},{immediate:true})
</script>
<style scoped>
.task-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px;}.task-layout h3{margin:0 0 12px;}.task-layout h4{margin:18px 0 8px;}.plain{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;}.node-list{list-style:none;padding:0;margin:0;}.node-list li{display:grid;gap:4px;padding:9px 0;border-bottom:1px solid var(--jc-line-2);}.history{grid-column:1/-1;border-top:1px solid var(--jc-line);padding-top:18px;}.history article{padding:10px 0;border-bottom:1px solid var(--jc-line-2);}.feedback-area{border-left:1px solid var(--jc-line);padding-left:24px;}.el-checkbox-group{display:grid;}:global(.key-task-dialog > .el-dialog__body){max-height:70dvh;overflow:auto;}@media(max-width:650px){.task-layout{grid-template-columns:1fr;gap:20px;}.feedback-area{border-left:0;border-top:1px solid var(--jc-line);padding:18px 0 0;}}
</style>
