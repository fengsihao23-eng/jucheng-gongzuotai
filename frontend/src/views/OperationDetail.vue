<template>
  <div class="jc-page" v-loading="loading">
    <el-button link @click="router.push('/operations')">← 运营项目</el-button>
    <template v-if="project">
      <PageHeader :eyebrow="`${project.category} · ${PROJECT_STATUS[project.status]}`" :title="project.name" :desc="project.objective || '明确阶段目标，推进关键任务。'">
        <el-button @click="ask">问小聚</el-button>
        <el-button v-if="project.can_manage && project.status === 'active'" type="primary" @click="newTask">布置任务</el-button>
      </PageHeader>
      <Panel class="mt-16">
        <el-tabs v-model="tab">
          <el-tab-pane label="任务情况" name="tasks">
            <div class="filters">
              <el-input v-model="query" placeholder="搜索任务 / 学校 / 负责人" clearable style="width: 240px" />
              <el-checkbox v-model="mine">我负责的</el-checkbox><el-checkbox v-model="keyOnly">仅重点</el-checkbox><el-checkbox v-model="attention">需要关注</el-checkbox>
              <span class="jc-spacer" />
              <el-radio-group v-model="taskView" size="small" aria-label="任务显示方式"><el-radio-button value="cards">任务卡</el-radio-button><el-radio-button value="table">进度表</el-radio-button></el-radio-group>
              <el-button v-if="delayRows.length" size="small" @click="delayOpen = true">待处理延期（{{ delayRows.length }}）</el-button>
              <el-button v-if="project.can_propose && project.status === 'active'" link type="primary" @click="newSelfTask">自行安排</el-button>
              <el-button link @click="summary">进展汇总</el-button>
            </div>
            <div class="board-status" role="group" aria-label="按任务状态筛选">
              <button v-for="(label, value) in statuses" :key="value" type="button" :aria-pressed="status === value" :data-status="value" :title="value === 'ongoing' ? '包含待开始、受阻、待审核和已退回的任务' : undefined" @click="status = value"><i /><span>{{ label }}</span><b class="jc-num">{{ taskGroups[value].length }}</b></button>
            </div>
            <TaskCards v-if="filteredTasks.length && taskView === 'cards'" :rows="filteredTasks" @open-task="openTask" @menu="openMenu" />
            <el-table v-else-if="filteredTasks.length" :data="filteredTasks" @row-click="openTask" :row-class-name="({ row }) => row.overdue ? 'is-alert' : ''">
              <el-table-column prop="title" label="事项" min-width="190" /><el-table-column prop="owner_name" label="负责人" width="100" /><el-table-column prop="due_date" label="截止" width="115" />
              <el-table-column prop="latest_feedback" label="最近进展" min-width="230" show-overflow-tooltip /><el-table-column prop="next_step" label="下一步 / 协调" min-width="180" show-overflow-tooltip />
              <el-table-column label="状态" width="110"><template #default="{ row }">{{ row.status === 'done' ? '已完成' : row.overdue ? '逾期未完成' : row.status === 'blocked' ? '受阻' : '进行中' }}</template></el-table-column>
              <el-table-column width="100"><template #default="{ row }"><el-button link @click.stop="openTask(row)">查看 / 反馈</el-button></template></el-table-column>
            </el-table>
            <ul v-if="menu.open" class="ctx-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" @click.stop>
              <li class="jc-down" @click="removeTask(menu.task)">作废任务</li>
            </ul>
            <div v-if="menu.open" class="ctx-mask" @click="menu.open = false" @contextmenu.prevent="menu.open = false" />
            <el-empty v-if="!filteredTasks.length" :description="project.tasks.length ? '没有符合筛选条件的任务' : '还没有安排任务'" :image-size="66"><el-button v-if="!project.tasks.length && project.can_manage && project.status === 'active'" type="primary" @click="newTask">布置第一项任务</el-button></el-empty>
          </el-tab-pane>
          <template v-if="project.can_view_project_content">
            <template v-if="hasSchools">
              <el-tab-pane v-if="canSection('procurement')" label="学校采购" name="procurement" lazy><SchoolData mode="procurement" :project="project" @saved="load" /></el-tab-pane>
              <el-tab-pane v-if="canSection('income')" label="每日收入" name="income" lazy><SchoolData mode="income" :project="project" @saved="load" /></el-tab-pane>
              <el-tab-pane v-if="canSection('outreach')" label="市场开拓" name="outreach" lazy><SchoolData mode="outreach" :project="project" @saved="load" @open-task="openTask" @assign-school="newOutreach" /></el-tab-pane>
              <el-tab-pane v-if="canSection('outreach')" label="学校档案" name="schools" lazy><SchoolData :project="project" @saved="load" @open-task="openTask" /></el-tab-pane>
            </template>
            <el-tab-pane v-if="canSection('products')" label="设备产品库" name="products" lazy><ProductLibrary :project="project" @saved="load" /></el-tab-pane>
            <el-tab-pane v-if="canSection('team')" label="项目团队" name="team" lazy><TeamMembers :project="project" @saved="load" @settings="projectForm = true" /></el-tab-pane>
          </template>
          <el-tab-pane v-if="canSection('reports')" label="周报 / 月报" name="digest" lazy><OpsDigest :project-id="project.id" :active="tab === 'digest'" /></el-tab-pane>
          <el-tab-pane v-if="canSection('funds') && auth.hasMenu('expenses') && auth.hasMenu('projects')" label="经费与审批" name="funds" lazy><ProjectFunds :project-id="project.id" :active="tab === 'funds'" /></el-tab-pane>
          <el-tab-pane v-if="project.can_manage" label="工作设置" name="controls" lazy><ProjectControls :project-id="project.id" :active="tab === 'controls'" /></el-tab-pane>
          <el-tab-pane v-if="project.can_archive_tasks" label="已作废任务" name="archive" lazy>
            <el-table :data="archivedTasks">
              <el-table-column label="任务" prop="title" min-width="175" />
              <el-table-column label="负责人" prop="owner_name" min-width="100" />
              <el-table-column label="作废说明" prop="archive_reason" min-width="175" />
              <el-table-column label="操作" width="165"><template #default="{ row }">
                <el-button link type="primary" @click="archivedTask = row.id; archiveOpen = true">查看记录</el-button>
                <el-button link type="primary" @click="restore(row)">恢复</el-button>
              </template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </Panel>
      <ProjectForm v-model="projectForm" :initial="project" @saved="load" />
      <TaskForm v-model="taskForm" :project="project" :initial="editingTask" :mode="taskMode" :school-preset="schoolPreset" @saved="t => pendingTask = t" @closed="taskFormClosed" />
      <TaskDialog v-model="archiveOpen" :task-id="archivedTask" :project-id="project.id" archived />
      <TaskDialog v-model="taskOpen" :task-id="selectedTask" :project-id="project.id" @edit="editTask" show-archive @archive="removeTask" />
      <el-dialog v-model="delayOpen" title="待处理延期" width="min(660px, calc(100vw - 24px))" align-center append-to-body @closed="delayClosed">
        <ul v-if="delayRows.length" class="delay-list">
          <li v-for="row in delayRows" :key="row.id">
            <div><strong>{{ row.title }}</strong><p>{{ TASK_SOURCES[row.source] }} · {{ row.owner_name }} · 原定 {{ row.deadline_basis }}</p></div>
            <el-button link type="primary" @click="selectDelay(row)">{{ row.can_review_delay ? '复核延期' : '补充延期原因' }}</el-button>
          </li>
        </ul>
        <p v-else class="jc-muted">暂无需要你处理的延期事项。</p>
      </el-dialog>
      <el-dialog v-model="summaryOpen" class="ops-summary-dialog" title="当前进展汇总" width="min(960px, calc(100vw - 24px))" align-center append-to-body>
        <ProjectProgress v-if="canSection('progress')" :project="project" read-only />
        <TaskOverview :rows="project.tasks" title="项目任务概览" />
        <div v-if="project.tasks.length" class="recent">
          <h3>{{ followUps.length ? '优先跟进' : '最新进展' }}</h3>
          <TaskCards :rows="(followUps.length ? followUps : project.tasks).slice(0, 6)" @open-task="t => { summaryOpen = false; openTask(t) }" />
        </div>
        <template #footer><el-button @click="copySummary">复制汇总</el-button><el-button type="primary" @click="ask">请小聚进一步分析</el-button></template>
      </el-dialog>
    </template>
    <el-empty v-else-if="!loading" description="无法打开运营项目，请检查访问权限或重试。"><el-button @click="load()">重试</el-button></el-empty>
  </div>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import ProjectForm from './operations/ProjectForm.vue'
import ProjectProgress from './operations/ProjectProgress.vue'
import TaskForm from './operations/TaskForm.vue'
import TaskDialog from './operations/TaskDialog.vue'
import TaskCards from './operations/TaskCards.vue'
import TaskOverview from './operations/TaskOverview.vue'
import OpsDigest from './operations/OpsDigest.vue'
import SchoolData from './operations/SchoolData.vue'
import ProductLibrary from './operations/ProductLibrary.vue'
import TeamMembers from './operations/TeamMembers.vue'
import ProjectControls from './operations/ProjectControls.vue'
import ProjectFunds from './operations/ProjectFunds.vue'
import { PROJECT_STATUS, TASK_SOURCES, taskStatus } from './operations/shared'
const route = useRoute(), router = useRouter(), auth = useAuth(), assistant = useAssistant()
const project = ref(null), loading = ref(false), tab = ref('tasks'), query = ref(''), status = ref('ongoing'), mine = ref(false), keyOnly = ref(false), attention = ref(false)
const taskMode = ref('company'), projectForm = ref(false), taskForm = ref(false), editingTask = ref(null), pendingTask = ref(null), summaryOpen = ref(false)
const popupPaused = ref(false)
const schoolPreset = ref(null), archivedTasks = ref([]), archivedTask = ref(null), archiveOpen = ref(false)
const hasSchools = computed(() => project.value?.category.includes('校园') || project.value?.schools.length)
const canSection = section => (project.value?.visible_sections || []).includes(section)
const taskView = ref('cards')
watch(() => route.query.digest, value => { if (value) tab.value = 'digest' }, { immediate: true })
watch(tab, value => { if (value === 'archive') loadArchive() })
async function loadArchive() {
  if (!project.value?.can_archive_tasks) return
  const id = project.value.id
  try {
    const result = await api.get('/operations/tasks', { project_id: id, archived: true })
    if (project.value?.id === id) archivedTasks.value = result.items
  } catch {}
}
async function restore(row) {
  try {
    await ElMessageBox.confirm('恢复「' + row.title + '」为可见任务？', '恢复任务')
    await api.post('/operations/tasks/' + row.id + '/restore')
    ElMessage.success('任务已恢复'); loadArchive(); load()
  } catch {}
}
const delayOpen = ref(false), pendingDelay = ref(null)
const menu = ref({ open: false, x: 0, y: 0, task: null })
const statuses = { ongoing: '进行中', done: '已完成' }
const selectedTask = computed(() => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined)
const taskOpen = computed({ get: () => Boolean(selectedTask.value) && !popupPaused.value, set: value => { if (!value && !popupPaused.value) closeTask() } })
const needsAttention = t => t.overdue || t.needs_update || t.deadline_status === 'overdue' || t.delay_status === 'pending' || ['blocked', 'review', 'rejected'].includes(taskStatus(t))
const matches = computed(() => (project.value?.tasks || []).filter(t => (!mine.value || t.owner_id === auth.profile?.id) &&
  (!keyOnly.value || t.priority === 'key') && (!attention.value || needsAttention(t)) &&
  `${t.title} ${t.owner_name} ${t.school_name}`.toLowerCase().includes(query.value.trim().toLowerCase())))
const taskGroups = computed(() => {
  const groups = { ongoing: [], done: [] }
  for (const task of matches.value) groups[taskStatus(task) === 'done' ? 'done' : 'ongoing'].push(task)
  return groups
})
const filteredTasks = computed(() => taskGroups.value[status.value])
const followUps = computed(() => (project.value?.tasks || []).filter(needsAttention))
const projectPlans = computed(() => (project.value?.tasks || []).filter(t => t.source === 'project_plan'))
const companyTasks = computed(() => (project.value?.tasks || []).filter(t => t.source === 'company'))
const delayRows = computed(() => (project.value?.tasks || []).filter(t => t.can_review_delay ||
  (t.can_explain_delay && ['needs_reason', 'rejected'].includes(t.delay_status))))
let version = 0
async function load(background = false) {
  const current = ++version, id = route.params.id
  loading.value = !project.value
  try { const data = await api.get(`/operations/${id}`, null, { silent: background }); if (current === version) project.value = data }
  catch (err) { if (current === version && [40301, 40401].includes(err.code)) project.value = null }
  finally { if (current === version) loading.value = false }
}
function newTask() { schoolPreset.value = null; taskMode.value='company';editingTask.value = null; taskForm.value = true }
function newOutreach(school) { newTask(); schoolPreset.value = school.id }
function newSelfTask() {schoolPreset.value = null; taskMode.value='self';editingTask.value=null;taskForm.value=true}
function editTask(task) { schoolPreset.value = null; editingTask.value = task; popupPaused.value = true; taskForm.value = true }
function openTask(t) { router.replace({ query: { ...route.query, task: t.id } }) }
function closeTask() { const { task, ...query } = route.query; if (task) router.replace({ query }) }
function openMenu(task, event) {
  if (!task.can_archive) return
  menu.value = { open: true, x: event.clientX, y: event.clientY, task }
}
async function removeTask(task) {
  menu.value.open = false
  if (!task) return
  try {
    const result = await ElMessageBox.prompt('作废后从任务、汇总和提醒中移除，过程记录可查、可恢复。', '作废「' + task.title + '」', {
      inputPlaceholder: '填写作废原因', inputValidator: value => Boolean(value?.trim()) || '请填写原因',
      confirmButtonText: '作废', cancelButtonText: '取消', type: 'warning',
    })
    await api.post('/operations/tasks/' + task.id + '/archive', { reason: result.value.trim() })
    if (selectedTask.value === task.id) closeTask()
    ElMessage.success('任务已作废'); load()
  } catch {}
}
function selectDelay(row) { pendingDelay.value = row; delayOpen.value = false }
function delayClosed() { if (pendingDelay.value) { openTask(pendingDelay.value); pendingDelay.value = null } }
function taskFormClosed() { popupPaused.value = false; if (pendingTask.value) { openTask(pendingTask.value); pendingTask.value = null } }
function progressSaved(progress) { if (project.value?.id === progress.project_id) project.value = { ...project.value, latest_progress: progress } }
function summary() { summaryOpen.value = true; load(true) }
async function copySummary() {
  try { const brief = await api.get(`/operations/${project.value.id}/brief`); await navigator.clipboard.writeText(brief.text); ElMessage.success('汇总已复制') }
  catch { ElMessage.info('暂时无法复制汇总，请重试') }
}
function ask() { assistant.open(`请查询运营项目「${project.value.name}」（运营项目 ID：${project.value.id}）的实际记录，分负责人计划和公司安排，说明当前进展、逾期受阻事项和下一步；如有自行安排另列。请标明尚未反馈或缺少信息的部分。`) }
watch(() => route.params.id, id => { if (id) { project.value = null; tab.value = 'tasks'; status.value = 'ongoing'; taskForm.value = false; pendingTask.value = null; popupPaused.value = false; summaryOpen.value = false; delayOpen.value = false; pendingDelay.value = null; load() } }, { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => Boolean(route.params.id) })
onBeforeUnmount(() => { version++ })
</script>
<style scoped>
.ctx-mask { position: fixed; inset: 0; z-index: 2000; }
.ctx-menu { position: fixed; z-index: 2001; min-width: 132px; margin: 0; padding: 4px; list-style: none; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 8px; box-shadow: var(--jc-shadow-pop); }
.ctx-menu li { padding: 7px 12px; font-size: 13px; border-radius: 6px; cursor: pointer; }
.ctx-menu li:hover { background: var(--jc-bg-2, #f9f8f5); }
.filters { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 14px; }
.board-status { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 15px; }
.board-status button { --tone: var(--jc-brand); display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--jc-line); border-radius: 7px; padding: 7px 10px; font: inherit; font-size: 12px; color: var(--jc-ink-2); background: var(--jc-bg-2, #f9f8f5); cursor: pointer; }
.board-status button[data-status="done"] { --tone: var(--jc-up); }
.board-status button[aria-pressed="true"], .board-status button:hover { border-color: var(--jc-brand); color: var(--jc-brand); }.board-status i { width: 7px; height: 7px; border-radius: 50%; background: var(--tone); }.board-status b { font-size: 15px; color: var(--jc-ink); }.recent { margin-top: 15px; }.recent h3 { font-size: 15px; }
:global(.ops-summary-dialog > .el-dialog__body) { max-height: 67dvh; overflow: auto; }
.delay-list { margin: 0; padding: 0; list-style: none; max-height: 62dvh; overflow: auto; }
.delay-list li { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 9px 0; overflow-wrap: anywhere; }
.delay-list li + li { border-top: 1px solid var(--jc-line-2); }.delay-list strong { color: var(--jc-ink); }.delay-list p { margin: 3px 0 0; color: var(--jc-ink-3); font-size: 12px; }.delay-list .el-button { flex-shrink: 0; }
</style>
