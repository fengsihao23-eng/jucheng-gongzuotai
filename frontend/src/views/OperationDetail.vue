<template>
  <div class="jc-page" v-loading="loading">
    <el-button link @click="router.push('/operations')">← 运营项目</el-button>
    <template v-if="project">
      <PageHeader v-record-delete="projectRemoval" :eyebrow="`${project.category} · ${PROJECT_STATUS[project.status]}`"
        :title="project.name" :desc="project.objective || '明确阶段目标，推进关键任务。'">
        <el-button @click="ask">问小聚</el-button>
        <el-button v-if="project.can_configure" @click="projectForm = true">编辑项目</el-button>
        <el-button v-if="project.can_manage && canSection('tasks') && project.status === 'active'" type="primary" @click="newTask">布置任务</el-button>
        <el-button v-if="projectRemoval" plain type="danger" @click="deleteRecord(projectRemoval)">删除项目</el-button>
      </PageHeader>
      <Panel class="mt-16">
        <el-tabs v-if="visibleTabs.length" v-model="tab">
          <el-tab-pane v-if="canSection('tasks')" label="任务情况" name="tasks">
            <ProjectTaskBoard :key="project.id" ref="taskBoard" v-model:task-id="taskId" :project="project" @refresh="load" />
          </el-tab-pane>
          <el-tab-pane v-if="canSection('progress')" label="项目进度" name="progress" lazy>
            <ProjectProgress :project="project" @saved="load" @open-task="openTask" />
          </el-tab-pane>
          <el-tab-pane v-if="canSection('procurement')" label="学校采购" name="procurement" lazy><SchoolData mode="procurement" :project="project" @saved="load" /></el-tab-pane>
          <el-tab-pane v-if="canSection('income')" :label="legacyCampusIncome ? '收入情况' : '每日收入'" name="income" lazy><CampusIncomeReport v-if="legacyCampusIncome" /><SchoolData v-else mode="income" :project="project" @saved="load" /></el-tab-pane>
          <el-tab-pane v-if="canSection('outreach')" label="市场开拓" name="outreach" lazy><SchoolData mode="outreach" :project="project" @saved="load" @open-task="openTask" @assign-school="newOutreach" /></el-tab-pane>
          <el-tab-pane v-if="canSection('products')" label="设备产品库" name="products" lazy><ProductLibrary :project="project" @saved="load" /></el-tab-pane>
          <el-tab-pane v-if="canSection('team')" label="项目团队" name="team" lazy><TeamMembers :project="project" @saved="load" @settings="projectForm = true" /></el-tab-pane>
          <el-tab-pane v-if="canSection('competitors')" label="对手信息" name="competitors" lazy>
            <CompetitorInfo :key="project.id" :project-id="project.id" :active="tab === 'competitors'" />
          </el-tab-pane>
          <el-tab-pane v-for="section in maintenanceSections.filter(s => canSection(s.key))" :key="section.key" :label="section.label" :name="section.key" lazy>
            <OperationsMaintenance :project="project" :kind="section.key" :label="section.label" :active="tab === section.key" />
          </el-tab-pane>
          <el-tab-pane v-if="canSection('warehouse')" label="仓库物料" name="warehouse" lazy><OperationsWarehouse :project="project" :active="tab === 'warehouse'" /></el-tab-pane>
          <el-tab-pane v-if="canSection('scrap')" label="设备报废" name="scrap" lazy><OperationsWarehouse :project="project" :active="tab === 'scrap'" scrap-only /></el-tab-pane>
          <el-tab-pane v-if="canSection('reports')" label="周报 / 月报" name="digest" lazy><OpsDigest :project-id="project.id" :active="tab === 'digest'" /></el-tab-pane>
          <el-tab-pane v-if="canSection('funds')" label="经费与审批" name="funds" lazy><ProjectFunds :project-id="project.id" :active="tab === 'funds'" /></el-tab-pane>
          <el-tab-pane v-if="canSection('user_list')" label="用户清单" name="user_list" lazy>
            <ProjectUserList :key="project.id" :project-id="project.id" :active="tab === 'user_list'" />
          </el-tab-pane>
          <el-tab-pane v-if="canSection('settings')" label="工作设置" name="settings" lazy><ProjectControls :project-id="project.id" :active="tab === 'settings'" :sections="visibleSections" /></el-tab-pane>
        </el-tabs>
        <el-empty v-else description="当前没有可用的项目功能" :image-size="80" />
      </Panel>
      <ProjectForm v-model="projectForm" :initial="project" @saved="load" />
    </template>
    <el-empty v-else-if="!loading" description="无法打开运营项目，请检查访问权限或重试。"><el-button @click="load()">重试</el-button></el-empty>
  </div>
</template>
<script setup>
import { deleteRecord } from '@/composables/useRecordDeletion'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useAssistant } from '@/store/assistant'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import ProjectForm from './operations/ProjectForm.vue'
import ProjectProgress from './operations/ProjectProgress.vue'
import ProjectTaskBoard from './operations/ProjectTaskBoard.vue'
import CompetitorInfo from './operations/CompetitorInfo.vue'
import OpsDigest from './operations/OpsDigest.vue'
import SchoolData from './operations/SchoolData.vue'
import CampusIncomeReport from './operations/CampusIncomeReport.vue'
import ProductLibrary from './operations/ProductLibrary.vue'
import TeamMembers from './operations/TeamMembers.vue'
import ProjectControls from './operations/ProjectControls.vue'
import ProjectFunds from './operations/ProjectFunds.vue'
import ProjectUserList from './operations/ProjectUserList.vue'
import OperationsWarehouse from './operations/OperationsWarehouse.vue'
import OperationsMaintenance from './operations/OperationsMaintenance.vue'
import { PROJECT_STATUS, projectProgressPrompt } from './operations/shared'
const route = useRoute(), router = useRouter(), auth = useAuth(), assistant = useAssistant()
const project = ref(null), loading = ref(false), tab = ref('tasks'), projectForm = ref(false), taskBoard = ref(null)
const taskId = computed({
  get: () => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined,
  set: id => router.replace({ query: { ...route.query, task: id } }),
})
const legacyCampusIncome = computed(() => project.value?.feature_settings?.legacy_campus_income ?? project.value?.name === '智慧校园')
const visibleSections = computed(() => (project.value?.visible_sections || []).filter(section =>
  section !== 'funds' || (auth.hasMenu('expenses') && auth.hasMenu('projects'))))
const visibleTabs = computed(() => visibleSections.value.map(section => section === 'reports' ? 'digest' : section))
const canSection = section => visibleSections.value.includes(section)
const maintenanceSections = computed(() => [
  { key: 'device_maintenance', label: '设备运维' },
  { key: 'arrears', label: project.value?.name.trim() === '一键报警' ? '欠款维护' : '欠费维护' },
  { key: 'collection_maintenance', label: '回款维护' },
  { key: 'customers', label: '客户资料维护' },
])
watch(() => route.query.digest, value => { if (value && canSection('reports')) tab.value = 'digest' })
watch(visibleTabs, tabs => {
  if (!tabs.includes(tab.value) && project.value) tab.value = tabs[0] || ''
})
let version = 0
async function load(background = false) {
  const current = ++version, id = route.params.id
  loading.value = !project.value
  try { const data = await api.get(`/operations/${id}`, null, { silent: background }); if (current === version) project.value = data }
  catch (err) { if (current === version && [40301, 40401].includes(err.code)) project.value = null }
  finally { if (current === version) loading.value = false }
}
function newTask() { taskBoard.value?.newTask() }
function newOutreach(school) { taskBoard.value?.newTask(school.id) }
function openTask(task) { taskBoard.value?.openTask(task) }
const projectRemoval = computed(() => project.value?.can_delete && {
  endpoint: '/operations/' + project.value.id, title: '删除运营项目', name: project.value.name,
  onDeleted: () => router.push('/operations'),
})
function ask() { assistant.open(projectProgressPrompt(project.value)) }
watch(() => route.params.id, id => {
  if (!id) return
  project.value = null
  tab.value = route.query.digest ? 'digest' : 'tasks'
  load()
}, { immediate: true })
useBusinessRefresh(() => load(true), { interval: 12000, enabled: () => Boolean(route.params.id) })
onBeforeUnmount(() => { version++ })
</script>
