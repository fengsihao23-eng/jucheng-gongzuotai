<template>
  <div class="key-task-board">
    <div class="task-filters">
      <el-input v-model="search" class="task-search" placeholder="搜索任务 / 负责人" aria-label="重点任务搜索" clearable />
      <el-checkbox v-model="mine">我负责的</el-checkbox>
      <el-checkbox v-model="attention">需要关注</el-checkbox>
      <span class="jc-spacer" />
      <el-radio-group v-model="view" size="small" aria-label="任务显示方式">
        <el-radio-button value="cards">任务卡</el-radio-button>
        <el-radio-button value="table">进度表</el-radio-button>
      </el-radio-group>
      <el-button link @click="summaryOpen = true">进展汇总</el-button>
    </div>
    <TaskStatusFilters v-model="status" :groups="groups" />
    <div v-if="filtered.length && view === 'cards'" class="project-task-grid">
      <ProjectTaskCard v-for="task in filtered" :key="task.id" v-record-delete="taskRemoval(task)"
        :card="cardData(task)" @click="openTask(task.id)" />
    </div>
    <el-table v-else-if="filtered.length" :data="filtered" @row-click="row => openTask(row.id)" @row-contextmenu="taskMenu">
      <el-table-column prop="title" label="事项" min-width="190" />
      <el-table-column prop="owner_name" label="负责人" min-width="100" />
      <el-table-column prop="plan_date" label="截止" width="108" />
      <el-table-column label="反馈截止" min-width="154"><template #default="{ row }"><span :class="{ 'jc-down': row.feedback_status === 'overdue' }">{{ feedbackDeadline(row) || '—' }}</span></template></el-table-column>
      <el-table-column prop="progress_note" label="最近进展" min-width="180" show-overflow-tooltip />
      <el-table-column prop="next_step" label="下一步" min-width="140" show-overflow-tooltip />
      <el-table-column label="状态" min-width="110"><template #default="{ row }">{{ taskState(row).label }}</template></el-table-column>
      <el-table-column label="操作" width="84"><template #default="{ row }"><el-button link type="primary" @click.stop="openTask(row.id)">{{ canFeedback(row) ? '反馈进展' : '查看详情' }}</el-button></template></el-table-column>
    </el-table>
    <el-empty v-else :description="project.tasks.length ? '没有符合条件的任务' : '还没有安排任务'" :image-size="66">
      <el-button v-if="editable && !project.tasks.length" type="primary" @click="editTask()">安排第一项任务</el-button>
    </el-empty>

    <KeyTaskForm v-model="formOpen" :initial="editingTask" :project="project" @saved="savedTask" />
    <KeyTaskDialog v-model="taskOpen" :task-id="taskId" :project="project" @saved="emit('refresh')" @edit="editTask" />
    <el-dialog v-model="summaryOpen" title="当前进展汇总" width="min(900px, calc(100vw - 24px))" align-center append-to-body>
      <h3 class="summary-title">{{ project.name }}</h3>
      <p class="jc-muted">进行中 {{ totals.ongoing.length }} 项 · 已完成 {{ totals.done.length }} 项 · 逾期未反馈 {{ totals.overdue.length }} 项</p>
      <div v-if="summaryTasks.length" class="project-task-grid">
        <ProjectTaskCard v-for="task in summaryTasks" :key="task.id" :card="cardData(task)"
          @click="summaryOpen = false; openTask(task.id)" />
      </div>
      <el-empty v-else description="还没有安排任务" :image-size="60" />
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import ProjectTaskCard from '@/components/ProjectTaskCard.vue'
import TaskStatusFilters from '@/components/TaskStatusFilters.vue'
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'
import { feedbackTime, groupTaskFeedback } from '@/utils/taskFeedback'
import KeyTaskDialog from './KeyTaskDialog.vue'
import KeyTaskForm from './KeyTaskForm.vue'

const props = defineProps({ project: { type: Object, required: true } })
const emit = defineEmits(['refresh'])
const taskId = defineModel('taskId', { type: Number })
const auth = useAuth()
const search = ref(''), mine = ref(false), attention = ref(false), status = ref('ongoing'), view = ref('cards')
const formOpen = ref(false), editingTask = ref(null), summaryOpen = ref(false)
const active = computed(() => !props.project.paused && props.project.status !== 5)
const editable = computed(() => active.value && props.project.can_manage)
const canFeedback = task => active.value && task.status !== 3 && task.can_feedback
const isDone = task => task.status === 3
const groups = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return groupTaskFeedback(props.project.tasks.filter(task => (!mine.value || task.owner_id === auth.profile?.id) &&
    (!attention.value || task.alert) && `${task.title} ${task.owner_name || ''}`.toLowerCase().includes(keyword)), isDone)
})
const filtered = computed(() => groups.value[status.value])
const totals = computed(() => groupTaskFeedback(props.project.tasks, isDone))
const summaryTasks = computed(() => {
  const attention = totals.value.ongoing.filter(task => task.alert)
  return (attention.length ? attention : props.project.tasks).slice(0, 6)
})
const taskOpen = computed({
  get: () => Boolean(taskId.value) && !formOpen.value,
  set: open => { if (!open && !formOpen.value) taskId.value = undefined },
})
const taskRemoval = task => (props.project.can_manage || auth.hasDeleteGrant) &&
  recordRemoval('key-task', task.id, task.title, () => emit('refresh'), { title: '删除重点项目任务' })
const taskMenu = rowDeletion(taskRemoval)

function openTask(id) { taskId.value = id }
function editTask(task = null) {
  editingTask.value = task
  formOpen.value = true
}
function savedTask(task) {
  emit('refresh')
  openTask(task.id)
}
function taskState(task) {
  if (isDone(task)) return { state: 'done', label: '已完成', tone: 'up' }
  if (task.is_delayed || task.status === 4) return { state: 'blocked', label: '逾期未完成', tone: 'down' }
  if (task.problems) return { state: 'blocked', label: '有问题', tone: 'down' }
  if (task.status === 1) return { state: 'todo', label: '待开始', tone: 'info' }
  return { state: 'doing', label: task.status_text, tone: 'brand' }
}
function feedbackDeadline(task) {
  if (task.feedback_deadline_pending) return feedbackTime(task.feedback_due_at)
  return active.value && !isDone(task) ? task.feedback_due_on || '' : ''
}
function cardData(task) {
  const { state, label, tone } = taskState(task)
  const badges = []
  if (task.feedback_status === 'overdue') badges.push({ label: '逾期未反馈', tone: 'down' })
  else if (task.needs_feedback) badges.push({ label: '待反馈', tone: 'warn' })
  const nodes = task.nodes.map(node => ({ id: node.id, name: node.name, due: node.due_on, completed: node.completed_on, overdue: node.overdue }))
  const done = nodes.filter(node => node.completed).length
  const report = task.latest_feedback?.created_at
  const percent = isDone(task) ? 100 : done && done < nodes.length ? Math.round(done / nodes.length * 100) : null
  const progressLabel = percent !== null ? `${percent}%` : done ? '节点已齐，待确认' : report ? '已反馈' : '待反馈'
  return {
    title: task.title, state, status: label, tone, badges,
    latest: task.progress_note || task.requirements || '尚未反馈进展', problem: task.problems, nextStep: task.next_step,
    progress: { label: progressLabel, percent }, nodes,
    feedbackDeadline: feedbackDeadline(task), feedbackOverdue: task.feedback_status === 'overdue',
    feedbackLabel: report ? '反馈 ' + datetime(report) : '尚未反馈', owner: task.owner_name, due: task.plan_date,
    completedOn: task.finish_date, action: canFeedback(task) ? '反馈进展' : '查看详情',
  }
}

defineExpose({ editTask })
</script>

<style scoped>
.task-filters { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 14px; }
.task-filters .el-checkbox { margin-right: 0; }
.task-search { width: min(240px, 100%); }
.summary-title { margin: 0 0 8px; }
</style>
