<template>
  <section class="overview" :aria-label="title">
    <div class="overview-header">
      <div><h3>{{ title }}</h3><p>{{ caption }}</p></div>
      <span v-if="updatedAt" class="sync"><i /> 更新于 {{ updatedAt }}</span>
    </div>
    <div class="overview-body">
      <div class="completion">
        <div class="ring" aria-hidden="true">
          <svg viewBox="0 0 100 100"><circle class="ring-track" cx="50" cy="50" r="43" pathLength="100" /><circle v-if="counts.rate" class="ring-value" cx="50" cy="50" r="43" pathLength="100" :stroke-dasharray="`${counts.rate} 100`" /></svg>
          <strong class="jc-num">{{ counts.rate == null ? '—' : `${counts.rate}%` }}</strong>
        </div>
        <div><b>任务完成率</b><p class="completion-number">已完成 <strong>{{ counts.done }}</strong> / {{ counts.total }} 项</p><p>未完成 {{ counts.pending }} 项</p></div>
      </div>
      <div class="distribution">
        <h4>执行状态</h4>
        <div class="segments" aria-hidden="true">
          <i v-for="status in statuses" v-show="counts[status.key]" :key="status.key" :class="status.key" :style="{ flexGrow: counts[status.key] }" />
        </div>
        <div class="legend"><span v-for="status in statuses" :key="status.key"><i :class="status.key" />{{ status.label }} <b>{{ counts[status.key] }}</b></span></div>
      </div>
      <div class="attention">
        <h4>需要跟进</h4>
        <dl>
          <div><dt>已逾期</dt><dd :class="{ urgent: counts.overdue }">{{ counts.overdue }}</dd></div>
          <div><dt>今日待反馈</dt><dd :class="{ warn: counts.needsUpdate }">{{ counts.needsUpdate }}</dd></div>
          <div><dt>待审核</dt><dd :class="{ warn: counts.review }">{{ counts.review }}</dd></div>
          <div><dt>已退回</dt><dd :class="{ urgent: counts.rejected }">{{ counts.rejected }}</dd></div>
        </dl>
      </div>
    </div>
    <div class="overview-footer">
      <span>公司安排 <b>{{ counts.company }}</b></span><span>自行安排 <b>{{ counts.self }}</b></span><span v-if="monthly">跨月延续 <b>{{ counts.carryover }}</b></span>
      <span class="rule">完成率按已生效任务计算</span>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { TASK_STATUS, taskSummary } from './shared'

const props = defineProps({ rows: { type: Array, required: true }, title: String, caption: String, updatedAt: String, monthly: Boolean })
const counts = computed(() => taskSummary(props.rows))
const statuses = ['done', 'doing', 'todo', 'blocked'].map(key => ({ key, label: TASK_STATUS[key] }))
</script>

<style scoped>
.overview { padding: 19px 22px 13px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-bg-2, #f9f8f5); }
.overview-header { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 8px; }
h3, h4, p { margin: 0; }
h3 { color: var(--jc-ink); font-size: 17px; line-height: 1.45; }
p, .rule, .sync { color: var(--jc-ink-3); font-size: 12px; line-height: 1.65; }
.sync { display: inline-flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
.sync i, .legend i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--jc-up); }
.overview-body { display: grid; grid-template-columns: minmax(174px, .95fr) minmax(152px, 1fr) minmax(160px, .95fr); gap: 22px; align-items: center; padding: 16px 0; }
.completion { display: flex; align-items: center; gap: 13px; min-width: 0; }
.completion b, h4 { font-size: 13px; color: var(--jc-ink); }
.completion-number strong { color: var(--jc-ink); font-size: 15px; }
.ring { flex: 0 0 78px; position: relative; display: grid; place-items: center; aspect-ratio: 1; color: var(--jc-brand); }
.ring svg { position: absolute; inset: 0; width: 100%; transform: rotate(-90deg); }
.ring circle { fill: none; stroke-width: 8; }
.ring-track { stroke: var(--jc-line); }
.ring-value { stroke: currentColor; stroke-linecap: round; transition: stroke-dasharray .25s ease; }
.ring strong { font-size: 19px; }
.segments { height: 8px; background: var(--jc-line); border-radius: 10px; overflow: hidden; display: flex; gap: 2px; margin: 10px 0; }
.segments i { min-width: 3px; }
i.done { background: var(--jc-up); } i.doing { background: var(--jc-brand); } i.todo { background: #aaa89f; } i.blocked { background: var(--jc-down); }
.legend { display: flex; flex-wrap: wrap; gap: 5px 10px; color: var(--jc-ink-2); font-size: 12px; }
.legend span { display: inline-flex; gap: 4px; align-items: center; white-space: nowrap; }
.attention dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 10px; margin: 8px 0 0; }
.attention dl div { display: flex; justify-content: space-between; gap: 4px; border: 1px solid var(--jc-line-2); background: var(--jc-surface); border-radius: 6px; padding: 5px 7px; font-size: 12px; }
dt { color: var(--jc-ink-2); } dd { margin: 0; font-weight: 650; } .urgent { color: var(--jc-down); } .warn { color: var(--jc-warn); }
.overview-footer { display: flex; flex-wrap: wrap; gap: 6px 18px; border-top: 1px solid var(--jc-line); padding-top: 10px; font-size: 12px; color: var(--jc-ink-2); }
.overview-footer .rule { margin-left: auto; }
@media (max-width: 1050px) { .overview-body { grid-template-columns: minmax(170px, 1fr) minmax(135px, 1fr); gap: 14px; } .attention { grid-column: 1 / -1; } .attention dl { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (max-width: 580px) { .overview { padding: 14px; } .overview-body { grid-template-columns: minmax(0, 1fr); } .attention dl { grid-template-columns: repeat(2, minmax(0, 1fr)); } .overview-footer .rule { margin-left: 0; } }
</style>
