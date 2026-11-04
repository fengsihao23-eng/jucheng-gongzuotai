<!-- 工作过程默认收起，用一行呈现当前步骤；按需展开时间线，结束后自动收起。 -->
<template>
  <div class="proc execution-card" :class="[state, { open }]" :data-task-id="task.id" :data-task-state="task.state">
    <button type="button" class="proc-hd" :aria-expanded="open" :aria-controls="`process-${task.id}`" @click="open = !open">
      <span class="ic" aria-hidden="true">
        <i v-if="live" class="spin" />
        <svg v-else-if="ok" viewBox="0 0 16 16" width="14" height="14"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
        <svg v-else viewBox="0 0 16 16" width="14" height="14"><path d="M8 4.5v4.2M8 11.3v.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>
      </span>
      <span class="ttl" :title="title">{{ title }}</span>
      <span class="meta">{{ meta }}</span>
      <svg class="chev" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M5 6.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </button>
    <div v-show="open" :id="`process-${task.id}`" class="proc-bd">
      <ol v-if="task.steps?.length" class="steps">
        <li v-for="(s, i) in task.steps" :key="i" :class="s.state">
          <span class="dot" aria-hidden="true" />
          <div class="body">
            <span class="lbl">{{ s.label }}</span>
            <span v-if="s.message" class="msg">{{ s.message }}</span>
          </div>
          <span class="st">{{ s.state === 'running' ? '进行中' : s.state === 'succeeded' ? '完成' : '待处理' }}</span>
        </li>
      </ol>
      <p v-else class="empty">{{ live ? '正在理解你的需求，准备执行…' : '本轮没有额外的执行步骤。' }}</p>
      <slot />
    </div>
    <p v-if="pollError" class="warn" role="status">连接短暂中断，正在重新获取进度…</p>
    <p v-if="task.runtime_event === 'auto_compaction_start'" class="warn" role="status">正在整理较早的上下文，随后继续…</p>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ task: { type: Object, required: true }, live: Boolean, pollError: Boolean, labels: { type: Object, default: () => ({}) } })
const open = ref(false)
watch(() => props.live, (v) => { if (!v) open.value = false })
const state = computed(() => props.task.state || 'running')
const ok = computed(() => state.value === 'succeeded')
const steps = computed(() => props.task.steps || [])
const title = computed(() => {
  if (props.live) {
    const cur = [...steps.value].reverse().find((s) => s.state === 'running')
    return cur ? cur.label : props.labels[state.value] || '正在处理'
  }
  return props.labels[state.value] || '已结束'
})
const meta = computed(() => {
  const parts = []
  if (steps.value.length) parts.push(`${steps.value.length} 步`)
  const sec = Math.round((props.task.updated_at || 0) - (props.task.created_at || 0))
  if (!props.live && sec > 0) parts.push(sec >= 60 ? `${Math.floor(sec / 60)} 分 ${sec % 60} 秒` : `${sec} 秒`)
  return parts.join(' · ')
})
</script>

<style scoped>
.proc { margin: 0 0 20px; font-size: 13px; }
.proc-hd { display: flex; align-items: center; gap: 7px; width: fit-content; max-width: 100%; min-height: 30px; margin-left: -5px; padding: 4px 6px; border: 0; border-radius: 7px; background: transparent; color: var(--jc-ink-3); font: inherit; text-align: left; cursor: pointer; }
.proc-hd:hover { background: #f5f5f5; color: var(--jc-ink); }
.ic { flex-shrink: 0; display: grid; place-items: center; width: 15px; height: 15px; color: var(--jc-up); }
.failed .ic, .cancelled .ic, .waiting_input .ic, .waiting_admin .ic, .awaiting_confirmation .ic, .partial .ic { color: var(--jc-warn); }
.spin { width: 12px; height: 12px; border: 1.6px solid var(--jc-line); border-top-color: var(--jc-brand); border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.ttl { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.running .ttl { color: var(--jc-ink-2); }
.meta { color: var(--jc-ink-3); font-size: 12px; white-space: nowrap; }
.chev { color: var(--jc-ink-3); transition: transform .15s; }
.open .chev { transform: rotate(180deg); }
.proc-bd { margin-top: 8px; padding: 0 12px 2px 30px; }
.steps { list-style: none; margin: 0; padding: 0; position: relative; }
.steps::before { content: ''; position: absolute; left: -17px; top: 8px; bottom: 8px; width: 1px; background: var(--jc-line); }
.steps li { position: relative; display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; }
.dot { position: absolute; left: -20px; top: 12px; width: 7px; height: 7px; border-radius: 50%; background: var(--jc-line); box-shadow: 0 0 0 3px #fff; }
.succeeded .dot { background: var(--jc-up); }
.running .dot { background: var(--jc-brand); animation: pulse 1.2s ease-in-out infinite; }
.pending .dot { background: var(--jc-warn); }
@keyframes pulse { 50% { opacity: .35; } }
.body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.lbl { color: var(--jc-ink); }
.msg { color: var(--jc-ink-3); font-size: 12px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.st { color: var(--jc-ink-3); font-size: 11px; white-space: nowrap; padding-top: 2px; }
.empty, .warn { margin: 6px 0; color: var(--jc-ink-3); font-size: 12px; }
.warn { color: var(--jc-warn); }
@media (prefers-reduced-motion: reduce) { .spin, .running .dot { animation: none; } .chev { transition: none; } }
</style>
