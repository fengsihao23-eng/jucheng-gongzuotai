<template>
  <button type="button" class="task-card" :data-status="card.state">
    <div class="card-top">
      <StatusPill :text="card.status" :tone="card.tone" dot />
      <span v-for="badge in card.badges" :key="badge.label" class="flag" :data-tone="badge.tone">{{ badge.label }}</span>
    </div>
    <strong class="title">{{ card.title }}</strong>
    <span v-if="card.context" class="context">{{ card.context }}</span>
    <span class="latest">{{ card.latest }}</span>
    <span v-if="card.problem" class="next jc-down">问题：{{ card.problem }}</span>
    <span v-if="card.nextStep" class="next">下一步：{{ card.nextStep }}</span>
    <template v-if="card.progress">
      <span class="progress-label"><span>任务进度</span><b class="jc-num">{{ card.progress.label }}</b></span>
      <span v-if="card.progress.percent !== null" class="progress-track" aria-hidden="true"><i :style="{ width: `${card.progress.percent}%` }" /></span>
      <span v-if="card.nodes.length" class="node-caption node-count">
        <span class="nodes" aria-hidden="true"><i v-for="node in card.nodes" :key="node.id" :class="{ complete: node.completed, late: node.overdue && !node.completed }" /></span>
        <span>节点 {{ completedNodes }} / {{ card.nodes.length }}</span>
      </span>
    </template>
    <span v-if="nextNode" class="node-caption">下一节点：{{ nextNode.name }} <span class="node-date">· {{ nextNode.due }}</span></span>
    <span v-if="card.feedbackDeadline" class="node-caption" :class="{ 'jc-down': card.feedbackOverdue }">反馈截止 {{ card.feedbackDeadline }}</span>
    <span class="node-caption">{{ card.feedbackLabel }}</span>
    <span v-if="card.pendingReplies" class="jc-down">{{ card.pendingReplies }} 条要求待我回复</span>
    <span v-if="card.carryover" class="carryover">从 {{ card.carryover }} 延续</span>
    <div class="card-foot">
      <span class="owner">{{ card.owner || '未分配' }}<small>截止 {{ card.due || '未登记' }}</small><small v-if="card.completedOn">完成 {{ card.completedOn }}</small></span>
      <span class="action">{{ card.action }} <span aria-hidden="true">→</span></span>
    </div>
  </button>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ card: { type: Object, required: true } })
const completedNodes = computed(() => props.card.nodes.filter(node => node.completed).length)
const nextNode = computed(() => props.card.nodes.find(node => !node.completed))
</script>

<style scoped>
:global(.project-task-grid) { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr)); gap: 14px; }
:global(.project-task-grid.single) { grid-template-columns: minmax(0, 1fr); }
.task-card { --state: var(--jc-brand); display: flex; flex-direction: column; align-items: stretch; min-width: 0; padding: 14px 15px 13px; position: relative; border: 1px solid var(--jc-line); border-top: 3px solid var(--state); border-radius: var(--jc-radius); background: var(--jc-surface); color: var(--jc-ink); font: inherit; text-align: left; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.task-card[data-status="done"] { --state: var(--jc-up); }.task-card[data-status="blocked"], .task-card[data-status="rejected"] { --state: var(--jc-down); }.task-card[data-status="todo"] { --state: #aaa89f; }.task-card[data-status="review"] { --state: var(--jc-warn); }
.task-card:hover, .task-card:focus-visible { border-color: var(--jc-brand); box-shadow: var(--jc-shadow-pop); }
.card-top, .progress-label, .card-foot { display: flex; align-items: center; gap: 7px; }
.card-top { flex-wrap: wrap; }.flag, .carryover { font-size: 11px; border: 1px solid var(--jc-line); border-radius: 4px; padding: 1px 5px; }.flag { color: var(--jc-warn); }.flag[data-tone="brand"] { color: var(--jc-brand); }.flag[data-tone="down"] { color: var(--jc-down); }.carryover { align-self: flex-start; color: var(--jc-ink-2); margin-top: 6px; }
.title { display: block; font-size: 16px; line-height: 1.45; margin: 10px 0 3px; overflow-wrap: anywhere; }
.context, .latest, .next, .node-caption { color: var(--jc-ink-2); font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; }.context { color: var(--jc-ink-3); }.latest { margin-top: 7px; }.latest, .next { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }.next { margin-top: 3px; }
.progress-label { justify-content: space-between; font-size: 12px; color: var(--jc-ink-2); padding-top: 12px; margin-top: auto; }.progress-label b { color: var(--jc-ink); }
.progress-track { display: block; height: 6px; border-radius: 6px; background: var(--jc-line-2); overflow: hidden; margin-top: 5px; }.progress-track i { display: block; height: 100%; border-radius: inherit; background: var(--state); transition: width .2s ease; }
.node-caption { margin-top: 7px; white-space: normal; }.node-count { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }.node-date { display: inline-block; white-space: nowrap; }
.nodes { display: inline-flex; flex-wrap: wrap; gap: 3px; }.nodes i { width: 7px; height: 7px; border: 1px solid #aaa89f; border-radius: 50%; }.nodes .complete { background: var(--jc-up); border-color: var(--jc-up); }.nodes .late { border-color: var(--jc-down); }
.card-foot { margin-top: 12px; border-top: 1px solid var(--jc-line-2); padding-top: 9px; justify-content: space-between; font-size: 12px; }.owner { min-width: 0; overflow-wrap: anywhere; }.owner small { display: block; font-size: 11px; color: var(--jc-ink-3); margin-top: 2px; }.action { color: var(--jc-brand); flex-shrink: 0; }
</style>
