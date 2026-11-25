<template>
  <div class="jc-page" v-loading="loading">
    <el-button link @click="router.push(auth.hasMenu('market') ? '/market?tab=key' : '/projects')">← 重点项目</el-button>
    <template v-if="project">
      <PageHeader v-record-delete="projectRemoval" eyebrow="重点项目" :title="project.name" :desc="project.content || '按任务和节点推进项目，记录实际进展。'">
        <el-button v-if="project.can_manage" @click="projectForm=true">编辑项目</el-button><el-button v-if="editable" type="primary" @click="taskBoard?.editTask()">安排任务</el-button>
        <KeyProjectPauseButton :project="project" @changed="load()" />
        <el-button v-if="projectRemoval" plain type="danger" @click="deleteRecord(projectRemoval)">删除项目</el-button>
        <RecordFeedbackButton :target="{ kind: 'key-project', id: project.project_code }" :heading="project.name"
          progress-hint="说明关键事项、节点进展或需要协调的问题。" />
      </PageHeader>
      <div class="project-meta jc-small jc-muted"><span>负责人 {{ project.owner_name || '未登记' }}</span><span>准备 {{ project.start_date || '未登记' }}</span><span>完成节点 {{ project.plan_end_date || '未登记' }}</span><StatusPill :text="project.paused?'已暂停跟进':project.status_text" :tone="project.paused?'warn':'brand'" /></div>
      <div class="jc-grid c4 mt-16"><Kpi label="项目任务" :value="project.tasks.length" :hint="`已完成 ${project.done_nodes} 项`" /><Kpi label="需要关注" :value="project.tasks.filter(t=>t.alert&&t.status!==3).length" hint="逾期、问题或待填写进展" /><Kpi label="合同金额" :value="wanNum(project.money.contract_total)" unit="万" /><Kpi label="累计回款" :value="wanNum(project.money.received)" unit="万" :hint="`未收 ${wan(project.money.receivable)}`" /></div>
      <Panel class="mt-16">
        <el-tabs v-model="tab">
          <el-tab-pane label="任务情况" name="tasks">
            <KeyTaskBoard :key="project.project_code" ref="taskBoard" v-model:task-id="taskId" :project="project" @refresh="load()" />
          </el-tab-pane>
          <el-tab-pane label="开票回款" name="financials">
            <div class="financial-period"><el-date-picker v-model="period" type="month" value-format="YYYY-MM" :clearable="false" aria-label="项目开票回款月份" @change="load()" /><span class="jc-small jc-muted">{{ project.financials.note }}</span></div>
            <div class="jc-grid c2"><Kpi :label="period===currentMonth?'当月开票':`${period} 开票`" :value="money(project.financials.invoiced)" unit="元" /><Kpi :label="period===currentMonth?'当月到账':`${period} 到账`" :value="money(project.financials.received)" unit="元" /></div>
            <h3 class="mt-16">开票清单</h3><el-table :data="project.financials.invoices" empty-text="此月份没有已确认的开票记录"><el-table-column prop="invoice_no" label="发票号码" min-width="150" /><el-table-column prop="issued_on" label="开票日期" width="115" /><el-table-column prop="contract_no" label="合同编号" min-width="145" /><el-table-column prop="customer" label="客户" min-width="160" /><el-table-column label="有效开票金额（元）" min-width="150" align="right"><template #default="{row}">{{ money(row.amount) }}</template></el-table-column></el-table>
            <h3 class="mt-16">到账清单</h3><el-table :data="project.financials.payments" empty-text="此月份没有已确认的到账记录"><el-table-column prop="payment_date" label="到账日期" width="120" /><el-table-column prop="contract_no" label="合同编号" min-width="145" /><el-table-column label="到账金额（元）" min-width="140" align="right"><template #default="{row}">{{ money(row.amount) }}</template></el-table-column></el-table>
          </el-tab-pane>
          <el-tab-pane label="跟进记录" name="history"><article v-for="f in project.feedbacks" :key="f.id" class="feedback-record"><small class="jc-muted">{{ f.feedback_date }} · {{ f.reporter_name }}</small><p :class="f.kind==='risk'?'jc-down':''">{{ f.content }}</p></article><el-empty v-if="!project.feedbacks.length" description="尚无跟进记录" /></el-tab-pane>
        </el-tabs>
      </Panel>
      <KeyProjectForm v-model="projectForm" :initial="project" @saved="load()" />
    </template><el-empty v-else-if="!loading" description="无法打开重点项目，请检查权限或重试"><el-button @click="load()">重试</el-button></el-empty>
  </div>
</template>
<script setup>
import { deleteRecord } from '@/composables/useRecordDeletion'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import api from '@/api/client'
import RecordFeedbackButton from '@/components/records/RecordFeedbackButton.vue'
import { useAuth } from '@/store/auth'
import { money, wan, wanNum } from '@/utils/format'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import KeyProjectForm from './market/KeyProjectForm.vue'
import KeyProjectPauseButton from './market/KeyProjectPauseButton.vue'
import KeyTaskBoard from './market/KeyTaskBoard.vue'
const route = useRoute(), router = useRouter(), auth = useAuth()
const project = ref(null), loading = ref(false), projectForm = ref(false), taskBoard = ref(null), tab = ref('tasks')
const currentMonth = dayjs().format('YYYY-MM'), period = ref(currentMonth)
const editable = computed(() => project.value?.can_manage && !project.value?.paused && project.value?.status !== 5)
const taskId = computed({
  get: () => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined,
  set: id => router.replace({ query: { ...route.query, task: id } }),
})
let version=0
async function load(quiet=false){const v=++version;if(!quiet&&!project.value)loading.value=true;try{const r=await api.get(`/market/projects/${route.params.code}`,{period:period.value},{silent:quiet});if(v===version)project.value=r}catch(e){if(v===version&&(!quiet||[40301,40401].includes(e.code)))project.value=null}finally{if(v===version)loading.value=false}}
const projectRemoval = computed(() => project.value?.can_delete && {
  endpoint: '/market/projects/' + project.value.project_code, title: '删除重点项目', name: project.value.name,
  onDeleted: () => router.push(auth.hasMenu('market') ? '/market?tab=key' : '/projects'),
})
watch(()=>route.params.code,()=>{project.value=null;tab.value='tasks';load()},{immediate:true})
useBusinessRefresh(()=>load(true),{interval:12000,enabled:()=>!projectForm.value})
</script>
<style scoped>
.project-meta{display:flex;gap:16px;align-items:center;flex-wrap:wrap;}.financial-period{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:16px;}.feedback-record{border-bottom:1px solid var(--jc-line);padding:14px 0;}.feedback-record p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;}@media(max-width:600px){.jc-grid.c4{grid-template-columns:repeat(2,minmax(0,1fr));}}
</style>
