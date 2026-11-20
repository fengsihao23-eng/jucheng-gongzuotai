<template>
  <div class="jc-page">
    <PageHeader eyebrow="运营管理 · 重点工作" title="运营项目" desc="把目标落到负责人，按节点反馈进展。">
      <el-button @click="load" :loading="loading">刷新</el-button>
      <el-button v-if="data.can_create" type="primary" @click="createOpen = true">新建运营项目</el-button>
    </PageHeader>
    <Panel class="mt-16" :loading="loading && !data.today">
      <el-tabs v-model="tab">
        <el-tab-pane v-if="auth.hasSection('operations', 'tasks')" label="月度任务总表" name="monthly" lazy><MonthlyPlan :today="data.today" :active="tab === 'monthly'" @open-task="openTask" /></el-tab-pane>
        <el-tab-pane v-if="auth.hasSection('operations', 'reports')" label="项目与个人总结" name="digest" lazy><OpsDigest :active="tab === 'digest'" /></el-tab-pane>
        <el-tab-pane label="项目总览" name="projects">
          <section v-if="attention" class="attention-line" aria-label="需要关注">
            <button type="button" class="attention-task" @click="openTask(attention.task)">
              <StatusPill :text="attention.label" :tone="attention.tone" dot />
              <span class="attention-copy"><span class="attention-project">{{ attention.task.project_name }} · </span><strong>{{ attention.task.title }}</strong></span>
              <span class="attention-arrow" aria-hidden="true">›</span>
            </button>
            <el-button link type="primary" @click="showTodos(attention.filter)">查看待办</el-button>
          </section>
          <div class="filters">
            <el-input v-model="query.keyword" placeholder="搜索项目或目标" clearable style="width: 240px" @change="load" />
            <el-select v-model="query.category" placeholder="全部分类" clearable style="width: 170px" @change="load"><el-option v-for="c in categories" :key="c" :value="c" :label="c" /></el-select>
            <el-checkbox v-model="query.key_only" @change="load">仅重点项目</el-checkbox>
          </div>
          <div class="project-grid">
            <button v-for="p in data.items" :key="p.id" class="project-card" @click="router.push(`/operations/${p.id}`)" @contextmenu.prevent="openMenu($event, p)">
              <div class="jc-row between"><span class="jc-small jc-muted">{{ p.category }}</span><StatusPill v-if="p.is_key" text="重点项目" tone="brand" /></div>
              <h3>{{ p.name }}</h3>
              <p class="objective">{{ p.objective || '目标待补充，进入项目安排首批重点工作。' }}</p>
              <div class="jc-row between jc-small"><span>负责人 · {{ p.owner_name }}</span><span class="jc-muted">{{ PROJECT_STATUS[p.status] }}</span></div>
              <div class="progress-line"><i :style="{ width: (p.stats.total ? p.stats.done / p.stats.total * 100 : 0) + '%' }" /></div>
              <div class="jc-row between jc-small"><span>已完成 {{ p.stats.done }} / {{ p.stats.total }}</span><span :class="p.stats.overdue ? 'jc-down' : 'jc-muted'">{{ p.stats.overdue ? `${p.stats.overdue} 项逾期` : '查看任务与进展 →' }}</span></div>
            </button>
          </div>
          <el-empty v-if="!data.items.length && !loading" :description="data.can_create ? '从智慧校园开始，建立第一个运营项目' : '暂无参与的运营项目，请由项目负责人加入团队'">
            <el-button v-if="data.can_create" type="primary" @click="createOpen = true">建立智慧校园项目</el-button>
          </el-empty>
        </el-tab-pane>
        <el-tab-pane v-if="auth.hasSection('operations', 'tasks')" label="当前需要做什么" name="todos">
          <div class="filters">
            <el-radio-group v-model="todoFilter"><el-radio-button value="all">全部待办</el-radio-button><el-radio-button value="overdue">逾期</el-radio-button><el-radio-button value="blocked">受阻</el-radio-button><el-radio-button value="needs_update">今日待反馈</el-radio-button><el-radio-button v-if="data.delay_review_inbox.length || todoFilter === 'delay_review'" value="delay_review">待我复核延期</el-radio-button></el-radio-group>
            <el-button :type="todoFilter === 'pending_replies' ? 'primary' : 'default'" @click="todoFilter = todoFilter === 'pending_replies' ? 'all' : 'pending_replies'">待我回复（{{ data.todos.filter(t => t.pending_replies).length }}）</el-button>
            <el-checkbox v-if="!['delay_review', 'pending_replies'].includes(todoFilter)" v-model="mine">只看我负责的</el-checkbox>
          </div>
          <el-table :data="todos" :row-class-name="({ row }) => row.overdue ? 'is-alert' : ''" @row-click="openTask">
            <el-table-column label="任务 / 项目" min-width="260"><template #default="{ row }"><b>{{ row.title }}</b><div class="jc-small jc-muted">{{ row.project_name }}{{ row.school_name ? ` · ${row.school_name}` : '' }}</div></template></el-table-column>
            <el-table-column prop="owner_name" label="负责人" width="110" />
            <el-table-column prop="due_date" label="截止日期" width="120" />
            <el-table-column label="状态" width="110"><template #default="{ row }"><StatusPill :text="row.status === 'done' ? completionLabel(row) : TASK_STATUS[row.status]" :tone="row.status === 'done' ? deadlineTone(row) : tone(row.status)" /></template></el-table-column>
            <el-table-column prop="next_step" label="下一步" min-width="190" show-overflow-tooltip />
            <el-table-column width="110"><template #default="{ row }"><el-button link type="primary" @click.stop="openTask(row)">{{ row.can_review_delay ? '复核延期' : row.can_feedback ? '反馈进展' : '查看详情' }}</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </Panel>
    <PasswordDeleteDialog v-model="deleteOpen" title="删除运营项目" :name="menu.project?.name" :endpoint="`/operations/${menu.project?.id}`" @deleted="load()" />
    <ProjectForm v-model="createOpen" @saved="p => router.push(`/operations/${p.id}`)" />
    <TaskDialog v-model="taskOpen" :task-id="selectedTask" @edit="editTask" />
    <TaskForm v-if="editProject" v-model="editOpen" :project="editProject" :initial="editingTask" @closed="popupPaused = false" />
    <div v-if="menu.open" class="ctx-mask" @click="menu.open = false" @contextmenu.prevent="menu.open = false">
      <div class="ctx-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" @click.stop>
        <div class="ctx-title">{{ menu.project?.name }}</div>
        <button type="button" class="ctx-item danger" @click="menu.open = false; deleteOpen = true">删除项目</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import ProjectForm from './operations/ProjectForm.vue'
import PasswordDeleteDialog from './operations/PasswordDeleteDialog.vue'
import MonthlyPlan from './operations/MonthlyPlan.vue'
import OpsDigest from './operations/OpsDigest.vue'
import TaskDialog from './operations/TaskDialog.vue'
import TaskForm from './operations/TaskForm.vue'
import { PROJECT_STATUS, TASK_STATUS, completionLabel, deadlineTone, tone } from './operations/shared'
const router = useRouter(), route = useRoute(), auth = useAuth(), loading = ref(false), createOpen = ref(false), tab = ref('projects')
const query = reactive({ keyword: '', category: '', key_only: false }), categories = ref([])
const data = reactive({ items: [], todos: [], delay_review_inbox: [], can_create: false, can_delete: false })
const menu = reactive({ open: false, x: 0, y: 0, project: null }), deleteOpen = ref(false)
const editProject = ref(null), editingTask = ref(null), editOpen = ref(false), popupPaused = ref(false)
const selectedTask = computed(() => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined)
const taskOpen = computed({ get: () => Boolean(selectedTask.value) && !popupPaused.value, set: value => { if (!value && !popupPaused.value) closeTask() } })
const todoFilter = ref('all'), mine = ref(route.query.mine === '1')
const todos = computed(() => {
  const filter = todoFilter.value
  if (filter === 'delay_review') return data.delay_review_inbox
  if (filter === 'pending_replies') return data.todos.filter(t => t.pending_replies)
  const tasks = filter === 'all' ? [...new Map([...data.todos, ...data.delay_review_inbox].map(task => [task.id, task])).values()] : data.todos
  return tasks.filter(t => (!mine.value || t.owner_id === auth.profile?.id) &&
    (filter === 'all' || (filter === 'blocked' ? t.status === 'blocked' : t[filter])))
})
const attention = computed(() => {
  const projectIds = new Set(data.items.map(project => project.id))
  const tasks = data.todos.filter(task => projectIds.has(task.project_id))
  const overdue = tasks.find(task => task.overdue)
  if (overdue) return { task: overdue, label: '已逾期', tone: 'down' }
  const blocked = tasks.find(task => task.status === 'blocked')
  if (blocked) return { task: blocked, label: '受阻', tone: 'down' }
  const delay = data.delay_review_inbox.find(task => projectIds.has(task.project_id))
  if (delay) return { task: delay, label: '延期待认可', tone: 'warn', filter: 'delay_review' }
  const needsUpdate = tasks.find(task => task.needs_update)
  return needsUpdate ? { task: needsUpdate, label: '今日待反馈', tone: 'warn' } : null
})
let version = 0
async function load(background = false) {
  const current = ++version
  if (background !== true) loading.value = true
  try { const result = await api.get('/operations', query, { silent: background === true }); if (current === version) Object.assign(data, result) }
  catch {} finally { if (current === version) loading.value = false }
}
function openTask(t) { router.replace({ query: { ...route.query, task: t.id } }) }
function openMenu(event, project) {
  if (!data.can_delete) return
  menu.project = project
  menu.x = Math.min(event.clientX, window.innerWidth - 190)
  menu.y = Math.min(event.clientY, window.innerHeight - 80)
  menu.open = true
}
function showTodos(filter = 'all') { todoFilter.value = filter; mine.value = false; tab.value = 'todos' }
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
  if (route.query.view === 'todos') tab.value = 'todos'
  await load()
  try { categories.value = (await api.get('/operations/options')).categories } catch {}
})
</script>
<style scoped>
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-bottom: 20px; }
.attention-line { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; padding: 4px 10px; border-radius: 7px; background: var(--jc-surface-2); }
.attention-task { display: flex; flex: 1; min-width: 0; align-items: center; gap: 8px; border: 0; border-radius: 4px; background: none; color: var(--jc-ink); text-align: left; font: inherit; padding: 4px 0; cursor: pointer; }
.attention-task :deep(.jc-pill) { flex-shrink: 0; }.attention-copy { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.attention-task:hover strong { color: var(--jc-brand); }.attention-task:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 2px; }.attention-task strong { font-weight: 600; }
.attention-project, .attention-arrow { color: var(--jc-ink-3); }.attention-arrow { font-size: 17px; }.attention-line .el-button { flex-shrink: 0; }
.project-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 18px; }
.project-card { border: 1px solid var(--jc-line); background: var(--jc-surface); padding: 22px; border-radius: var(--jc-radius); text-align: left; color: var(--jc-ink); cursor: pointer; font: inherit; transition: border-color .15s; }
.project-card:hover { border-color: var(--jc-brand); }
.project-card h3 { font-family: var(--jc-font-serif); font-size: 22px; margin: 18px 0 8px; }
.objective { color: var(--jc-ink-2); font-size: 13px; line-height: 1.7; min-height: 44px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin: 0 0 24px; }
.progress-line { height: 4px; background: var(--jc-line-2); margin: 18px 0 10px; border-radius: 4px; overflow: hidden; }
.progress-line i { height: 100%; display: block; background: var(--jc-brand); }
.ctx-mask { position: fixed; inset: 0; z-index: 3000; }
.ctx-menu { position: fixed; min-width: 160px; padding: 6px; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 8px; box-shadow: 0 12px 32px rgba(0, 0, 0, .16); }
.ctx-title { padding: 6px 10px 8px; font-size: 12px; color: var(--jc-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 220px; border-bottom: 1px solid var(--jc-line-2); margin-bottom: 4px; }
.ctx-item { display: block; width: 100%; padding: 8px 10px; border: 0; border-radius: 6px; background: none; color: var(--jc-ink); font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.ctx-item:hover { background: var(--jc-surface-2); }
.ctx-item.danger { color: var(--el-color-danger, #d9534f); }
@media(max-width: 560px) { .attention-line { gap: 6px; padding-inline: 7px; }.attention-task { gap: 6px; }.attention-project { display: none; } }
</style>
