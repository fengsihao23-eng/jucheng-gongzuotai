<template>
  <div class="monthly" v-loading="loading && month !== data.month">
    <div class="toolbar">
      <div class="toolbar-group">
        <el-date-picker v-model="month" type="month" format="YYYY 年 M 月" value-format="YYYY-MM" :clearable="false" aria-label="计划月份" style="width: 144px" />
        <el-button plain :aria-expanded="filtersOpen" :aria-controls="filterId" @click="filtersOpen = !filtersOpen">筛选{{ filterCount ? ` (${filterCount})` : '' }}</el-button>
      </div>
      <div class="toolbar-group">
        <el-button link type="primary" @click="summarize">让小聚汇总</el-button>
        <slot name="actions" />
      </div>
    </div>
    <div v-show="filtersOpen" :id="filterId" class="filters">
      <el-select v-model="ownerId" aria-label="负责人" placeholder="全部负责人" clearable filterable :disabled="mine"><el-option v-for="u in owners" :key="u.id" :label="u.name" :value="u.id" /></el-select>
      <el-select v-model="source" aria-label="任务来源" placeholder="全部任务来源" clearable><el-option v-for="(v,k) in TASK_SOURCES" :key="k" :label="v" :value="k" /></el-select>
      <el-checkbox v-model="mine" @change="value => { if (value) ownerId = null }">我负责的</el-checkbox><el-checkbox v-model="reviewMine" @change="value => { if (value) delayMine = false }">待我审核</el-checkbox>
      <el-checkbox v-if="delayMine || data.items.some(task => task.can_review_delay)" v-model="delayMine" @change="value => { if (value) reviewMine = false }">待我复核延期</el-checkbox>
      <el-button v-if="filterCount" link type="primary" @click="clearFilters">清除筛选</el-button>
    </div>
    <p v-if="fetchError" class="fetch-error" role="status">暂时无法更新月度任务。<el-button link type="primary" @click="load()">重试</el-button></p>
    <TaskOverview :rows="rows" :title="`${month} 月度汇总`" monthly />
    <OwnerDeadlineTable :rows="rows" />
    <div v-if="rows.length" class="list-heading">
      <h3>任务明细 <span>{{ rows.length }} 项</span></h3>
      <el-popover v-if="data.note" :content="data.note" trigger="click" placement="bottom-end" :width="256"><template #reference><el-button link>统计说明</el-button></template></el-popover>
    </div>
    <el-table v-if="rows.length" class="monthly-table" :data="rows" :row-class-name="({row}) => row.overdue ? 'is-alert' : ''" @row-click="t => emit('open-task', t)" @row-contextmenu="taskMenu">
      <el-table-column label="负责人 / 项目" min-width="170"><template #default="{row}"><b>{{ row.owner_name }}</b><div class="jc-muted jc-small">{{ row.project_name }}</div></template></el-table-column>
      <el-table-column label="本月任务" width="130"><template #default="{row}">{{ TASK_SOURCES[row.source] }}<div v-if="row.carryover" class="jc-small jc-muted">从 {{ row.plan_month }} 延续</div><div v-if="row.approval_status !== 'approved'" class="jc-small jc-down">{{ APPROVAL_STATUS[row.approval_status] }}</div></template></el-table-column>
      <el-table-column label="具体事项" min-width="260"><template #default="{row}"><b>{{ row.title }}</b><p class="plain jc-small jc-muted">{{ row.latest_feedback || row.plan_context?.progress || row.description || '尚未反馈进展' }}</p></template></el-table-column>
      <el-table-column label="完成情况" min-width="215"><template #default="{row}">
        <div class="row-status"><StatusPill v-if="row.approval_status === 'approved'" :text="completionLabel(row)" :tone="row.status === 'done' ? deadlineTone(row) : 'warn'" /><StatusPill v-else :text="APPROVAL_STATUS[row.approval_status]" tone="info" />
          <b v-if="row.approval_status === 'approved' && row.status !== 'done' && (row.progress > 0 || row.last_feedback_at)" class="row-progress">{{ taskProgress(row).label }}</b></div>
        <p v-if="row.delay_status" class="plain jc-small jc-muted">{{ row.deadline_status === 'overdue' ? '原定期限已过 · ' : '' }}延期{{ DELAY_STATUS[row.delay_status] }}</p>
        <p v-if="row.approval_status === 'approved' && row.status !== 'done'" class="plain jc-small">原因：{{ row.reason }}</p><p v-if="row.next_step" class="plain jc-small jc-muted">下一步：{{ row.next_step }}</p>
      </template></el-table-column>
      <el-table-column label="日期" width="147"><template #default="{row}">
        <div class="date-cell"><span class="jc-muted jc-small">截止</span> {{ row.due_date }}</div>
        <div v-if="row.deadline_basis && row.deadline_basis !== row.due_date" class="date-cell jc-muted jc-small">原定 {{ row.deadline_basis }}</div>
        <div v-if="row.completed_on" class="date-cell"><span class="jc-muted jc-small">完成</span> {{ row.completed_on }}</div>
      </template></el-table-column>
      <el-table-column width="100"><template #default="{row}"><el-button link type="primary" @click.stop="emit('open-task',row)">{{ row.can_review_delay ? '复核延期' : row.can_review ? '处理审核' : row.can_feedback ? '反馈进展' : '查看详情' }}</el-button></template></el-table-column>
    </el-table>
    <TaskCards v-if="rows.length" class="monthly-cards" :rows="rows" :show-project="!projectId" single @open-task="t => emit('open-task', t)" />
    <div v-else-if="!loading && !fetchError" class="empty">
      <span>{{ data.items.length ? '没有符合筛选条件的任务' : '本月暂无任务' }}</span>
      <el-button v-if="data.items.length && filterCount" link type="primary" @click="clearFilters">清除筛选</el-button>
      <el-button v-else-if="suggestedMonth" link type="primary" @click="month = suggestedMonth">查看 {{ suggestedMonth }}</el-button>
    </div>
  </div>
</template>
<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import dayjs from 'dayjs'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import TaskCards from './TaskCards.vue'
import TaskOverview from './TaskOverview.vue'
import OwnerDeadlineTable from './OwnerDeadlineTable.vue'
import { APPROVAL_STATUS, DELAY_STATUS, TASK_SOURCES, completionLabel, deadlineTone, taskProgress } from './shared'
const props = defineProps({ projectId: Number, today: String, active: { type: Boolean, default: true } })
const emit = defineEmits(['open-task'])
const auth = useAuth(), assistant = useAssistant(), month = ref((props.today || dayjs().format('YYYY-MM-DD')).slice(0,7))
const ownerId = ref(null), source = ref(''), mine = ref(false), reviewMine = ref(false), delayMine = ref(false), loading = ref(false), fetchError = ref(false)
const filterId = useId(), filtersOpen = ref(false)
const filterCount = computed(() => [ownerId.value, source.value, mine.value, reviewMine.value, delayMine.value].filter(Boolean).length)
const data = reactive({ month: '', items: [], available_months: [], note: '' })
const suggestedMonth = computed(() => {
  const months = [...data.available_months].sort()
  return months.find(value => value > month.value) || months.filter(value => value < month.value).at(-1)
})
const owners = computed(() => [...new Map(data.items.map(t => [t.owner_id,{id:t.owner_id,name:t.owner_name}])).values()])
const rows = computed(() => (data.month === month.value ? data.items : []).filter(t => (mine.value ? t.owner_id === auth.profile?.id : !ownerId.value || t.owner_id === ownerId.value) &&
  (!source.value || t.source === source.value) && (!reviewMine.value || t.can_review) && (!delayMine.value || t.can_review_delay))
  .sort((a,b) => a.owner_id-b.owner_id || a.source.localeCompare(b.source)))
let version = 0
async function load(background = false) {
  if (!props.active || !month.value) return
  const current = ++version
  if (!background) loading.value = true
  try {
    const result = await api.get('/operations/monthly', { month: month.value, project_id: props.projectId }, { silent: true })
    if (current === version) { Object.assign(data, result); fetchError.value = false }
  } catch { if (current === version) fetchError.value = true }
  finally { if (current === version) loading.value = false }
}
function clearFilters() { ownerId.value = null; source.value = ''; mine.value = false; reviewMine.value = false; delayMine.value = false }
function summarize() { assistant.open(`请查询 ${month.value} 的运营月度任务表${props.projectId ? `，项目ID ${props.projectId}` : ''}${mine.value || ownerId.value ? `，负责人ID ${mine.value ? auth.profile.id : ownerId.value}` : ''}${source.value ? `，只看${TASK_SOURCES[source.value]}` : ''}${reviewMine.value ? '，只看待我审核的自拟任务' : ''}${delayMine.value ? '，只看待我复核的延期事项' : ''}。按负责人汇总任务数、按时完成、逾期完成和未完成，指出跨月和需要处理的事项。使用实际统计，延期认可不改变准时结果。`) }
watch(() => [props.active, props.projectId, month.value], () => load(), { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => props.active })
onBeforeUnmount(() => { version++ })
const taskMenu = rowDeletion(row => row.can_archive && recordRemoval('operation-task', row.id, row.title, () => load(), { title: '删除任务' }))
</script>
<style scoped>
.toolbar, .toolbar-group, .filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }.toolbar { justify-content: space-between; margin-bottom: 12px; }
.toolbar-group .el-button + .el-button, .filters .el-button + .el-button { margin: 0; }
.filters { margin: 0 0 12px; padding: 9px 10px; border-radius: 6px; background: var(--jc-surface-2); }.filters .el-select { width: 145px; }.filters .el-checkbox { margin-right: 8px; }
.plain { white-space: pre-wrap; line-height: 1.6; margin: 6px 0; word-break: break-word; }
.list-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 12px; margin: 17px 0 8px; }
.list-heading h3 { margin: 0; font-size: 15px; }.list-heading h3 span { margin-left: 6px; color: var(--jc-ink-3); font-size: 12px; font-weight: 400; }
.monthly-cards { display: none; }.monthly-table :deep(.el-table__row) { cursor: pointer; }
.date-cell { white-space: nowrap; }.row-status { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; }.row-progress { color: var(--jc-brand); font-variant-numeric: tabular-nums; font-size: 12px; }
.empty, .fetch-error { margin: 0; padding: 14px 0; color: var(--jc-ink-3); font-size: 13px; }.empty .el-button { margin-left: 10px; }.fetch-error { color: var(--jc-warn); }
@media (max-width: 900px) { .monthly-table { display: none; }.monthly-cards { display: grid; } }
@media (max-width: 500px) { .filters .el-select { width: calc(50% - 4px); }.toolbar-group:last-child { gap: 12px; } }
</style>
