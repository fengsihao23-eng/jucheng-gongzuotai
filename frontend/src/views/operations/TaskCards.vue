<template>
  <div class="project-task-grid" :class="{ single }">
    <ProjectTaskCard v-for="task in rows" :key="task.id" :card="cardData(task)"
      @click="emit('open-task', task)" @contextmenu.prevent.stop="openMenu(task, $event)" />
    <Teleport to="body">
      <div v-if="menu.open" class="task-menu-mask" @click="menu.open = false" @contextmenu.prevent="menu.open = false" @keydown.esc="menu.open = false">
        <div class="task-context-menu" role="menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" @click.stop>
          <button role="menuitem" type="button" @click="deleteOpen = true; menu.open = false">删除任务</button>
        </div>
      </div>
    </Teleport>
    <PasswordDeleteDialog v-model="deleteOpen" :name="menu.task?.title" :endpoint="`/operations/tasks/${menu.task?.id}`" />
  </div>
</template>

<script setup>
import ProjectTaskCard from '@/components/ProjectTaskCard.vue'
import { datetime } from '@/utils/format'
import { feedbackTime } from '@/utils/taskFeedback'
import { reactive, ref } from 'vue'
import PasswordDeleteDialog from './PasswordDeleteDialog.vue'
import { TASK_SOURCES, completionLabel, deadlineTone, statusLabel, taskProgress, taskStatus, tone } from './shared'
const props = defineProps({ rows: { type: Array, required: true }, showProject: Boolean, single: Boolean })
const emit = defineEmits(['open-task', 'menu'])
const deleteOpen = ref(false), menu = reactive({ open: false, x: 0, y: 0, task: null })
function cardData(task) {
  const badges = []
  if (task.priority === 'key') badges.push({ label: '重点', tone: 'brand' })
  if (task.overdue) badges.push({ label: '已逾期', tone: 'down' })
  else if (task.deadline_status === 'overdue') badges.push({ label: '原定期限已过', tone: 'down' })
  if (task.feedback_status === 'overdue') badges.push({ label: '逾期未反馈', tone: 'down' })
  else if (task.needs_update) badges.push({ label: '今日待反馈', tone: 'warn' })
  return {
    title: task.title, state: taskStatus(task), badges,
    status: task.status === 'done' ? completionLabel(task) : statusLabel(task),
    tone: task.status === 'done' ? deadlineTone(task) : task.approval_status === 'approved' ? tone(task.status) : task.approval_status === 'rejected' ? 'down' : 'warn',
    context: [TASK_SOURCES[task.source], props.showProject ? task.project_name : '', task.school_name].filter(Boolean).join(' · '),
    latest: task.latest_feedback || task.plan_context?.progress || task.description || '尚未反馈进展',
    nextStep: task.next_step, progress: task.approval_status === 'approved' ? taskProgress(task) : null,
    nodes: task.nodes.map(node => ({ id: node.id, name: node.name, due: node.due_date, completed: node.completed_at, overdue: node.overdue })),
    feedbackDeadline: task.feedback_deadline_pending ? feedbackTime(task.feedback_due_at) : '',
    feedbackOverdue: task.feedback_status === 'overdue',
    feedbackLabel: task.last_feedback_at ? '反馈 ' + datetime(task.last_feedback_at) : '尚未反馈',
    pendingReplies: task.pending_replies, carryover: task.carryover ? task.plan_month : '',
    owner: task.owner_name, due: task.due_date, completedOn: task.completed_on,
    action: task.can_review_delay ? '复核延期' : task.can_review ? '处理审核' : task.can_feedback ? '反馈进展' : '查看详情',
  }
}
function openMenu(task, event) {
  emit('menu', task, event)
  if (!task.can_archive || task.archived_at) return
  Object.assign(menu, { open: true, task, x: Math.max(8, Math.min(event.clientX, window.innerWidth - 170)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - 65)) })
}
</script>

<style scoped>
.task-menu-mask { position: fixed; inset: 0; z-index: 3000; }
.task-context-menu { position: fixed; min-width: 150px; padding: 6px; border: 1px solid var(--jc-line); border-radius: 8px; background: var(--jc-surface); box-shadow: 0 8px 28px #0002; }
.task-context-menu button { width: 100%; padding: 10px 14px; border: 0; border-radius: 5px; text-align: left; background: none; color: var(--el-color-danger); cursor: pointer; font: inherit; }
.task-context-menu button:hover { background: var(--jc-surface-2); }
</style>
