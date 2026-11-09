<template>
  <section v-if="rows.length || !monthly" class="overview" :aria-label="title">
    <div class="completion">
      <div class="completion-heading">
        <h3>{{ monthly ? '本月任务完成情况' : title }}</h3>
        <strong v-if="counts.total" class="rate jc-num">{{ counts.rate }}%</strong>
      </div>
      <template v-if="counts.total">
        <div class="track" role="progressbar" aria-label="任务完成率" :aria-valuenow="counts.rate" :aria-valuetext="`已完成 ${counts.done} 项，共 ${counts.total} 项`"><i :style="{ width: `${counts.rate}%` }" /></div>
        <p class="completion-note"><span>已完成 <strong>{{ counts.done }} / {{ counts.total }}</strong> 项</span><span v-if="counts.pending">未完成 {{ counts.pending }} 项</span><span v-if="monthly && counts.carryover">含跨月 {{ counts.carryover }} 项</span></p>
        <p class="completion-note timing"><span>按时完成 <strong>{{ counts.onTime }}</strong> 项</span><span :class="{ late: counts.lateDone }">逾期完成 <strong>{{ counts.lateDone }}</strong> 项</span><span v-if="counts.unknownDone">完成日期待核对 {{ counts.unknownDone }} 项</span></p>
      </template>
      <p v-else>{{ rows.length ? '暂无已生效的任务' : '还没有安排任务' }}</p>
    </div>
    <div v-if="rows.length" class="follow-up">
      <h4>需要跟进</h4>
      <ul v-if="signals.length"><li v-for="item in signals" :key="item.key" :class="item.tone">{{ item.label }} <b>{{ counts[item.key] }}</b></li></ul>
      <p v-else>暂无需要跟进的事项</p>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { taskSummary } from './shared'

const props = defineProps({ rows: { type: Array, required: true }, title: String, monthly: Boolean })
const counts = computed(() => taskSummary(props.rows))
const signals = computed(() => [
  { key: 'overdue', label: '逾期', tone: 'urgent' }, { key: 'blocked', label: '受阻', tone: 'urgent' },
  { key: 'needsUpdate', label: '今日待反馈', tone: 'warn' }, { key: 'review', label: '待审核', tone: 'warn' },
  { key: 'rejected', label: '已退回', tone: 'warn' }, { key: 'delayPending', label: '延期待认可', tone: 'warn' },
].filter(item => counts.value[item.key] > 0))
</script>

<style scoped>
.overview { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr); gap: 15px 26px; align-items: center; padding: 15px 18px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface-2); }
h3, h4, p { margin: 0; }
h3 { color: var(--jc-ink); font-size: 15px; } h4 { color: var(--jc-ink); font-size: 13px; }
.completion-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }.rate { color: var(--jc-brand); font-size: 21px; }
.track { height: 6px; overflow: hidden; border-radius: 8px; background: var(--jc-line); margin: 7px 0; }.track i { display: block; height: 100%; border-radius: inherit; background: var(--jc-brand); transition: width .2s ease; }
p { color: var(--jc-ink-3); font-size: 13px; line-height: 1.6; }.completion-note { display: flex; flex-wrap: wrap; gap: 3px 12px; }.completion-note strong { color: var(--jc-ink); }
.timing { margin-top: 3px; }.timing .late, .timing .late strong { color: var(--jc-down); }
.follow-up { padding-left: 22px; border-left: 1px solid var(--jc-line); }.follow-up p, ul { margin: 6px 0 0; }.follow-up ul { display: flex; flex-wrap: wrap; gap: 5px 7px; padding: 0; list-style: none; }
.follow-up li { border-radius: 5px; padding: 3px 7px; font-size: 12px; white-space: nowrap; }.follow-up li b { margin-left: 2px; }.urgent { color: var(--jc-down); background: var(--jc-down-bg); }.warn { color: #80550e; background: var(--jc-warn-bg); }
@media (max-width: 630px) { .overview { grid-template-columns: minmax(0, 1fr); padding: 13px 15px; gap: 9px; }.follow-up { padding: 9px 0 0; border-left: 0; border-top: 1px solid var(--jc-line); } }
</style>
