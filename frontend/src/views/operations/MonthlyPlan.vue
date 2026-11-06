<template>
  <div v-loading="loading">
    <div class="filters">
      <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" aria-label="计划月份" style="width: 160px" @change="load" />
      <el-select v-model="ownerId" placeholder="全部负责人" clearable filterable style="width: 170px"><el-option v-for="u in owners" :key="u.id" :label="u.name" :value="u.id" /></el-select>
      <el-select v-model="source" placeholder="全部任务来源" clearable style="width: 160px"><el-option v-for="(v,k) in TASK_SOURCES" :key="k" :label="v" :value="k" /></el-select>
      <el-checkbox v-model="mine">我负责的</el-checkbox><el-checkbox v-model="reviewMine">待我审核</el-checkbox>
      <el-button @click="load">刷新</el-button><el-button @click="summarize">让小聚汇总本月</el-button>
    </div>
    <div class="jc-grid c4 monthly-kpis">
      <Kpi label="有效任务" :value="stats.total" unit="项" :hint="`公司安排 ${stats.company} · 自行安排 ${stats.self}`" />
      <Kpi label="已完成" :value="stats.done" unit="项" hint="当前筛选中已生效任务的状态" />
      <Kpi label="未完成" :value="stats.pending" unit="项" :hint="`跨月事项 ${stats.carryover} 项`" />
      <Kpi label="待审核" :value="stats.awaiting" unit="项" :hint="`已退回 ${stats.rejected} 项`" />
    </div>
    <p class="jc-small jc-muted">{{ data.note }}</p>
    <el-table :data="rows" :row-class-name="({row}) => row.overdue ? 'is-alert' : ''" @row-click="t => emit('open-task', t)">
      <el-table-column label="负责人 / 项目" min-width="170"><template #default="{row}"><b>{{ row.owner_name }}</b><div class="jc-muted jc-small">{{ row.project_name }}</div></template></el-table-column>
      <el-table-column label="本月任务" width="130"><template #default="{row}">{{ TASK_SOURCES[row.source] }}<div v-if="row.carryover" class="jc-small jc-muted">从 {{ row.plan_month }} 延续</div><div v-if="row.approval_status !== 'approved'" class="jc-small jc-down">{{ APPROVAL_STATUS[row.approval_status] }}</div></template></el-table-column>
      <el-table-column label="具体事项" min-width="260"><template #default="{row}"><b>{{ row.title }}</b><p class="plain jc-small jc-muted">{{ row.latest_feedback || row.description || '尚未反馈进展' }}</p></template></el-table-column>
      <el-table-column label="本月完成情况（当前）" min-width="240"><template #default="{row}"><StatusPill v-if="row.approval_status === 'approved'" :text="row.completion" :tone="row.status === 'done' ? 'up' : 'warn'" /><StatusPill v-else :text="APPROVAL_STATUS[row.approval_status]" tone="info" /><p v-if="row.approval_status === 'approved' && row.status !== 'done'" class="plain jc-small">原因：{{ row.reason }}</p><p v-if="row.next_step" class="plain jc-small jc-muted">下一步：{{ row.next_step }}</p></template></el-table-column>
      <el-table-column prop="due_date" label="完成时间" width="115" />
      <el-table-column width="100"><template #default="{row}"><el-button link type="primary" @click.stop="emit('open-task',row)">{{ row.can_review ? '处理审核' : row.can_feedback ? '反馈进展' : '查看详情' }}</el-button></template></el-table-column>
      <template #empty><el-empty description="本月暂无匹配的任务，可进入项目布置任务或提交自行安排" /></template>
    </el-table>
  </div>
</template>
<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import dayjs from 'dayjs'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { APPROVAL_STATUS, TASK_SOURCES } from './shared'
const props = defineProps({ projectId: Number, today: String, refreshKey: Number })
const emit = defineEmits(['open-task'])
const auth = useAuth(), assistant = useAssistant(), month = ref((props.today || dayjs().format('YYYY-MM-DD')).slice(0,7))
const ownerId = ref(null), source = ref(''), mine = ref(false), reviewMine = ref(false), loading = ref(false)
const data = reactive({items:[],stats:{},source_counts:{},carryover_count:0,note:''})
const owners = computed(() => [...new Map(data.items.map(t => [t.owner_id,{id:t.owner_id,name:t.owner_name}])).values()])
const rows = computed(() => data.items.filter(t => (!ownerId.value || t.owner_id === ownerId.value) && (!source.value || t.source === source.value) &&
  (!mine.value || t.owner_id === auth.profile?.id) && (!reviewMine.value || t.can_review)).sort((a,b) => a.owner_id-b.owner_id || a.source.localeCompare(b.source)))
const stats=computed(()=>{const active=rows.value.filter(t=>t.approval_status==='approved');const done=active.filter(t=>t.status==='done').length;return {
  total:active.length,done,pending:active.length-done,company:active.filter(t=>t.source==='company').length,self:active.filter(t=>t.source==='self').length,
  carryover:rows.value.filter(t=>t.carryover).length,awaiting:rows.value.filter(t=>['department_pending','company_pending'].includes(t.approval_status)).length,
  rejected:rows.value.filter(t=>t.approval_status==='rejected').length}})
let version=0
async function load() { const current=++version; loading.value=true; try {const r=await api.get('/operations/monthly',{month:month.value,project_id:props.projectId}); if(current===version)Object.assign(data,r)} catch {} finally {if(current===version)loading.value=false} }
function summarize() { assistant.open(`请查询 ${month.value} 的运营月度任务表${props.projectId ? `，项目ID ${props.projectId}` : ''}${mine.value || ownerId.value ? `，负责人ID ${mine.value ? auth.profile.id : ownerId.value}` : ''}${source.value ? `，只看${TASK_SOURCES[source.value]}` : ''}${reviewMine.value ? '，只看待我审核的事项' : ''}，区分公司安排和自行安排，汇总完成情况、未完成原因、跨月事项和待审核事项。使用实际记录，没有反馈的明确说明。`) }
watch(() => props.refreshKey,load)
useBusinessRefresh(load)
onMounted(load)
defineExpose({load})
</script>
<style scoped>
.filters{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:0 0 18px}.plain{white-space:pre-wrap;line-height:1.6;margin:6px 0;word-break:break-word}
@media(max-width:560px){.monthly-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
