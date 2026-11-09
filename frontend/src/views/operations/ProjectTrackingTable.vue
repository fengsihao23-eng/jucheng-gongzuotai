<template>
  <section class="tracking" :aria-labelledby="headingId">
    <h3 :id="headingId">{{ plans ? '负责人计划' : '重点任务跟进' }}</h3>
    <div v-if="rows.length" class="sheet">
      <table :aria-labelledby="headingId" role="table">
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" class="work-col">{{ plans ? '下一步工作' : '布置内容' }}</th>
            <th scope="col">进度情况</th>
            <th scope="col" :class="plans ? 'issues-col' : 'owner-col'">{{ plans ? '遇到的问题' : '负责人' }}</th>
            <th scope="col" class="date-col">{{ plans ? '计划完成' : '要求完成' }}</th>
            <th scope="col" class="result-col">是否按时</th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          <tr v-for="task in rows" :key="task.id" role="row">
            <th scope="row" class="work-cell" role="rowheader">
              <button type="button" class="open-task clip" :title="task.title" @click="emit('open-task', task)">{{ task.title }}</button>
              <small v-if="plans">{{ task.owner_name }}负责</small>
              <small v-if="task.status !== 'done' && task.next_step" class="clip" :title="task.next_step">接下来：{{ task.next_step }}</small>
            </th>
            <td class="current-cell" role="cell">
              <span class="mobile-label">进度</span>
              <span v-if="task.status !== 'done'" class="status" :class="{ blocked: task.status === 'blocked' }">
                {{ statusLabel(task) }}<template v-if="task.progress"> · {{ taskProgress(task).label }}</template>
              </span>
              <span class="clip" :class="{ muted: !current(task) }" :title="current(task)">{{ current(task) || '尚未反馈' }}</span>
            </td>
            <td class="context-cell" role="cell">
              <span class="mobile-label">{{ plans ? '问题' : '负责人' }}</span>
              <span v-if="plans" class="clip" :class="{ muted: !problems(task) }" :title="problems(task)">{{ problems(task) || '—' }}</span>
              <template v-else>{{ task.owner_name }}</template>
            </td>
            <td class="date-cell" role="cell">
              <span class="mobile-label">{{ plans ? '计划完成' : '要求完成' }}</span>
              <time :datetime="task.due_date">{{ task.due_date }}</time>
              <small v-if="task.deadline_basis !== task.due_date && task.deadline_basis">原定 {{ task.deadline_basis }}</small>
            </td>
            <td class="result-cell" role="cell">
              <span class="mobile-label">是否按时</span>
              <StatusPill :text="deadlineLabel(task)" :tone="resultTone(task)" />
              <small v-if="task.completed_on">实际 {{ task.completed_on }}</small>
              <button v-if="task.can_review_delay || task.delay_status === 'needs_reason'" class="delay-link" type="button" @click="emit('open-task', task)">{{ task.can_review_delay ? '复核延期' : '待补延期原因' }}</button>
              <small v-else-if="task.delay_status">延期{{ DELAY_STATUS[task.delay_status] }}</small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="empty">{{ plans ? '填写项目进度时，带日期的下一步工作会列在这里。' : '布置任务后，可在这里查看反馈和完成结果。' }}</p>
  </section>
</template>

<script setup>
import { useId } from 'vue'
import { DEADLINE_STATUS, DELAY_STATUS, deadlineTone, statusLabel, taskProgress } from './shared'

defineProps({ rows: { type: Array, required: true }, plans: Boolean })
const emit = defineEmits(['open-task'])
const headingId = useId()
function current(task) {
  if (!task.last_feedback_at) return task.plan_context?.progress || ''
  const text = task.latest_feedback || ''
  const reason = task.incomplete_reason && '\n未完成原因：' + task.incomplete_reason
  return task.source === 'project_plan' && reason && text.endsWith(reason) ? text.slice(0, -reason.length) : text
}
function problems(task) {
  return task.last_feedback_at ? task.incomplete_reason : task.plan_context?.problems
}
function deadlineLabel(task) {
  if (task.overdue && task.deadline_status !== 'overdue') return '节点或任务逾期'
  return DEADLINE_STATUS[task.deadline_status] || '未到期'
}
function resultTone(task) { return task.overdue || task.deadline_status === 'overdue' ? 'down' : deadlineTone(task) }
</script>

<style scoped>
.tracking + .tracking { margin-top: 23px; }
h3 { margin: 0 0 8px; font-size: 15px; color: var(--jc-ink); }
.sheet { overflow: hidden; border: 1px solid var(--jc-line); border-radius: 8px; }
table { width: 100%; table-layout: fixed; border-spacing: 0; font-size: 13px; line-height: 1.6; }
th, td { padding: 9px 11px; text-align: start; vertical-align: top; overflow-wrap: anywhere; }
thead th { color: var(--jc-ink-2); background: var(--jc-surface-2); font-weight: 600; }
tbody :is(th,td) { color: var(--jc-ink-2); border-top: 1px solid var(--jc-line-2); font-weight: 400; }
tr > * + * { border-inline-start: 1px solid var(--jc-line-2); }
tbody tr:nth-child(even) { background: var(--jc-surface-2); }
tbody tr:focus-within { background: var(--jc-brand-50); }
.work-col { width: 27%; }.issues-col { width: 21%; }.owner-col { width: 15%; }.date-col { width: 120px; }.result-col { width: 126px; }
.clip { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; white-space: pre-wrap; overflow-wrap: anywhere; }
.open-task, .delay-link { color: var(--jc-brand); padding: 0; border: none; background: transparent; text-align: start; font: inherit; cursor: pointer; }
.open-task { font-weight: 600; }.delay-link { display: block; margin-top: 3px; font-size: 12px; }
:is(.open-task,.delay-link):hover { text-decoration: underline; }
.status { font-weight: 600; color: var(--jc-ink); }.status.blocked { color: var(--jc-down); }
.status + .clip { margin-top: 3px; }
small, .muted { color: var(--jc-ink-3); } small { display: block; margin-top: 3px; font-size: 11px; }
time { font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--jc-ink); }
.empty { margin: 0; padding: 10px 12px; color: var(--jc-ink-3); background: var(--jc-surface-2); border-radius: 7px; font-size: 13px; }
.mobile-label { display: none; }
@media (max-width: 920px) {
  table, tbody { display: block; }
  thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  tbody tr { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 12px; padding: 11px 12px; }
  tbody tr + tr { border-top: 1px solid var(--jc-line-2); }
  tbody :is(th,td) { display: block; padding: 0; border: 0; }
  .work-cell, .current-cell, .context-cell { grid-column: 1 / -1; }
  .open-task { font-size: 14px; }
  .mobile-label { display: inline-block; margin-right: 6px; color: var(--jc-ink-3); font-size: 11px; }
  :is(.date-cell,.result-cell) .mobile-label { display: block; }
}
</style>
