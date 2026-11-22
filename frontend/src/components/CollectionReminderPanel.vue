<template>
  <section v-if="data?.can_manage || data?.rule || data?.payment_due_on" class="collection-reminder">
    <h3>自动节点提醒</h3>
    <p v-if="data.payment_due_on" :class="data.payment_due ? 'jc-down' : 'jc-muted'" class="jc-small">回款日 {{ data.payment_due_on }}<span v-if="data.payment_due"> · 到期未收，请汇报跟进情况和原因</span></p>
    <p v-if="data.rule" class="jc-small" :class="data.rule.overdue ? 'jc-down' : 'jc-muted'">{{ data.rule.enabled ? `${data.rule.recipient_name} · ${data.rule.cadence === 'daily' ? '每日' : '每周'}反馈 · 下次节点 ${data.rule.next_due_on}${data.rule.overdue ? ' · 已超时' : ''}` : '自动提醒已暂停' }}</p>
    <details v-if="data.can_manage"><summary>{{ data.rule ? '调整提醒安排' : '设置负责人及日 / 周反馈节点' }}</summary>
      <el-form label-position="top" class="reminder-form">
        <el-form-item label="节点负责人"><el-select v-model="form.recipient_id" filterable aria-label="自动提醒负责人"><el-option v-for="u in data.recipients" :key="u.id" :value="u.id" :label="u.name" /></el-select></el-form-item>
        <el-form-item label="反馈周期"><el-radio-group v-model="form.cadence" aria-label="回款反馈周期"><el-radio value="daily">每日</el-radio><el-radio value="weekly">每周</el-radio></el-radio-group></el-form-item>
        <el-form-item label="首次 / 重新安排反馈日期"><el-date-picker v-model="form.due_on" type="date" value-format="YYYY-MM-DD" aria-label="回款反馈节点日期" /></el-form-item>
        <el-form-item label="约定回款日"><el-date-picker v-model="form.payment_due_on" type="date" value-format="YYYY-MM-DD" aria-label="约定回款日" placeholder="未指定时使用反馈中的预计回款日" /></el-form-item>
        <el-form-item label="同时提醒管理者"><el-select v-model="form.manager_ids" multiple filterable aria-label="回款提醒管理者"><el-option v-for="u in data.managers" :key="u.id" :value="u.id" :label="u.name" /></el-select></el-form-item>
        <el-form-item><el-switch v-model="form.enabled" active-text="开启自动提醒" /></el-form-item>
        <p class="jc-small jc-muted">每天 09:00 检查；超过节点后提醒负责人及所选管理者，含合同查看入口。负责人反馈后按周期顺延；原表未收为零后停止提醒。</p>
        <el-button type="primary" plain size="small" :loading="saving" :disabled="!form.recipient_id || !form.due_on" @click="save">保存自动提醒</el-button>
      </el-form>
    </details>
  </section>
</template>
<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import api from '@/api/client'
const props=defineProps({data:Object,sheet:String,row:Number,revision:String,suggestedRecipient:Number,pendingRecipient:Number})
const emit=defineEmits(['saved']),form=reactive({}),saving=ref(false)
watch(()=>[props.sheet,props.row,props.data?.rule?.revision],()=>{const r=props.data?.rule;Object.assign(form,{recipient_id:r?.recipient_id||props.pendingRecipient||props.suggestedRecipient||null,cadence:r?.cadence||'weekly',due_on:r?.due_on||dayjs().add(1,'day').format('YYYY-MM-DD'),payment_due_on:r?.payment_due_on||null,manager_ids:[...(r?.manager_ids||[])],enabled:r?.enabled??true,expected_revision:r?.revision||0})},{immediate:true})
async function save(){if(saving.value)return;saving.value=true;try{await api.put(`/business-workbook/collections/items/${encodeURIComponent(props.sheet)}/${props.row}/reminder`,{...form,revision:props.revision});ElMessage.success('自动提醒安排已保存');emit('saved')}catch{}finally{saving.value=false}}
</script>
<style scoped>
.collection-reminder { border-top:1px solid var(--jc-line);padding-top:16px;margin-top:16px; }.collection-reminder h3 { font-size:14px;margin:0 0 8px; }.collection-reminder summary { cursor:pointer;color:var(--jc-brand);font-size:13px;line-height:1.7; }.reminder-form { margin-top:12px; }.reminder-form .el-select,.reminder-form .el-date-editor { width:100%; }.reminder-form p { line-height:1.7; }
</style>
