<template>
  <div class="cards" :class="{ single }">
    <button v-for="task in rows" :key="task.id" type="button" class="task-card" :data-status="taskStatus(task)" @click="$emit('open-task', task)">
      <div class="card-top">
        <StatusPill :text="task.status === 'done' ? completionLabel(task) : statusLabel(task)" :tone="task.status === 'done' ? deadlineTone(task) : task.approval_status === 'approved' ? tone(task.status) : task.approval_status === 'rejected' ? 'down' : 'warn'" dot />
        <span v-if="task.priority === 'key'" class="key">重点</span>
        <span v-if="task.overdue" class="jc-down flag">已逾期</span><span v-else-if="task.deadline_status === 'overdue'" class="jc-down flag">原定期限已过</span><span v-else-if="task.needs_update" class="flag">今日待反馈</span>
      </div>
      <strong class="title">{{ task.title }}</strong>
      <span v-if="showProject || task.school_name" class="context">{{ [showProject ? task.project_name : '', task.school_name].filter(Boolean).join(' · ') }}</span>
      <span class="latest">{{ task.latest_feedback || task.description || '尚未反馈进展' }}</span>
      <span v-if="task.next_step" class="next">下一步：{{ task.next_step }}</span>
      <template v-if="task.approval_status === 'approved'">
        <span class="progress-label"><span>任务进度</span><b class="jc-num">{{ taskProgress(task).label }}</b></span>
        <span v-if="taskProgress(task).percent !== null" class="progress-track" aria-hidden="true"><i :style="{ width: `${task.progress}%` }" /></span>
        <span v-if="task.nodes.length" class="node-caption">
          <span class="nodes" aria-hidden="true"><i v-for="node in task.nodes" :key="node.id" :class="{ complete: node.completed_at, late: node.overdue && !node.completed_at }" /></span>
          节点 {{ task.nodes.filter(n => n.completed_at).length }} / {{ task.nodes.length }}
        </span>
      </template>
      <span v-if="task.carryover" class="carryover">从 {{ task.plan_month }} 延续</span>
      <div class="card-foot"><span class="owner">{{ task.owner_name }}<small>截止 {{ task.due_date }}</small><small v-if="task.completed_on">完成 {{ task.completed_on }}</small></span><span class="action">{{ task.can_review_delay ? '复核延期' : task.can_review ? '处理审核' : task.can_feedback ? '反馈进展' : '查看详情' }} <span aria-hidden="true">→</span></span></div>
    </button>
  </div>
</template>

<script setup>
import { completionLabel, deadlineTone, statusLabel, taskProgress, taskStatus, tone } from './shared'
defineProps({ rows: { type: Array, required: true }, showProject: Boolean, single: Boolean })
defineEmits(['open-task'])
</script>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr)); gap: 14px; }
.cards.single { grid-template-columns: minmax(0, 1fr); }
.task-card { --state: var(--jc-brand); display: flex; flex-direction: column; align-items: stretch; min-width: 0; padding: 14px 15px 13px; position: relative; border: 1px solid var(--jc-line); border-top: 3px solid var(--state); border-radius: var(--jc-radius); background: var(--jc-surface); color: var(--jc-ink); font: inherit; text-align: left; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.task-card[data-status="done"] { --state: var(--jc-up); }.task-card[data-status="blocked"], .task-card[data-status="rejected"] { --state: var(--jc-down); }.task-card[data-status="todo"] { --state: #aaa89f; }.task-card[data-status="review"] { --state: var(--jc-warn); }
.task-card:hover, .task-card:focus-visible { border-color: var(--jc-brand); box-shadow: var(--jc-shadow-pop); }
.card-top, .progress-label, .card-foot { display: flex; align-items: center; gap: 7px; }
.card-top { flex-wrap: wrap; }.key, .flag, .carryover { font-size: 11px; border: 1px solid var(--jc-line); border-radius: 4px; padding: 1px 5px; }.key { color: var(--jc-brand); }.flag { color: var(--jc-warn); }.flag.jc-down { color: var(--jc-down); }.carryover { align-self: flex-start; color: var(--jc-ink-2); margin-top: 6px; }
.title { display: block; font-size: 16px; line-height: 1.45; margin: 10px 0 3px; overflow-wrap: anywhere; }
.context, .latest, .next, .node-caption { color: var(--jc-ink-2); font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; }.context { color: var(--jc-ink-3); }.latest { margin-top: 7px; }.latest, .next { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }.next { margin-top: 3px; }
.progress-label { justify-content: space-between; font-size: 12px; color: var(--jc-ink-2); padding-top: 12px; margin-top: auto; }.progress-label b { color: var(--jc-ink); }
.progress-track { display: block; height: 6px; border-radius: 6px; background: var(--jc-line-2); overflow: hidden; margin-top: 5px; }.progress-track i { display: block; height: 100%; border-radius: inherit; background: var(--state); transition: width .2s ease; }
.node-caption { display: flex; gap: 8px; align-items: center; margin-top: 7px; white-space: nowrap; }.nodes { display: inline-flex; flex-wrap: wrap; gap: 3px; }.nodes i { width: 7px; height: 7px; border: 1px solid #aaa89f; border-radius: 50%; }.nodes .complete { background: var(--jc-up); border-color: var(--jc-up); }.nodes .late { border-color: var(--jc-down); }
.card-foot { margin-top: 12px; border-top: 1px solid var(--jc-line-2); padding-top: 9px; justify-content: space-between; font-size: 12px; }.owner { min-width: 0; overflow-wrap: anywhere; }.owner small { display: block; font-size: 11px; color: var(--jc-ink-3); margin-top: 2px; }.action { color: var(--jc-brand); flex-shrink: 0; }
</style>
