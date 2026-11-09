<template>
  <div v-loading="loading && !data.month" class="monthly">
    <div class="toolbar">
      <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" aria-label="计划月份" class="month" />
      <el-button plain :aria-expanded="filtersOpen" :aria-controls="filterId" @click="filtersOpen = !filtersOpen">筛选<span v-if="filterCount">（{{ filterCount }}）</span></el-button>
      <el-button v-if="showAssistant" link type="primary" @click="summarize">让小聚汇总</el-button>
    </div>
    <div v-show="filtersOpen" :id="filterId" class="filters">
      <el-select v-model="ownerId" aria-label="负责人" placeholder="全部负责人" clearable filterable :disabled="mine"><el-option v-for="u in owners" :key="u.id" :label="u.name" :value="u.id" /></el-select>
      <el-select v-model="source" aria-label="任务来源" placeholder="全部任务来源" clearable><el-option v-for="(label, value) in TASK_SOURCES" :key="value" :label="label" :value="value" /></el-select>
      <el-checkbox v-model="mine" @change="value => { if (value) ownerId = null }">我负责的</el-checkbox>
      <el-checkbox v-model="reviewMine">待我审核</el-checkbox>
      <el-button v-if="filterCount" link type="primary" @click="clearFilters">清除筛选</el-button>
    </div>
    <div v-if="fetchError" class="notice" role="status">暂时无法获取最新任务。<el-button link type="primary" @click="load()">重试</el-button></div>
    <template v-if="rows.length">
      <TaskOverview :rows="rows" :title="`${month} 月度汇总`" />
      <TaskList :rows="rows" :show-project="!projectId" label="月度任务" monthly @open-task="task => emit('open-task', task)" />
    </template>
    <div v-else-if="!loading && !fetchError" class="empty">
      <span>{{ filterCount && data.items.length ? '没有符合筛选条件的任务' : '本月没有任务' }}</span>
      <el-button v-if="!data.items.length && suggestedMonth" link type="primary" @click="month = suggestedMonth">查看 {{ suggestedMonth }}</el-button>
      <el-button v-else-if="filterCount && data.items.length" link type="primary" @click="clearFilters">清除筛选</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import dayjs from 'dayjs'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import TaskList from './TaskList.vue'
import TaskOverview from './TaskOverview.vue'
import { orderTasks, TASK_SOURCES } from './shared'

const props = defineProps({ projectId: Number, today: String, active: { type: Boolean, default: true }, showAssistant: { type: Boolean, default: true } })
const emit = defineEmits(['open-task'])
const auth = useAuth(), assistant = useAssistant(), month = ref((props.today || dayjs().format('YYYY-MM-DD')).slice(0, 7))
const filterId = useId(), filtersOpen = ref(false)
const ownerId = ref(null), source = ref(''), mine = ref(false), reviewMine = ref(false), loading = ref(false), fetchError = ref(false)
const data = reactive({ month: '', items: [], available_months: [] })
const filterCount = computed(() => [ownerId.value, source.value, mine.value, reviewMine.value].filter(Boolean).length)
const suggestedMonth = computed(() => {
  const months = [...data.available_months].sort()
  return months.find(value => value > month.value) || months.filter(value => value < month.value).at(-1)
})
const owners = computed(() => [...new Map(data.items.map(t => [t.owner_id, { id: t.owner_id, name: t.owner_name }])).values()])
const rows = computed(() => orderTasks((data.month === month.value ? data.items : []).filter(t =>
  (mine.value ? t.owner_id === auth.profile?.id : !ownerId.value || t.owner_id === ownerId.value) &&
  (!source.value || t.source === source.value) && (!reviewMine.value || t.can_review))))
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
function clearFilters() { ownerId.value = null; source.value = ''; mine.value = false; reviewMine.value = false }
function summarize() {
  const owner = mine.value ? auth.profile?.id : ownerId.value
  assistant.open(`请查询 ${month.value} 的运营月度任务表${props.projectId ? `，项目ID ${props.projectId}` : ''}${owner ? `，负责人ID ${owner}` : ''}${source.value ? `，只看${TASK_SOURCES[source.value]}` : ''}${reviewMine.value ? '，只看待我审核的事项' : ''}，用简短中文说明哪些已完成、哪些需要跟进。请根据实际记录回答。`)
}
defineExpose({ summarize })
watch(() => [props.active, props.projectId, month.value], () => load(), { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => props.active })
onBeforeUnmount(() => { version++ })
</script>

<style scoped>
.toolbar, .filters { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }.toolbar .el-button + .el-button { margin: 0; }
.toolbar .month { --el-date-editor-width: 138px; }.filters { margin: 10px 0 4px; padding: 10px 12px; background: var(--jc-surface-2); border-radius: 6px; }
.filters .el-select { width: 145px; }.filters .el-checkbox { margin-right: 8px; }
.empty, .notice { padding: 18px 0 10px; color: var(--jc-ink-3); font-size: 14px; }.empty .el-button { margin-left: 10px; }.notice { color: var(--jc-warn); }
@media (max-width: 480px) { .filters .el-select { width: calc(50% - 4px); } }
</style>
