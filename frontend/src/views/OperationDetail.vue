<template>
  <div class="jc-page operation-detail" v-loading="loading">
    <router-link class="back" to="/operations">← 运营项目</router-link>
    <template v-if="project">
      <PageHeader class="project-heading" :title="project.name" :desc="project.objective || undefined">
        <template #meta>
          <div class="project-meta">
            <StatusPill :text="PROJECT_STATUS[project.status]" :tone="project.status === 'active' ? 'brand' : project.status === 'completed' ? 'up' : 'info'" />
            <span>负责人 {{ project.owner_name }}</span><span v-if="project.target_date">目标日期 {{ project.target_date }}</span>
          </div>
        </template>
        <el-button text @click="ask">问小聚</el-button>
        <el-dropdown trigger="click" @command="handleAction">
          <el-button text>更多<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="canAssign && canPropose" command="self">自行安排</el-dropdown-item>
              <el-dropdown-item v-if="project.latest_progress" command="history">进度记录</el-dropdown-item>
              <el-dropdown-item command="copy">复制进展汇总</el-dropdown-item>
              <el-dropdown-item v-if="project.can_manage" command="settings">项目设置</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button v-if="canAssign" type="primary" @click="newTask('company')">布置任务</el-button>
        <el-button v-else-if="canPropose" type="primary" @click="newTask('self')">自行安排</el-button>
      </PageHeader>

      <div v-if="fetchError" class="notice" role="status">暂时无法获取最新进展。<el-button link type="primary" @click="load()">重试</el-button></div>
      <el-tabs v-model="section" class="project-tabs">
        <el-tab-pane label="概览" name="overview">
          <div class="overview-layout">
            <ProjectProgress :key="project.id" ref="progressPanel" :project="project" @saved="progressSaved" />
            <section class="task-preview surface" aria-label="任务进展">
              <header class="preview-header">
                <h2>任务进展</h2>
                <el-button v-if="project.tasks.length" link type="primary" @click="showTasks(false)">查看全部</el-button>
              </header>
              <div v-if="project.tasks.length" class="preview-summary">
                <span v-if="counts.total">已完成 <strong>{{ counts.done }} / {{ counts.total }}</strong> 项</span>
                <el-button v-if="attentionCount" link type="warning" @click="showTasks(true)">{{ attentionCount }} 项需关注</el-button>
              </div>
              <TaskList v-if="previewTasks.length" :rows="previewTasks" label="当前任务" compact @open-task="openTask" />
              <p v-else class="empty">还没有任务</p>
            </section>
          </div>
        </el-tab-pane>

        <el-tab-pane label="任务" name="tasks" lazy>
          <section class="surface work-panel" aria-label="项目任务">
            <div class="task-toolbar">
              <div class="view-switch" role="group" aria-label="任务视图">
                <button type="button" :aria-pressed="view === 'tasks'" @click="navigate('tasks')">当前任务</button>
                <button type="button" :aria-pressed="view === 'monthly'" @click="navigate('monthly')">月度任务</button>
              </div>
              <el-button v-if="view !== 'monthly'" plain :aria-expanded="filtersOpen" :aria-controls="filterId" @click="filtersOpen = !filtersOpen">筛选<span v-if="filterCount">（{{ filterCount }}）</span></el-button>
            </div>
            <div v-show="view === 'tasks'">
              <div v-show="filtersOpen" :id="filterId" class="filters">
                <el-input v-model="keyword" aria-label="搜索任务" placeholder="任务、学校或负责人" clearable />
                <el-select v-model="status" aria-label="任务状态"><el-option v-for="(label, value) in taskFilters" :key="value" :label="label" :value="value" /></el-select>
                <el-checkbox v-model="mine">我负责的</el-checkbox><el-checkbox v-model="keyOnly">仅重点</el-checkbox>
                <el-button v-if="filterCount" link type="primary" @click="clearFilters">清除筛选</el-button>
              </div>
              <TaskList v-if="filteredTasks.length" :rows="filteredTasks" @open-task="openTask" />
              <p v-else class="empty">{{ project.tasks.length ? '没有符合筛选条件的任务' : '还没有任务' }}</p>
            </div>
            <MonthlyPlan v-if="monthlyMounted" v-show="view === 'monthly'" :key="project.id" ref="monthlyPanel" :project-id="project.id" :today="project.today" :active="view === 'monthly'" :show-assistant="false" @open-task="openTask" />
          </section>
        </el-tab-pane>

        <el-tab-pane label="项目资料" name="materials" lazy>
          <section class="surface work-panel" aria-label="项目资料">
            <el-tabs v-model="materialTab" class="material-tabs">
              <el-tab-pane label="学校与经营资料" name="schools" lazy><SchoolData :project="project" @saved="load" @open-task="openTask" /></el-tab-pane>
              <el-tab-pane label="设备产品库" name="products" lazy><ProductLibrary :project="project" @saved="load" /></el-tab-pane>
              <el-tab-pane label="项目团队" name="team" lazy><TeamMembers :project="project" @saved="load" /></el-tab-pane>
            </el-tabs>
          </section>
        </el-tab-pane>
      </el-tabs>

      <ProjectForm v-model="projectForm" :initial="project" @saved="load" />
      <TaskForm v-model="taskForm" :project="project" :initial="editingTask" :mode="taskMode" @saved="task => pendingTask = task" @closed="taskFormClosed" />
      <TaskDialog v-model="taskOpen" :task-id="selectedTask" :project-id="project.id" @edit="editTask" />
    </template>
    <el-empty v-else-if="!loading" description="无法打开运营项目"><el-button @click="load()">重试</el-button></el-empty>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, useId, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import ProjectForm from './operations/ProjectForm.vue'
import ProjectProgress from './operations/ProjectProgress.vue'
import TaskForm from './operations/TaskForm.vue'
import TaskDialog from './operations/TaskDialog.vue'
import TaskList from './operations/TaskList.vue'
import SchoolData from './operations/SchoolData.vue'
import ProductLibrary from './operations/ProductLibrary.vue'
import TeamMembers from './operations/TeamMembers.vue'
import MonthlyPlan from './operations/MonthlyPlan.vue'
import { PROJECT_STATUS, TASK_STATUS, orderTasks, taskAttention, taskStatus, taskSummary } from './operations/shared'

const route = useRoute(), router = useRouter(), auth = useAuth(), assistant = useAssistant()
const project = ref(null), loading = ref(false), fetchError = ref(false), materialTab = ref('schools')
const progressPanel = ref(), monthlyPanel = ref(), monthlyMounted = ref(false)
const filtersOpen = ref(false), keyword = ref(''), status = ref('all'), mine = ref(false), keyOnly = ref(false), filterId = useId()
const taskMode = ref('company'), projectForm = ref(false), taskForm = ref(false), editingTask = ref(null), pendingTask = ref(null), popupPaused = ref(false)
const taskFilters = { all: '全部状态', attention: '需关注', pending: '未完成', ...TASK_STATUS, review: '待审核', rejected: '已退回' }
const view = computed(() => ['tasks', 'monthly', 'materials'].includes(route.query.view) ? route.query.view : 'overview')
const section = computed({ get: () => view.value === 'monthly' ? 'tasks' : view.value, set: navigate })
const canAssign = computed(() => project.value?.can_manage && project.value?.status === 'active')
const canPropose = computed(() => project.value?.can_propose && project.value?.status === 'active')
const selectedTask = computed(() => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined)
const taskOpen = computed({ get: () => Boolean(selectedTask.value) && !popupPaused.value, set: value => { if (!value && !popupPaused.value) closeTask() } })
const orderedTasks = computed(() => orderTasks(project.value?.tasks || []))
const counts = computed(() => taskSummary(orderedTasks.value))
const attentionCount = computed(() => orderedTasks.value.filter(taskAttention).length)
const previewTasks = computed(() => {
  const pending = orderedTasks.value.filter(task => taskStatus(task) !== 'done')
  return (pending.length ? pending : orderedTasks.value).slice(0, 4)
})
const filterCount = computed(() => [keyword.value.trim(), status.value !== 'all', mine.value, keyOnly.value].filter(Boolean).length)
const filteredTasks = computed(() => orderedTasks.value.filter(task => {
  const state = taskStatus(task)
  return (!mine.value || task.owner_id === auth.profile?.id) && (!keyOnly.value || task.priority === 'key') &&
    (status.value === 'all' || (status.value === 'attention' ? taskAttention(task) : status.value === 'pending' ? ['todo', 'doing', 'blocked'].includes(state) : state === status.value)) &&
    `${task.title} ${task.owner_name} ${task.school_name || ''}`.toLowerCase().includes(keyword.value.trim().toLowerCase())
}))

let version = 0
async function load(background = false) {
  const current = ++version, id = route.params.id
  loading.value = !project.value
  try {
    const result = await api.get(`/operations/${id}`, null, { silent: background === true })
    if (current === version) { project.value = result; fetchError.value = false }
  } catch (err) {
    if (current === version) {
      fetchError.value = true
      if ([40301, 40401].includes(err.code)) project.value = null
    }
  } finally { if (current === version) loading.value = false }
}
function navigate(target) {
  if (target === view.value) return
  const query = { ...route.query }
  if (target === 'overview') delete query.view
  else query.view = target
  router.replace({ query })
}
function clearFilters() { keyword.value = ''; status.value = 'all'; mine.value = false; keyOnly.value = false }
function showTasks(onlyAttention) {
  clearFilters(); filtersOpen.value = onlyAttention
  if (onlyAttention) status.value = 'attention'
  navigate('tasks')
}
function newTask(mode) { taskMode.value = mode; editingTask.value = null; taskForm.value = true }
function editTask(task) { editingTask.value = task; popupPaused.value = true; taskForm.value = true }
function openTask(task) { router.replace({ query: { ...route.query, task: task.id } }) }
function closeTask() { const { task, ...query } = route.query; if (task) router.replace({ query }) }
function taskFormClosed() { popupPaused.value = false; if (pendingTask.value) { openTask(pendingTask.value); pendingTask.value = null } }
function progressSaved(progress) { if (project.value?.id === progress.project_id) project.value = { ...project.value, latest_progress: progress } }
async function handleAction(action) {
  if (action === 'self') newTask('self')
  else if (action === 'settings') projectForm.value = true
  else if (action === 'history') progressPanel.value?.openHistory()
  else if (action === 'copy') {
    try {
      const brief = await api.get(`/operations/${project.value.id}/brief`)
      await navigator.clipboard.writeText(brief.text)
      ElMessage.success('汇总已复制')
    } catch { ElMessage.info('暂时无法复制汇总，请重试') }
  }
}
function ask() {
  if (view.value === 'monthly' && monthlyPanel.value) return monthlyPanel.value.summarize()
  assistant.open(`请查看运营项目「${project.value.name}」（项目 ID：${project.value.id}），按最新进展、需要关注的问题和下一步三点简短汇报。只使用实际记录。`)
}
watch(() => route.params.id, id => {
  if (!id) return
  project.value = null; taskForm.value = false; pendingTask.value = null; popupPaused.value = false
  materialTab.value = 'schools'; clearFilters(); filtersOpen.value = false; monthlyMounted.value = view.value === 'monthly'
  load()
}, { immediate: true })
watch(view, value => { if (value === 'monthly') monthlyMounted.value = true }, { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => Boolean(route.params.id) })
onBeforeUnmount(() => { version++ })
</script>

<style scoped>
.operation-detail { max-width: 1320px; }
.back { display: inline-block; margin-bottom: 10px; color: var(--jc-ink-2); font-size: 13px; text-decoration: none; }.back:hover { color: var(--jc-brand); }
.project-heading { margin-bottom: 13px; }.project-heading :deep(h1) { font-size: clamp(25px, 2.4vw, 34px); line-height: 1.25; overflow-wrap: anywhere; }.project-heading :deep(.l) { min-width: 0; }.project-heading :deep(.el-button + .el-button) { margin-left: 0; }
.project-meta { display: flex; align-items: center; gap: 6px 12px; flex-wrap: wrap; margin-top: 9px; color: var(--jc-ink-2); font-size: 13px; }.project-meta :deep(.jc-pill) { font-size: 11px; }
.project-tabs > :deep(.el-tabs__header) { margin-bottom: 16px; }
.overview-layout { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(300px, 1fr); gap: 14px; align-items: start; }
.surface { min-width: 0; padding: 19px 21px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); }
.preview-header, .preview-summary, .task-toolbar, .filters { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }.preview-header, .task-toolbar { justify-content: space-between; }
.preview-header h2 { margin: 0; color: var(--jc-ink); font-size: 18px; }.preview-summary { margin: 8px 0 3px; color: var(--jc-ink-2); font-size: 13px; }.preview-summary strong { color: var(--jc-ink); }
.preview-summary .el-button { margin: 0; }.preview-summary:not(:empty) { padding-bottom: 6px; border-bottom: 1px solid var(--jc-line-2); }
.empty { margin: 16px 0 4px; color: var(--jc-ink-3); font-size: 14px; }.notice { margin: 6px 0; color: var(--jc-warn); font-size: 13px; }
.task-toolbar { margin-bottom: 10px; }.view-switch { display: inline-flex; gap: 2px; padding: 3px; border-radius: 7px; background: var(--jc-surface-2); }
.view-switch button { min-height: 28px; border: 1px solid transparent; border-radius: 5px; padding: 3px 10px; background: transparent; color: var(--jc-ink-2); cursor: pointer; font: inherit; font-size: 13px; }.view-switch button[aria-pressed='true'] { border-color: var(--jc-line); color: var(--jc-brand); background: var(--jc-surface); }
.filters { margin-bottom: 5px; padding: 10px 12px; border-radius: 6px; background: var(--jc-surface-2); }.filters .el-input { width: 194px; }.filters .el-select { width: 126px; }.filters .el-checkbox { margin-right: 6px; }
.material-tabs :deep(.el-tabs__header) { margin-bottom: 14px; }
@media (max-width: 1020px) { .overview-layout { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 600px) { .surface { padding: 14px; }.project-heading { gap: 10px; }.filters .el-input { width: 100%; }.project-tabs > :deep(.el-tabs__header) { margin-bottom: 12px; } }
</style>
