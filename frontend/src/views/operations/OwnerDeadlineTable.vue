<template>
  <details v-if="owners.length" class="owner-breakdown">
    <summary>按负责人查看 <span>{{ owners.length }} 人</span></summary>
    <div class="owner-scroll">
      <table aria-label="负责人任务统计">
        <thead><tr><th scope="col">负责人</th><th scope="col">任务数</th><th scope="col">已完成</th><th scope="col">按时完成</th><th scope="col">逾期完成</th><th v-if="hasUnknown" scope="col">完成日期待核对</th><th scope="col">未完成</th></tr></thead>
        <tbody><tr v-for="owner in owners" :key="owner.id">
          <th scope="row">{{ owner.name }}</th><td>{{ owner.total }}</td><td>{{ owner.done }}</td><td>{{ owner.onTime }}</td>
          <td :class="{ late: owner.lateDone }">{{ owner.lateDone }}</td><td v-if="hasUnknown">{{ owner.unknownDone }}</td><td>{{ owner.pending }}</td>
        </tr></tbody>
      </table>
    </div>
  </details>
</template>

<script setup>
import { computed } from 'vue'
import { taskSummary } from './shared'

const props = defineProps({ rows: { type: Array, required: true } })
const owners = computed(() => {
  const groups = new Map()
  for (const task of props.rows) {
    if (task.approval_status !== 'approved') continue
    if (!groups.has(task.owner_id)) groups.set(task.owner_id, [])
    groups.get(task.owner_id).push(task)
  }
  return [...groups.entries()].map(([id, tasks]) => ({ id, name: tasks[0].owner_name, ...taskSummary(tasks) }))
})
const hasUnknown = computed(() => owners.value.some(owner => owner.unknownDone))
</script>

<style scoped>
.owner-breakdown { margin-top: 10px; }.owner-breakdown summary { width: fit-content; padding: 3px 0; cursor: pointer; color: var(--jc-brand); font-size: 13px; font-weight: 600; }
.owner-breakdown summary span { color: var(--jc-ink-3); font-weight: 400; margin-left: 4px; }
.owner-scroll { overflow-x: auto; margin-top: 6px; border: 1px solid var(--jc-line); border-radius: 6px; }
table { width: 100%; border-spacing: 0; font-size: 13px; font-variant-numeric: tabular-nums; } th, td { padding: 7px 9px; text-align: right; }
th:first-child { text-align: left; } thead th { background: var(--jc-surface-2); color: var(--jc-ink-2); font-weight: 600; }
tbody th, tbody td { border-top: 1px solid var(--jc-line-2); }.late { color: var(--jc-down); font-weight: 600; }
@media(max-width: 530px) { table { font-size: 12px; } th, td { padding: 6px 5px; } }
</style>
