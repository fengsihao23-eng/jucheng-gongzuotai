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
          <ProjectOverview v-model:keyword="query.keyword" v-model:category="query.category" v-model:key-only="query.key_only"
            :data="data" :loading="loading" :show-task-list="auth.hasSection('operations', 'tasks')" project-menu
            @filter="load()" @open-task="openTask" @show-todos="showTodos" @project-menu="openMenu" @create="createOpen = true" />
        </el-tab-pane>
        <el-tab-pane v-if="auth.hasSection('operations', 'tasks')" label="当前需要做什么" name="todos">
          <div class="filters">
            <el-radio-group v-model="todoFilter"><el-radio-button value="all">全部待办</el-radio-button><el-radio-button value="overdue">逾期</el-radio-button><el-radio-button value="blocked">受阻</el-radio-button><el-radio-button value="needs_update">今日待反馈</el-radio-button><el-radio-button value="feedback_overdue">逾期未反馈</el-radio-button><el-radio-button v-if="data.delay_review_inbox.length || todoFilter === 'delay_review'" value="delay_review">待我复核延期</el-radio-button></el-radio-group>
            <el-button :type="todoFilter === 'pending_replies' ? 'primary' : 'default'" @click="todoFilter = todoFilter === 'pending_replies' ? 'all' : 'pending_replies'">待我回复（{{ data.todos.filter(t => t.pending_replies).length }}）</el-button>
            <el-checkbox v-if="!['delay_review', 'pending_replies'].includes(todoFilter)" v-model="mine">只看我负责的</el-checkbox>
          </div>
          <el-table :data="todos" :row-class-name="({ row }) => row.overdue ? 'is-alert' : ''" @row-click="openTask" @row-contextmenu="taskMenu">
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
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import ProjectForm from './operations/ProjectForm.vue'
import ProjectOverview from './operations/ProjectOverview.vue'
import PasswordDeleteDialog from './operations/PasswordDeleteDialog.vue'
import MonthlyPlan from './operations/MonthlyPlan.vue'
import OpsDigest from './operations/OpsDigest.vue'
import TaskDialog from './operations/TaskDialog.vue'
import TaskForm from './operations/TaskForm.vue'
import { TASK_STATUS, completionLabel, deadlineTone, tone } from './operations/shared'
import { useProjectOverview } from './operations/useProjectOverview'
const router = useRouter(), route = useRoute(), auth = useAuth(), createOpen = ref(false)
const tab = ref(route.query.view === 'todos' && auth.hasSection('operations', 'tasks') ? 'todos' : 'projects')
const { query, data, loading, load } = useProjectOverview()
const taskMenu = rowDeletion(row => row.can_archive && recordRemoval('operation-task', row.id, row.title, () => load(), { title: '删除任务' }))
const menu = reactive({ open: false, x: 0, y: 0, project: null }), deleteOpen = ref(false)
const editProject = ref(null), editingTask = ref(null), editOpen = ref(false), popupPaused = ref(false)
const selectedTask = computed(() => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined)
const taskOpen = computed({ get: () => Boolean(selectedTask.value) && !popupPaused.value, set: value => { if (!value && !popupPaused.value) closeTask() } })
const todoFilter = ref(route.query.filter === 'delay_review' ? 'delay_review' : 'all'), mine = ref(route.query.mine === '1')
const todos = computed(() => {
  const filter = todoFilter.value
  if (filter === 'delay_review') return data.delay_review_inbox
  if (filter === 'pending_replies') return data.todos.filter(t => t.pending_replies)
  const tasks = filter === 'all' ? [...new Map([...data.todos, ...data.delay_review_inbox].map(task => [task.id, task])).values()] : data.todos
  return tasks.filter(t => (!mine.value || t.owner_id === auth.profile?.id) &&
    (filter === 'all' || (filter === 'blocked' ? t.status === 'blocked' : filter === 'feedback_overdue' ? t.feedback_status === 'overdue' : t[filter])))
})
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
</script>
<style scoped>
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-bottom: 20px; }
.ctx-mask { position: fixed; inset: 0; z-index: 3000; }
.ctx-menu { position: fixed; min-width: 160px; padding: 6px; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 8px; box-shadow: 0 12px 32px rgba(0, 0, 0, .16); }
.ctx-title { padding: 6px 10px 8px; font-size: 12px; color: var(--jc-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 220px; border-bottom: 1px solid var(--jc-line-2); margin-bottom: 4px; }
.ctx-item { display: block; width: 100%; padding: 8px 10px; border: 0; border-radius: 6px; background: none; color: var(--jc-ink); font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.ctx-item:hover { background: var(--jc-surface-2); }
.ctx-item.danger { color: var(--el-color-danger, #d9534f); }
</style>
