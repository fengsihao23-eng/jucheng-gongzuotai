<template>
  <div class="jc-page" v-loading="loading">
    <el-button link @click="router.push('/operations')">← 运营项目</el-button>
    <template v-if="project">
      <PageHeader :eyebrow="`${project.category} · ${PROJECT_STATUS[project.status]}`" :title="project.name" :desc="project.objective || '明确阶段目标，推进关键任务。'">
        <el-button @click="summary">进展汇总</el-button>
        <el-button @click="ask">问小聚</el-button>
        <el-button v-if="project.can_manage" @click="projectForm = true">项目设置</el-button>
        <el-button v-if="project.can_propose && project.status === 'active'" @click="newSelfTask">自行安排</el-button>
        <el-button v-if="project.can_manage && project.status === 'active'" type="primary" @click="newTask">布置任务</el-button>
      </PageHeader>
      <div class="jc-grid c4 operation-kpis">
        <Kpi label="待完成任务" :value="project.stats.pending" unit="项" :hint="`已完成 ${project.stats.done} 项 · 共 ${project.stats.total} 项`" />
        <Kpi label="逾期 / 受阻" :value="`${project.stats.overdue} / ${project.stats.blocked}`" unit="项" hint="优先处理需要协调的事项" />
        <Kpi label="今日待反馈" :value="project.stats.needs_update" unit="项" :hint="`另有 ${project.stats.awaiting_approval || 0} 项待审核`" />
        <Kpi label="参与团队" :value="project.members.length" unit="人" :hint="`负责人 · ${project.owner_name}`" />
      </div>
      <Panel class="mt-16">
        <el-tabs v-model="tab">
          <el-tab-pane label="月度任务" name="monthly"><MonthlyPlan :project-id="project.id" :today="project.today" :refresh-key="refreshKey" @open-task="openTask" /></el-tab-pane>
          <el-tab-pane label="重点任务" name="tasks">
            <div class="filters">
              <el-input v-model="query" placeholder="搜索任务 / 学校 / 负责人" clearable style="width: 240px" />
              <el-select v-model="status" placeholder="全部状态" clearable style="width: 140px"><el-option v-for="(v, k) in TASK_STATUS" :key="k" :label="v" :value="k" /></el-select>
              <el-checkbox v-model="mine">我负责的</el-checkbox><el-checkbox v-model="keyOnly">仅重点</el-checkbox><el-checkbox v-model="attention">需要关注</el-checkbox>
            </div>
            <el-table :data="filteredTasks" :row-class-name="({ row }) => row.overdue ? 'is-alert' : ''" @row-click="openTask">
              <el-table-column label="任务 / 最新进展" min-width="280"><template #default="{ row }"><div class="jc-row"><StatusPill v-if="row.priority === 'key'" text="重点" tone="brand" /><b>{{ row.title }}</b></div><p class="task-next">{{ row.latest_feedback || row.description || '尚未反馈进展' }}</p><p v-if="row.next_step" class="task-next">下一步：{{ row.next_step }}</p><span v-if="row.school_name" class="jc-muted jc-small">{{ row.school_name }}</span></template></el-table-column>
              <el-table-column prop="owner_name" label="负责人" width="100" />
              <el-table-column label="截止 / 提醒" width="150"><template #default="{ row }"><div>{{ row.due_date }}</div><span v-if="row.overdue" class="jc-down jc-small">已逾期</span><span v-else-if="row.needs_update" class="jc-muted jc-small">今日待反馈</span></template></el-table-column>
              <el-table-column label="进度 / 审核" width="140"><template #default="{ row }"><StatusPill :text="row.approval_status === 'approved' ? TASK_STATUS[row.status] : APPROVAL_STATUS[row.approval_status]" :tone="tone(row.status)" /><el-progress :percentage="row.progress" :stroke-width="4" color="#1B3275" class="mt-8" /><span v-if="row.nodes.length" class="jc-small jc-muted">节点 {{ row.nodes.filter(n => n.completed_at).length }} / {{ row.nodes.length }}</span></template></el-table-column>
              <el-table-column width="110"><template #default="{ row }"><el-button type="primary" link @click.stop="openTask(row)">{{ row.can_feedback ? '反馈进展' : '查看记录' }}</el-button></template></el-table-column>
              <template #empty><el-empty description="把本周最重要的工作安排在这里"><el-button v-if="project.can_manage && project.status === 'active'" type="primary" @click="newTask">布置第一项任务</el-button></el-empty></template>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="学校与经营资料" name="schools"><SchoolData :project="project" @saved="load" @open-task="openTask" /></el-tab-pane>
          <el-tab-pane label="设备产品库" name="products"><ProductLibrary :project="project" @saved="load" /></el-tab-pane>
          <el-tab-pane label="项目团队" name="team"><TeamMembers :project="project" @saved="load" /></el-tab-pane>
        </el-tabs>
      </Panel>
      <ProjectForm v-model="projectForm" :initial="project" @saved="load" />
      <TaskForm v-model="taskForm" :project="project" :initial="editingTask" :mode="taskMode" @saved="taskSaved" />
      <TaskDrawer v-model="drawer" :task-id="selectedTask" :can-manage="project.can_manage && project.status === 'active'" @saved="load" @edit="editTask" />
    </template>
    <el-dialog v-model="summaryOpen" title="当前进展汇总" width="min(740px, 94vw)"><pre class="brief">{{ summaryText }}</pre><template #footer><el-button @click="copySummary">复制汇总</el-button><el-button type="primary" @click="ask">请小聚进一步分析</el-button></template></el-dialog>
  </div>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import ProjectForm from './operations/ProjectForm.vue'
import TaskForm from './operations/TaskForm.vue'
import TaskDrawer from './operations/TaskDrawer.vue'
import SchoolData from './operations/SchoolData.vue'
import ProductLibrary from './operations/ProductLibrary.vue'
import TeamMembers from './operations/TeamMembers.vue'
import MonthlyPlan from './operations/MonthlyPlan.vue'
import { APPROVAL_STATUS, PROJECT_STATUS, TASK_STATUS, tone } from './operations/shared'
const route = useRoute(), router = useRouter(), auth = useAuth(), assistant = useAssistant()
const project = ref(null), loading = ref(false), tab = ref('monthly'), query = ref(''), status = ref(''), mine = ref(false), keyOnly = ref(false), attention = ref(false)
const taskMode=ref('company'),refreshKey=ref(0)
const projectForm = ref(false), taskForm = ref(false), editingTask = ref(null), drawer = ref(false), selectedTask = ref(null)
const summaryOpen = ref(false), summaryText = ref('')
const filteredTasks = computed(() => (project.value?.tasks || []).filter(t => (!mine.value || t.owner_id === auth.profile?.id) &&
  (!keyOnly.value || t.priority === 'key') && (!status.value || t.status === status.value) &&
  (!attention.value || t.overdue || t.needs_update || t.status === 'blocked') && `${t.title} ${t.owner_name} ${t.school_name}`.includes(query.value)))
let timer, version = 0
async function load() {
  const current = ++version, id = route.params.id
  loading.value = !project.value
  try { const data = await api.get(`/operations/${id}`); if (current === version) {project.value = data;refreshKey.value++} } catch {} finally { if (current === version) loading.value = false }
}
function newTask() { taskMode.value='company';editingTask.value = null; taskForm.value = true }
function newSelfTask() {taskMode.value='self';editingTask.value=null;taskForm.value=true}
function editTask(task) { editingTask.value = task; drawer.value = false; taskForm.value = true }
function openTask(t) { router.replace({ query: { ...route.query, task: t.id } }); selectedTask.value = t.id; drawer.value = true }
function taskSaved(t) { load(); openTask(t) }
async function summary() { try { summaryText.value = (await api.get(`/operations/${project.value.id}/brief`)).text; summaryOpen.value = true } catch {} }
async function copySummary() { try { await navigator.clipboard.writeText(summaryText.value); ElMessage.success('汇总已复制') } catch { ElMessage.info('请选中汇总文字复制') } }
function ask() { assistant.open(`请查询运营项目「${project.value.name}」（运营项目 ID：${project.value.id}）的实际任务和反馈，总结当前进展、逾期受阻事项，以及接下来需要推进什么；请标明尚未反馈或缺少信息的部分。`) }
watch(drawer, v => { if (!v && route.query.task) { const { task, ...query } = route.query; router.replace({ query }) } })
watch(() => route.query.task, v => { if (v && Number(v) > 0) { selectedTask.value = Number(v); drawer.value = true } })
watch(() => route.params.id, async () => { project.value = null; drawer.value = false; await load() })
useBusinessRefresh(load)
onMounted(async () => { await load(); if (route.query.task) { selectedTask.value = Number(route.query.task); drawer.value = true }; timer = setInterval(() => { if (!document.hidden && !projectForm.value && !taskForm.value) load() }, 30000) })
onBeforeUnmount(() => { version++; clearInterval(timer) })
</script>
<style scoped>
.filters { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }.task-next { color: var(--jc-ink-2); font-size: 12px; margin: 6px 0 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }.mt-8 { margin-top: 8px; }.brief { white-space: pre-wrap; font: inherit; line-height: 1.9; }
@media(max-width: 560px) { .operation-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
