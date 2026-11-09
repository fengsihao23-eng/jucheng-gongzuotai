<template>
  <div class="monthly" v-loading="loading && !data.month">
    <div class="filters">
      <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" aria-label="计划月份" style="width: 160px" />
      <el-select v-model="ownerId" placeholder="全部负责人" clearable filterable :disabled="mine" style="width: 170px"><el-option v-for="u in owners" :key="u.id" :label="u.name" :value="u.id" /></el-select>
      <el-select v-model="source" placeholder="全部任务来源" clearable style="width: 160px"><el-option v-for="(v,k) in TASK_SOURCES" :key="k" :label="v" :value="k" /></el-select>
      <el-checkbox v-model="mine">我负责的</el-checkbox><el-checkbox v-model="reviewMine">待我审核</el-checkbox>
      <el-button :loading="loading" @click="load()">刷新</el-button><el-button @click="summarize">让小聚汇总本月</el-button>
      <slot name="actions" />
    </div>
    <TaskOverview :rows="rows" :title="`${month} 月度汇总`" :caption="fetchError ? '暂时无法同步，可点击刷新重试。' : '按当前筛选汇总，显示任务最新状态。'" :updated-at="updatedAt" monthly />
    <div class="list-heading"><h3>任务明细 <span>{{ rows.length }} 项</span></h3><span class="jc-small jc-muted">{{ data.note }}</span></div>
    <el-table v-if="rows.length" class="monthly-table" :data="rows" :row-class-name="({row}) => row.overdue ? 'is-alert' : ''" @row-click="t => emit('open-task', t)">
      <el-table-column label="负责人 / 项目" min-width="170"><template #default="{row}"><b>{{ row.owner_name }}</b><div class="jc-muted jc-small">{{ row.project_name }}</div></template></el-table-column>
      <el-table-column label="本月任务" width="130"><template #default="{row}">{{ TASK_SOURCES[row.source] }}<div v-if="row.carryover" class="jc-small jc-muted">从 {{ row.plan_month }} 延续</div><div v-if="row.approval_status !== 'approved'" class="jc-small jc-down">{{ APPROVAL_STATUS[row.approval_status] }}</div></template></el-table-column>
      <el-table-column label="具体事项" min-width="260"><template #default="{row}"><b>{{ row.title }}</b><p class="plain jc-small jc-muted">{{ row.latest_feedback || row.description || '尚未反馈进展' }}</p></template></el-table-column>
      <el-table-column label="本月完成情况（当前）" min-width="240"><template #default="{row}"><StatusPill v-if="row.approval_status === 'approved'" :text="row.completion" :tone="row.status === 'done' ? 'up' : 'warn'" /><StatusPill v-else :text="APPROVAL_STATUS[row.approval_status]" tone="info" /><p v-if="row.approval_status === 'approved' && row.status !== 'done'" class="plain jc-small">原因：{{ row.reason }}</p><p v-if="row.next_step" class="plain jc-small jc-muted">下一步：{{ row.next_step }}</p></template></el-table-column>
      <el-table-column label="日期" width="120"><template #default="{row}"><span class="jc-muted jc-small">{{ row.completed_at ? '完成' : '截止' }}</span><div class="date-cell">{{ row.completed_at ? date(row.completed_at) : row.due_date }}</div></template></el-table-column>
      <el-table-column width="100"><template #default="{row}"><el-button link type="primary" @click.stop="emit('open-task',row)">{{ row.can_review ? '处理审核' : row.can_feedback ? '反馈进展' : '查看详情' }}</el-button></template></el-table-column>
    </el-table>
    <TaskCards v-if="rows.length" class="monthly-cards" :rows="rows" :show-project="!projectId" single @open-task="t => emit('open-task', t)" />
    <div v-else-if="!loading" class="empty">
      <b>{{ fetchError ? '月度任务加载失败' : '本月暂无匹配的任务' }}</b>
      <p v-if="!fetchError">{{ data.items.length ? '可以调整负责人、任务来源等筛选条件。' : '可以切换计划月份，或进入项目布置任务。' }}</p>
      <div v-if="!data.items.length && otherMonths.length" class="months"><el-button v-for="value in otherMonths" :key="value" size="small" @click="month = value">查看 {{ value }}</el-button></div>
    </div>
  </div>
</template>
<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import dayjs from 'dayjs'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { date } from '@/utils/format'
import TaskCards from './TaskCards.vue'
import TaskOverview from './TaskOverview.vue'
import { APPROVAL_STATUS, TASK_SOURCES } from './shared'
const props = defineProps({ projectId: Number, today: String, active: { type: Boolean, default: true } })
const emit = defineEmits(['open-task'])
const auth = useAuth(), assistant = useAssistant(), month = ref((props.today || dayjs().format('YYYY-MM-DD')).slice(0,7))
const ownerId = ref(null), source = ref(''), mine = ref(false), reviewMine = ref(false), loading = ref(false), fetchError = ref(false), updatedAt = ref('')
const data = reactive({ month: '', items: [], available_months: [], note: '' })
const otherMonths = computed(() => data.available_months.filter(value => value !== month.value).sort().slice(-6).reverse())
const owners = computed(() => [...new Map(data.items.map(t => [t.owner_id,{id:t.owner_id,name:t.owner_name}])).values()])
const rows = computed(() => (data.month === month.value ? data.items : []).filter(t => (mine.value ? t.owner_id === auth.profile?.id : !ownerId.value || t.owner_id === ownerId.value) &&
  (!source.value || t.source === source.value) && (!reviewMine.value || t.can_review)).sort((a,b) => a.owner_id-b.owner_id || a.source.localeCompare(b.source)))
const clock = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', timeStyle: 'medium', hour12: false })
let version = 0
async function load(background = false) {
  if (!props.active || !month.value) return
  const current = ++version
  if (!background) loading.value = true
  try {
    const result = await api.get('/operations/monthly', { month: month.value, project_id: props.projectId }, { silent: background })
    if (current === version) { Object.assign(data, result); updatedAt.value = clock.format(new Date()); fetchError.value = false }
  } catch { if (current === version) fetchError.value = true }
  finally { if (current === version) loading.value = false }
}
function summarize() { assistant.open(`请查询 ${month.value} 的运营月度任务表${props.projectId ? `，项目ID ${props.projectId}` : ''}${mine.value || ownerId.value ? `，负责人ID ${mine.value ? auth.profile.id : ownerId.value}` : ''}${source.value ? `，只看${TASK_SOURCES[source.value]}` : ''}${reviewMine.value ? '，只看待我审核的事项' : ''}，区分公司安排和自行安排，汇总完成情况、未完成原因、跨月事项和待审核事项。使用实际记录，没有反馈的明确说明。`) }
watch(() => [props.active, props.projectId, month.value], () => load(), { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => props.active })
onBeforeUnmount(() => { version++ })
</script>
<style scoped>
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 0 0 16px; }
.plain { white-space: pre-wrap; line-height: 1.6; margin: 6px 0; word-break: break-word; }
.list-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 12px; margin: 17px 0 8px; }
.list-heading h3 { margin: 0; font-size: 15px; }.list-heading h3 span { margin-left: 6px; color: var(--jc-ink-3); font-size: 12px; font-weight: 400; }
.monthly-cards { display: none; }.monthly-table :deep(.el-table__row) { cursor: pointer; }
.date-cell { white-space: nowrap; }
.empty { text-align: center; padding: 22px 12px; background: var(--jc-bg-2, #f9f8f5); border: 1px dashed var(--jc-line); border-radius: var(--jc-radius); color: var(--jc-ink-2); }
.empty p { margin: 5px 0 0; font-size: 12px; }.months { display: flex; justify-content: center; gap: 6px; flex-wrap: wrap; margin-top: 10px; }.months .el-button + .el-button { margin: 0; }
@media (max-width: 900px) { .monthly-table { display: none; }.monthly-cards { display: grid; } }
@media (max-width: 560px) { .filters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }.filters :deep(.el-date-editor), .filters :deep(.el-select) { width: 100% !important; min-width: 0; }.filters .el-button { margin: 0; }.filters .el-button:last-child { grid-column: 1 / -1; }.filters .el-checkbox { margin: 0; } }
</style>
