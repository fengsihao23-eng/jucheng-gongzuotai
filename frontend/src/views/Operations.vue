<template>
  <div class="jc-page operations">
    <PageHeader class="operations-heading" title="运营项目">
      <el-button v-if="data.can_create" type="primary" @click="createOpen = true">新建运营项目</el-button>
    </PageHeader>
    <el-tabs v-model="tab" class="operations-tabs">
      <el-tab-pane label="项目" name="projects">
        <section v-loading="loading" class="surface" aria-label="项目列表">
          <div v-if="data.items.length > 1 || filterOpen || hasFilter" class="toolbar">
            <el-input v-model="query.keyword" aria-label="搜索项目" placeholder="搜索项目" clearable @change="load()" />
            <el-button plain :aria-expanded="filterOpen" :aria-controls="filterId" @click="filterOpen = !filterOpen">筛选<span v-if="query.category || query.key_only">（{{ Number(!!query.category) + Number(query.key_only) }}）</span></el-button>
          </div>
          <div v-show="filterOpen" :id="filterId" class="filters">
            <el-select v-model="query.category" aria-label="项目分类" placeholder="全部分类" clearable @change="load()"><el-option v-for="category in categories" :key="category" :value="category" :label="category" /></el-select>
            <el-checkbox v-model="query.key_only" @change="load()">仅重点项目</el-checkbox>
            <el-button v-if="hasFilter" link type="primary" @click="clearFilters">清除筛选</el-button>
          </div>
          <ul v-if="data.items.length" class="project-list">
            <li v-for="project in data.items" :key="project.id">
              <router-link :to="`/operations/${project.id}`" class="project-row">
                <div class="project-title"><strong>{{ project.name }}</strong><span>{{ [project.category === project.name ? '' : project.category, project.owner_name].filter(Boolean).join(' · ') }}</span></div>
                <div class="project-state">
                  <span :class="{ attention: project.stats.overdue || project.stats.blocked }">{{ projectStatus(project) }}</span>
                  <small v-if="project.stats.total">已完成 {{ project.stats.done }} / {{ project.stats.total }} 项</small>
                </div>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 4 4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </router-link>
            </li>
          </ul>
          <p v-else-if="data.today" class="empty">{{ hasFilter ? '没有符合筛选条件的项目' : '还没有运营项目' }}</p>
          <p v-if="fetchError" class="notice" role="status">暂时无法获取最新项目。<el-button link type="primary" @click="load()">重试</el-button></p>
        </section>
      </el-tab-pane>
      <el-tab-pane label="待办" name="todos" lazy>
        <section class="surface" aria-label="运营待办">
          <div class="toolbar" v-if="data.todos.length">
            <el-select v-model="todoFilter" aria-label="待办状态"><el-option v-for="(label, value) in todoFilters" :key="value" :value="value" :label="label" /></el-select>
            <el-checkbox v-model="mine">只看我负责的</el-checkbox>
          </div>
          <TaskList v-if="todos.length" :rows="todos" show-project @open-task="openTask" />
          <p v-else class="empty">{{ data.todos.length ? '没有符合条件的待办' : '当前没有待办' }}</p>
        </section>
      </el-tab-pane>
      <el-tab-pane label="月度任务总表" name="monthly" lazy>
        <section class="surface"><MonthlyPlan v-if="data.today" :today="data.today" :active="tab === 'monthly'" @open-task="openTask" /></section>
      </el-tab-pane>
    </el-tabs>
    <ProjectForm v-model="createOpen" @saved="project => router.push(`/operations/${project.id}`)" />
    <TaskDialog v-model="taskOpen" :task-id="selectedTask" @edit="editTask" />
    <TaskForm v-if="editProject" v-model="editOpen" :project="editProject" :initial="editingTask" @closed="popupPaused = false" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, useId } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import ProjectForm from './operations/ProjectForm.vue'
import MonthlyPlan from './operations/MonthlyPlan.vue'
import TaskDialog from './operations/TaskDialog.vue'
import TaskForm from './operations/TaskForm.vue'
import TaskList from './operations/TaskList.vue'
import { orderTasks, PROJECT_STATUS } from './operations/shared'

const router = useRouter(), route = useRoute(), auth = useAuth(), loading = ref(false), fetchError = ref(false), createOpen = ref(false), tab = ref('projects')
const query = reactive({ keyword: '', category: '', key_only: false }), categories = ref([]), filterOpen = ref(false), filterId = useId()
const hasFilter = computed(() => Boolean(query.keyword || query.category || query.key_only))
const data = reactive({ items: [], todos: [], can_create: false, today: '' })
const editProject = ref(null), editingTask = ref(null), editOpen = ref(false), popupPaused = ref(false)
const selectedTask = computed(() => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined)
const taskOpen = computed({ get: () => Boolean(selectedTask.value) && !popupPaused.value, set: value => { if (!value && !popupPaused.value) closeTask() } })
const todoFilter = ref('all'), mine = ref(false), todoFilters = { all: '全部待办', overdue: '已逾期', blocked: '受阻', needs_update: '今日待反馈' }
const todos = computed(() => orderTasks(data.todos.filter(task => (!mine.value || task.owner_id === auth.profile?.id) &&
  (todoFilter.value === 'all' || (todoFilter.value === 'blocked' ? task.status === 'blocked' : task[todoFilter.value])))))
let version = 0
async function load(background = false) {
  const current = ++version
  if (!background) loading.value = true
  try {
    const result = await api.get('/operations', query, { silent: true })
    if (current === version) { Object.assign(data, result); fetchError.value = false }
  } catch { if (current === version) fetchError.value = true }
  finally { if (current === version) loading.value = false }
}
function clearFilters() { Object.assign(query, { keyword: '', category: '', key_only: false }); load() }
function projectStatus(project) {
  if (project.stats.overdue) return `${project.stats.overdue} 项逾期`
  if (project.stats.blocked) return `${project.stats.blocked} 项受阻`
  return PROJECT_STATUS[project.status]
}
function openTask(task) { router.replace({ query: { ...route.query, task: task.id } }) }
function closeTask() { const { task, ...query } = route.query; if (task) router.replace({ query }) }
async function editTask(task) {
  try {
    const project = await api.get(`/operations/${task.project_id}`)
    if (selectedTask.value !== task.id) return
    editingTask.value = task; editProject.value = project; popupPaused.value = true; editOpen.value = true
  } catch {}
}
useBusinessRefresh(() => load(true), { interval: 12000 })
onMounted(async () => {
  await load()
  try { categories.value = (await api.get('/operations/options', null, { silent: true })).categories } catch {}
})
onBeforeUnmount(() => { version++ })
</script>

<style scoped>
.operations { max-width: 1320px; }.operations-heading { margin-bottom: 14px; }.operations-heading :deep(h1) { font-size: clamp(25px, 2.4vw, 34px); }
.surface { padding: 14px 21px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); }
.toolbar, .filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 9px; }.toolbar .el-input { width: 192px; }.toolbar .el-select, .filters .el-select { width: 142px; }
.filters { padding: 8px 10px; background: var(--jc-surface-2); border-radius: 6px; }.filters .el-checkbox { margin-right: 6px; }
.project-list { margin: 0; padding: 0; list-style: none; }.project-list li + li { border-top: 1px solid var(--jc-line-2); }
.project-row { display: grid; grid-template-columns: minmax(0, 1fr) auto 16px; align-items: center; gap: 13px; min-height: 64px; padding: 10px 4px; border-radius: 4px; color: var(--jc-ink); text-decoration: none; }.project-row:hover { background: var(--jc-surface-2); }.project-row:focus-visible { outline: 2px solid var(--jc-brand); }
.project-title, .project-state { display: grid; gap: 5px; }.project-title strong { font-size: 16px; overflow-wrap: anywhere; }.project-title span, .project-state small { color: var(--jc-ink-3); font-size: 12px; }.project-state { justify-items: end; white-space: nowrap; color: var(--jc-ink-2); font-size: 13px; }.project-state .attention { color: var(--jc-down); }.project-row svg { color: var(--jc-ink-3); }
.empty, .notice { margin: 8px 0; font-size: 14px; color: var(--jc-ink-3); }.notice { color: var(--jc-warn); }
@media (max-width: 600px) { .surface { padding: 12px; }.project-row { gap: 8px; } }
</style>
