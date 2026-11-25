<template>
  <div class="board-status" role="group" aria-label="按任务状态筛选">
    <button v-for="(label, value) in TASK_FILTERS" :key="value" type="button" :aria-pressed="status === value" :data-status="value"
      :title="value === 'ongoing' ? '包含待开始、受阻和待审核等未完成任务' : undefined"
      @click="status = value"><i /><span>{{ label }}</span><b class="jc-num">{{ groups[value].length }}</b></button>
  </div>
</template>

<script setup>
import { TASK_FILTERS } from '@/utils/taskFeedback'

defineProps({ groups: { type: Object, required: true } })
const status = defineModel({ type: String, required: true })
</script>

<style scoped>
.board-status { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 15px; }
.board-status button { --tone: var(--jc-brand); display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--jc-line); border-radius: 7px; padding: 7px 10px; font: inherit; font-size: 12px; color: var(--jc-ink-2); background: var(--jc-bg-2, #f9f8f5); cursor: pointer; }
.board-status button[data-status="done"] { --tone: var(--jc-up); }
.board-status button[data-status="pending"] { --tone: var(--jc-warn); }
.board-status button[data-status="overdue"] { --tone: var(--jc-down); }
.board-status button[aria-pressed="true"], .board-status button:hover { border-color: var(--jc-brand); color: var(--jc-brand); }
.board-status i { width: 7px; height: 7px; border-radius: 50%; background: var(--tone); }
.board-status b { font-size: 15px; color: var(--jc-ink); }
</style>
