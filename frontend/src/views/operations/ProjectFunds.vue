<template>
  <div v-loading="loading" v-if="data"><p class="jc-muted">{{ data.note }}</p><template v-if="data.linked"><div class="jc-grid c2"><Kpi label="已审批归集金额" :value="money(data.approved_amount)" unit="元" /><Kpi label="已标记付款金额" :value="money(data.paid_amount)" unit="元" /></div><p class="jc-small jc-muted">关联业务项目：{{ data.project_code }}</p><el-table :data="data.items"><el-table-column prop="approval_no" label="企微审批单号" min-width="200" /><el-table-column prop="applicant" label="申请人" width="110" /><el-table-column prop="date" label="发生日期" width="120" /><el-table-column prop="reason" label="用途" min-width="240" /><el-table-column label="金额" width="140"><template #default="{row}">{{ money(row.amount) }}</template></el-table-column><el-table-column label="状态" width="140"><template #default="{row}">{{ statuses[row.status] }} · {{ row.pay_status ? '已付' : '未付' }}</template></el-table-column></el-table></template></div>
  <p v-else-if="error" class="jc-muted">{{ error }}</p>
</template>
<script setup>
import { ref, watch } from 'vue'
import api from '@/api/client'
import { money } from '@/utils/format'
const props=defineProps({projectId:Number,active:Boolean}),data=ref(null),loading=ref(false),error=ref('')
const statuses={1:'审批中',2:'待归集',3:'已驳回',4:'已归集'}
watch(()=>[props.projectId,props.active],async()=>{if(!props.active)return;loading.value=true;try{data.value=await api.get(`/operations/${props.projectId}/funds`,null,{silent:true});error.value=''}catch(e){data.value=null;error.value=e.message}finally{loading.value=false}},{immediate:true})
</script>
