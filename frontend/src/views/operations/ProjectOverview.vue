<template>
  <section v-if="attention" class="attention-line" aria-label="需要关注">
    <button type="button" class="attention-task" @click="emit('open-task', attention.task)">
      <StatusPill :text="attention.label" :tone="attention.tone" dot />
      <span class="attention-copy"><span class="attention-project">{{ attention.task.project_name }} · </span><strong>{{ attention.task.title }}</strong></span>
      <span class="attention-arrow" aria-hidden="true">›</span>
    </button>
    <el-button v-if="showTaskList" link type="primary" @click="emit('show-todos', attention.filter)">查看待办</el-button>
  </section>
  <div class="filters">
    <el-input v-model="keyword" placeholder="搜索项目或目标" aria-label="搜索运营项目" clearable class="search" @change="emit('filter')" />
    <el-select v-model="category" placeholder="全部分类" aria-label="运营项目分类" clearable :value-on-clear="''" class="category" @change="emit('filter')">
      <el-option v-for="name in data.categories" :key="name" :value="name" :label="name" />
    </el-select>
    <el-checkbox v-model="keyOnly" @change="emit('filter')">仅重点项目</el-checkbox>
  </div>
  <div class="project-grid">
    <button v-for="project in data.items" :key="project.id" type="button" class="project-card"
      @click="router.push({ name: 'operation-detail', params: { id: project.id } })" @contextmenu="openMenu($event, project)">
      <div class="jc-row between"><span class="jc-small jc-muted">{{ project.category }}</span><StatusPill v-if="project.is_key" text="重点项目" tone="brand" /></div>
      <h3>{{ project.name }}</h3>
      <p class="objective">{{ project.objective || '目标待补充，进入项目安排首批重点工作。' }}</p>
      <div class="jc-row between jc-small"><span>负责人 · {{ project.owner_name }}</span><span class="jc-muted">{{ PROJECT_STATUS[project.status] }}</span></div>
      <div class="progress-line"><i :style="{ width: (project.stats.total ? project.stats.done / project.stats.total * 100 : 0) + '%' }" /></div>
      <div class="jc-row between jc-small"><span>已完成 {{ project.stats.done }} / {{ project.stats.total }}</span><span :class="project.stats.overdue ? 'jc-down' : 'jc-muted'">{{ project.stats.overdue ? `${project.stats.overdue} 项逾期` : '查看任务与进展 →' }}</span></div>
    </button>
  </div>
  <el-empty v-if="data.today && !data.items.length && !loading" :description="emptyText" :image-size="100">
    <el-button v-if="!filtered && data.can_create" type="primary" @click="emit('create')">建立智慧校园项目</el-button>
  </el-empty>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { PROJECT_STATUS } from './shared'

const props = defineProps({ data: { type: Object, required: true }, loading: Boolean, projectMenu: Boolean, showTaskList: Boolean })
const emit = defineEmits(['filter', 'open-task', 'show-todos', 'project-menu', 'create'])
const keyword = defineModel('keyword', { type: String, required: true })
const category = defineModel('category', { type: String, required: true })
const keyOnly = defineModel('keyOnly', { type: Boolean, required: true })
const router = useRouter()
const filtered = computed(() => Boolean(keyword.value.trim() || category.value || keyOnly.value))
const emptyText = computed(() => {
  if (filtered.value) return '暂无符合条件的运营项目'
  return props.data.can_create ? '暂无运营项目' : '暂无参与的运营项目，请由项目负责人加入团队'
})
const attention = computed(() => {
  const projectIds = new Set(props.data.items.map(project => project.id))
  const tasks = props.data.todos.filter(task => projectIds.has(task.project_id))
  const overdue = tasks.find(task => task.overdue)
  if (overdue) return { task: overdue, label: '已逾期', tone: 'down' }
  const blocked = tasks.find(task => task.status === 'blocked')
  if (blocked) return { task: blocked, label: '受阻', tone: 'down' }
  const delay = props.data.delay_review_inbox.find(task => projectIds.has(task.project_id))
  if (delay) return { task: delay, label: '延期待认可', tone: 'warn', filter: 'delay_review' }
  const missedFeedback = tasks.find(task => task.feedback_status === 'overdue')
  if (missedFeedback) return { task: missedFeedback, label: '逾期未反馈', tone: 'down', filter: 'feedback_overdue' }
  const needsUpdate = tasks.find(task => task.needs_update)
  return needsUpdate ? { task: needsUpdate, label: '今日待反馈', tone: 'warn' } : null
})
function openMenu(event, project) {
  if (!props.projectMenu || !props.data.can_delete) return
  event.preventDefault()
  emit('project-menu', event, project)
}
</script>

<style scoped>
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-bottom: 20px; }
.search { width: min(240px, 100%); }
.category { width: min(170px, 100%); }
.attention-line { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; padding: 4px 10px; border-radius: 7px; background: var(--jc-surface-2); }
.attention-task { display: flex; flex: 1; min-width: 0; align-items: center; gap: 8px; border: 0; border-radius: 4px; background: none; color: var(--jc-ink); text-align: left; font: inherit; padding: 4px 0; cursor: pointer; }
.attention-task :deep(.jc-pill) { flex-shrink: 0; }
.attention-copy { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.attention-task:hover strong { color: var(--jc-brand); }
.attention-task:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 2px; }
.attention-task strong { font-weight: 600; }
.attention-project, .attention-arrow { color: var(--jc-ink-3); }
.attention-arrow { font-size: 17px; }
.attention-line .el-button { flex-shrink: 0; }
.project-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr)); gap: 18px; }
.project-card { border: 1px solid var(--jc-line); background: var(--jc-surface); padding: 22px; border-radius: var(--jc-radius); text-align: left; color: var(--jc-ink); cursor: pointer; font: inherit; transition: border-color .15s; overflow-wrap: anywhere; }
.project-card:hover { border-color: var(--jc-brand); }
.project-card h3 { font-family: var(--jc-font-serif); font-size: 22px; margin: 18px 0 8px; }
.objective { color: var(--jc-ink-2); font-size: 13px; line-height: 1.7; min-height: 44px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin: 0 0 24px; }
.progress-line { height: 4px; background: var(--jc-line-2); margin: 18px 0 10px; border-radius: 4px; overflow: hidden; }
.progress-line i { height: 100%; display: block; background: var(--jc-brand); }
@media (max-width: 560px) {
  .attention-line { gap: 6px; padding-inline: 7px; }
  .attention-task { gap: 6px; }
  .attention-project { display: none; }
}
</style>
