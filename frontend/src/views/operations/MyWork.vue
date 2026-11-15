<template>
  <section v-if="data && (data.projects.length || data.todos.length)" class="my-work" aria-label="我的工作入口">
    <div class="heading"><div><h2>{{ data.can_manage ? '项目与当前工作' : '我的项目与工作' }}</h2><p>从这里接任务、回复要求、查看进展。</p></div><el-button @click="load">刷新工作</el-button></div>
    <div class="work-counts"><button @click="filter = 'all'">我的待办 <b>{{ data.stats.todos }}</b></button><button @click="filter = 'needs_update'">待反馈 <b>{{ data.stats.needs_feedback }}</b></button><button @click="filter = 'pending_replies'">待回复 <b>{{ data.stats.replies }}</b></button><button @click="filter = 'review'">待审核 <b>{{ data.stats.reviews }}</b></button></div>
    <div class="project-links"><router-link v-for="p in data.projects" :key="p.id" :to="`/operations/${p.id}`"><b>{{ p.name }}</b><span>{{ p.mine_pending }} 项待我处理 · {{ p.stats.done }}/{{ p.stats.total }} 已完成</span></router-link></div>
    <ul v-if="todos.length" class="work-list"><li v-for="t in todos.slice(0, 8)" :key="t.id"><button @click="openTask(t)"><span><b>{{ t.title }}</b><small>{{ t.project_name }} · 截止 {{ t.due_date }}</small></span><StatusPill :text="t.pending_replies ? '待我回复' : t.can_review || t.can_review_delay ? '待我审核' : t.overdue ? '已逾期' : t.needs_update ? '待反馈' : '进行中'" :tone="t.overdue ? 'warn' : 'brand'" /></button></li></ul>
    <p v-else class="jc-muted jc-small">当前筛选下没有待办，可进入项目查看已完成事项。</p>
    <el-button v-if="data.todos.length > 8" link type="primary" @click="router.push('/operations?view=todos&mine=1')">查看全部待办</el-button>
    <details v-if="data.recent.length"><summary>最近进展</summary><ul class="work-list"><li v-for="t in data.recent" :key="t.id"><button @click="openTask(t)"><span><b>{{ t.project_name }} · {{ t.title }}</b><small>{{ t.owner_name }} · {{ datetime(t.last_feedback_at) }}</small><p>{{ t.latest_feedback }}</p></span></button></li></ul></details>
  </section>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import api from '@/api/client'
import { datetime } from '@/utils/format'
const router = useRouter(), data = ref(null), filter = ref('all')
let revision = 0
async function load() { const current = ++revision; try { const result = await api.get('/portal/work', null, { silent: true }); if (current === revision) data.value = result } catch { if (current === revision) data.value = null } }
const todos = computed(() => (data.value?.todos || []).filter(t => filter.value === 'all' || (filter.value === 'review' ? t.can_review || t.can_review_delay : filter.value === 'needs_update' ? t.needs_update && t.can_feedback : t[filter.value])))
function openTask(t) { router.push(`/operations/${t.project_id}?task=${t.id}`) }
onMounted(load); useBusinessRefresh(load, { interval: 15000 })
</script>
<style scoped>
.my-work { background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 9px; padding: 20px; margin-bottom: 24px; }.heading { display: flex; gap: 12px; justify-content: space-between; align-items: center; }.heading h2 { font-size: 20px; margin: 0; }.heading p { color: var(--jc-ink-3); font-size: 13px; }.work-counts { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 10px; }.work-counts button { padding: 12px; border: 1px solid var(--jc-line); background: var(--jc-bg); border-radius: 6px; text-align: left; color: var(--jc-ink-2); cursor: pointer; }.work-counts b { display: block; font-size: 23px; color: var(--jc-brand); margin-top: 6px; }.project-links { display: flex; flex-wrap: wrap; gap: 10px; margin: 17px 0; }.project-links a { display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--jc-line); padding: 12px 15px; border-radius: 6px; color: var(--jc-brand); text-decoration: none; }.project-links span { font-size: 12px; color: var(--jc-ink-3); }.work-list { padding: 0; list-style: none; }.work-list button { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 15px; text-align: left; background: none; border: 0; border-top: 1px solid var(--jc-line); padding: 12px 0; color: var(--jc-ink); cursor: pointer; font: inherit; }.work-list small { display: block; color: var(--jc-ink-3); margin-top: 5px; }.work-list p { font-size: 13px; margin: 6px 0 0; white-space: pre-wrap; }summary { cursor: pointer; color: var(--jc-ink-2); }@media(max-width:560px) { .my-work { padding: 14px; }.work-counts { grid-template-columns: repeat(2,minmax(0,1fr)); }.heading { align-items: start; }.heading h2 { font-size: 18px; } }
</style>
