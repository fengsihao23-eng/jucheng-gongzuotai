<template>
  <ul class="task-list" :class="{ compact }" role="list" :aria-label="label">
    <li v-for="row in items" :key="row.task.id">
      <button type="button" class="task-row" @click="emit('open-task', row.task)">
        <span class="task-main">
          <strong>{{ row.task.title }}</strong>
          <span class="task-meta">{{ row.meta.join(' · ') }}</span>
          <span v-if="!compact && row.note" class="task-note">{{ row.note }}</span>
        </span>
        <span class="task-state">
          <span :class="['state-label', row.state.tone]">{{ row.state.label }}</span>
          <span v-if="row.showProgress" class="task-percent"><span class="track" aria-hidden="true"><i :style="{ width: `${row.task.progress}%` }" /></span>{{ row.task.progress }}%</span>
        </span>
        <svg class="chevron" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 4 4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </li>
  </ul>
</template>

<script setup>
import { computed } from 'vue'
import { date } from '@/utils/format'
import { TASK_SOURCES, statusLabel, taskAttention, tone } from './shared'

const props = defineProps({ rows: { type: Array, required: true }, label: { type: String, default: '任务列表' }, compact: Boolean, showProject: Boolean, monthly: Boolean })
const emit = defineEmits(['open-task'])
const items = computed(() => props.rows.map(task => {
  const meta = [props.showProject && task.project_name, task.owner_name, !props.compact && task.school_name,
    task.completed_at ? `完成 ${date(task.completed_at)}` : task.due_date && `截止 ${task.due_date}`,
    props.monthly && TASK_SOURCES[task.source], props.monthly && task.carryover && `从 ${task.plan_month} 延续`].filter(Boolean)
  return { task, meta, state: taskAttention(task) || { label: statusLabel(task), tone: tone(task.status) },
    showProgress: task.approval_status === 'approved' && task.status !== 'done' && (task.status !== 'todo' || task.progress > 0),
    note: task.latest_feedback || (task.incomplete_reason && `原因：${task.incomplete_reason}`) || (task.next_step && `下一步：${task.next_step}`) || task.description }
}))
</script>

<style scoped>
.task-list { list-style: none; padding: 0; margin: 0; }
.task-list li + li { border-top: 1px solid var(--jc-line-2); }
.task-row { display: grid; grid-template-columns: minmax(0, 1fr) auto 16px; align-items: center; width: 100%; gap: 14px; padding: 14px 3px; border: 0; border-radius: 4px; background: transparent; text-align: left; color: var(--jc-ink); font: inherit; cursor: pointer; }
.task-row:hover { background: var(--jc-surface-2); }.task-row:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 1px; }
.task-main { min-width: 0; }.task-main strong { display: block; font-size: 15px; font-weight: 600; line-height: 1.45; overflow-wrap: anywhere; }
.task-meta, .task-note { display: block; margin-top: 4px; color: var(--jc-ink-3); font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.task-note { color: var(--jc-ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.task-state { display: grid; justify-items: end; gap: 5px; white-space: nowrap; }
.state-label { font-size: 13px; color: var(--jc-ink-3); }.state-label.up { color: var(--jc-up); }.state-label.brand { color: var(--jc-brand); }.state-label.down { color: var(--jc-down); }.state-label.warn { color: var(--jc-warn); }
.task-percent { display: flex; align-items: center; gap: 6px; color: var(--jc-ink-2); font-size: 12px; font-variant-numeric: tabular-nums; }
.track { display: block; width: 44px; height: 4px; border-radius: 4px; overflow: hidden; background: var(--jc-line); }.track i { display: block; height: 100%; border-radius: inherit; background: var(--jc-brand); }
.chevron { color: var(--jc-ink-3); }.compact .task-row { gap: 8px; }
@media (max-width: 560px) { .task-row { gap: 8px; }.task-meta { font-size: 11px; }.track { width: 28px; } }
</style>
