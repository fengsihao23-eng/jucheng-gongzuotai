<template>
  <section v-if="rows.length" class="task-overview" :aria-label="title">
    <div v-if="counts.total" class="completion">
      <span>已完成 <strong>{{ counts.done }} / {{ counts.total }}</strong> 项</span>
      <div class="track" role="progressbar" aria-label="任务完成率" :aria-valuenow="counts.rate"><i :style="{ width: `${counts.rate}%` }" /></div>
      <strong class="rate">{{ counts.rate }}%</strong>
    </div>
    <div v-if="attention" class="attention">{{ attention }} 项需关注</div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { taskAttention, taskSummary } from './shared'
const props = defineProps({ rows: { type: Array, required: true }, title: String })
const counts = computed(() => taskSummary(props.rows))
const attention = computed(() => props.rows.filter(taskAttention).length)
</script>

<style scoped>
.task-overview { display: flex; gap: 8px 18px; align-items: center; flex-wrap: wrap; padding: 10px 0 12px; border-bottom: 1px solid var(--jc-line-2); color: var(--jc-ink-2); font-size: 13px; }
.completion { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.completion strong { color: var(--jc-ink); }.completion .rate { color: var(--jc-brand); font-variant-numeric: tabular-nums; }
.track { width: clamp(34px, 8vw, 96px); height: 5px; border-radius: 6px; background: var(--jc-line); overflow: hidden; }.track i { display: block; height: 100%; background: var(--jc-brand); }
.attention { color: var(--jc-warn); }
</style>
