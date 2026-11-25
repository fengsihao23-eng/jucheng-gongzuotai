<template>
  <div class="project-task-board">
    <div class="filters">
      <el-input v-model="query" class="task-search" placeholder="搜索任务 / 学校 / 负责人" aria-label="搜索任务" clearable />
      <el-checkbox v-model="mine">我负责的</el-checkbox>
      <el-checkbox v-model="keyOnly">仅重点</el-checkbox>
      <el-checkbox v-model="attention">需要关注</el-checkbox>
      <span class="jc-spacer" />
      <el-radio-group v-model="taskView" size="small" aria-label="任务显示方式">
        <el-radio-button value="cards">任务卡</el-radio-button>
        <el-radio-button value="table">进度表</el-radio-button>
      </el-radio-group>
      <el-button v-if="delayRows.length" size="small" @click="delayOpen = true">待处理延期（{{ delayRows.length }}）</el-button>
      <el-button v-if="project.can_propose && project.status === 'active'" link type="primary" @click="newSelfTask">自行安排</el-button>
      <el-button link @click="summaryOpen = true; emit('refresh', true)">进展汇总</el-button>
    </div>
    <div class="board-status" role="group" aria-label="按任务状态筛选">
      <button v-for="(label, value) in statuses" :key="value" type="button" :aria-pressed="status === value" :data-status="value"
        :title="value === 'ongoing' ? '包含待开始、受阻、待审核和已退回的任务' : undefined" @click="status = value"><i /><span>{{ label }}</span><b class="jc-num">{{ taskGroups[value].length }}</b></button>
    </div>
    <TaskCards v-if="filteredTasks.length && taskView === 'cards'" :rows="filteredTasks" @open-task="openTask" />
    <el-table v-else-if="filteredTasks.length" :data="filteredTasks" @row-click="openTask" :row-class-name="({ row }) => row.overdue ? 'is-alert' : ''" @row-contextmenu="taskMenu">
      <el-table-column prop="title" label="事项" min-width="190" />
      <el-table-column prop="owner_name" label="负责人" width="100" />
      <el-table-column prop="due_date" label="截止" width="115" />
      <el-table-column prop="latest_feedback" label="最近进展" min-width="230" show-overflow-tooltip />
      <el-table-column prop="next_step" label="下一步 / 协调" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="110"><template #default="{ row }">{{ row.status === 'done' ? '已完成' : row.overdue ? '逾期未完成' : row.status === 'blocked' ? '受阻' : '进行中' }}</template></el-table-column>
      <el-table-column width="100"><template #default="{ row }"><el-button link @click.stop="openTask(row)">查看 / 反馈</el-button></template></el-table-column>
    </el-table>
    <el-empty v-else :description="project.tasks.length ? '没有符合筛选条件的任务' : '还没有安排任务'" :image-size="66">
      <el-button v-if="!project.tasks.length && project.can_manage && project.status === 'active'" type="primary" @click="newTask()">布置第一项任务</el-button>
    </el-empty>

    <TaskForm v-model="taskForm" :project="project" :initial="editingTask" :mode="taskMode" :school-preset="schoolPreset" @saved="t => pendingTask = t" @closed="taskFormClosed" />
    <TaskDialog v-model="taskOpen" :task-id="selectedTask" :project-id="project.id" @edit="editTask" />
    <el-dialog v-model="delayOpen" title="待处理延期" width="min(660px, calc(100vw - 24px))" align-center append-to-body @closed="delayClosed">
      <ul v-if="delayRows.length" class="delay-list">
        <li v-for="row in delayRows" :key="row.id">
          <div><strong>{{ row.title }}</strong><p>{{ TASK_SOURCES[row.source] }} · {{ row.owner_name }} · 原定 {{ row.deadline_basis }}</p></div>
          <el-button link type="primary" @click="selectDelay(row)">{{ row.can_review_delay ? '复核延期' : '补充延期原因' }}</el-button>
        </li>
      </ul>
      <p v-else class="jc-muted">暂无需要你处理的延期事项。</p>
    </el-dialog>
    <el-dialog v-model="summaryOpen" class="ops-summary-dialog" title="当前进展汇总" width="min(960px, calc(100vw - 24px))" align-center append-to-body>
      <ProjectProgress v-if="project.visible_sections.includes('progress')" :project="project" read-only />
      <TaskOverview :rows="project.tasks" title="项目任务概览" />
      <div v-if="project.tasks.length" class="recent">
        <h3>{{ followUps.length ? '优先跟进' : '最新进展' }}</h3>
        <TaskCards :rows="(followUps.length ? followUps : project.tasks).slice(0, 6)" @open-task="t => { summaryOpen = false; openTask(t) }" />
      </div>
      <template #footer>
        <el-button @click="copySummary">复制汇总</el-button>
        <el-button type="primary" @click="assistant.open(projectProgressPrompt(project))">请小聚进一步分析</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { useAssistant } from '@/store/assistant'
import { useAuth } from '@/store/auth'
import ProjectProgress from './ProjectProgress.vue'
import TaskCards from './TaskCards.vue'
import TaskDialog from './TaskDialog.vue'
import TaskForm from './TaskForm.vue'
import TaskOverview from './TaskOverview.vue'
import { projectProgressPrompt, TASK_SOURCES, taskStatus } from './shared'

const props = defineProps({ project: { type: Object, required: true } })
const emit = defineEmits(['refresh'])
const selectedTask = defineModel('taskId', { type: Number })
const auth = useAuth(), assistant = useAssistant()
const query = ref(''), status = ref('ongoing'), mine = ref(false), keyOnly = ref(false), attention = ref(false), taskView = ref('cards')
const statuses = { ongoing: '进行中', done: '已完成' }
const taskForm = ref(false), taskMode = ref('company'), editingTask = ref(null), pendingTask = ref(null), schoolPreset = ref(null)
const popupPaused = ref(false), delayOpen = ref(false), pendingDelay = ref(null), summaryOpen = ref(false)
const taskOpen = computed({
  get: () => Boolean(selectedTask.value) && !popupPaused.value,
  set: value => { if (!value && !popupPaused.value) selectedTask.value = undefined },
})
const needsAttention = t => t.overdue || t.needs_update || t.deadline_status === 'overdue' || t.delay_status === 'pending' || ['blocked', 'review', 'rejected'].includes(taskStatus(t))
const taskGroups = computed(() => {
  const groups = { ongoing: [], done: [] }
  const keyword = query.value.trim().toLowerCase()
  const matches = props.project.tasks.filter(task => (!mine.value || task.owner_id === auth.profile?.id) &&
    (!keyOnly.value || task.priority === 'key') && (!attention.value || needsAttention(task)) &&
    `${task.title} ${task.owner_name} ${task.school_name}`.toLowerCase().includes(keyword))
  for (const task of matches) groups[taskStatus(task) === 'done' ? 'done' : 'ongoing'].push(task)
  return groups
})
const filteredTasks = computed(() => taskGroups.value[status.value])
const followUps = computed(() => props.project.tasks.filter(needsAttention))
const delayRows = computed(() => props.project.tasks.filter(t => t.can_review_delay ||
  (t.can_explain_delay && ['needs_reason', 'rejected'].includes(t.delay_status))))
const taskMenu = rowDeletion(row => row.can_archive && recordRemoval('operation-task', row.id, row.title, () => emit('refresh'), { title: '删除任务' }))

function openTask(task) { selectedTask.value = task.id }
function showTaskForm(mode, task = null, school = null) {
  taskMode.value = mode
  editingTask.value = task
  schoolPreset.value = school
  taskForm.value = true
}
function newTask(school = null) { showTaskForm('company', null, school) }
function newSelfTask() { showTaskForm('self') }
function editTask(task) {
  popupPaused.value = true
  showTaskForm(task.source === 'self' ? 'self' : 'company', task)
}
function taskFormClosed() {
  popupPaused.value = false
  if (pendingTask.value) {
    openTask(pendingTask.value)
    pendingTask.value = null
  }
}
function selectDelay(row) {
  pendingDelay.value = row
  delayOpen.value = false
}
function delayClosed() {
  if (pendingDelay.value) {
    openTask(pendingDelay.value)
    pendingDelay.value = null
  }
}
async function copySummary() {
  try {
    const brief = await api.get(`/operations/${props.project.id}/brief`)
    await navigator.clipboard.writeText(brief.text)
    ElMessage.success('汇总已复制')
  } catch { ElMessage.info('暂时无法复制汇总，请重试') }
}

defineExpose({ newTask, openTask })
</script>

<style scoped>
.filters { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 14px; }
.filters .el-checkbox { margin-right: 0; }
.task-search { width: min(240px, 100%); }
.board-status { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 15px; }
.board-status button { --tone: var(--jc-brand); display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--jc-line); border-radius: 7px; padding: 7px 10px; font: inherit; font-size: 12px; color: var(--jc-ink-2); background: var(--jc-bg-2, #f9f8f5); cursor: pointer; }
.board-status button[data-status="done"] { --tone: var(--jc-up); }
.board-status button[aria-pressed="true"], .board-status button:hover { border-color: var(--jc-brand); color: var(--jc-brand); }
.board-status i { width: 7px; height: 7px; border-radius: 50%; background: var(--tone); }
.board-status b { font-size: 15px; color: var(--jc-ink); }
.recent { margin-top: 15px; }.recent h3 { font-size: 15px; }
:global(.ops-summary-dialog > .el-dialog__body) { max-height: 67dvh; overflow: auto; }
.delay-list { margin: 0; padding: 0; list-style: none; max-height: 62dvh; overflow: auto; }
.delay-list li { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 9px 0; overflow-wrap: anywhere; }
.delay-list li + li { border-top: 1px solid var(--jc-line-2); }.delay-list strong { color: var(--jc-ink); }.delay-list p { margin: 3px 0 0; color: var(--jc-ink-3); font-size: 12px; }.delay-list .el-button { flex-shrink: 0; }
</style>
