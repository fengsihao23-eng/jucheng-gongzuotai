<template>
  <Panel title="任务情况" :loading="(loadingProjects && !data.today) || loadingProject">
    <template v-if="auth.hasMenu('operations')" #extra>
      <el-select v-if="data.items.length" v-model="projectId" aria-label="选择运营项目" placeholder="选择运营项目" filterable class="project-select">
        <el-option v-for="item in data.items" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
      <el-button v-if="canAssign" type="primary" @click="taskBoard?.newTask()">布置任务</el-button>
      <el-button v-if="project" @click="openProject(project.id)">项目详情</el-button>
      <el-button v-if="data.can_create" @click="createOpen = true">新建运营项目</el-button>
    </template>

    <ProjectTaskBoard v-if="canViewTasks" :key="project.id" ref="taskBoard" :project="project" @refresh="loadProject" />
    <el-empty v-else-if="!auth.hasMenu('operations')" description="当前账号暂无运营项目访问权限" :image-size="80" />
    <el-empty v-else-if="project" description="当前账号暂无此项目的任务查看权限" :image-size="80" />
    <el-empty v-else-if="!loadingProjects && !loadingProject && (data.today || loadError)" :description="loadError || '暂无可查看的运营项目'" :image-size="80">
      <el-button v-if="loadError && projectId" @click="loadProject()">重试</el-button>
    </el-empty>
  </Panel>
  <ProjectForm v-if="data.can_create" v-model="createOpen" @saved="item => openProject(item.id)" />
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api/client'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { useAuth } from '@/store/auth'
import ProjectForm from '../operations/ProjectForm.vue'
import ProjectTaskBoard from '../operations/ProjectTaskBoard.vue'
import { useProjectOverview } from '../operations/useProjectOverview'

const auth = useAuth(), router = useRouter()
const canReadOperations = () => auth.hasMenu('operations')
const { data, loading: loadingProjects } = useProjectOverview(canReadOperations)
const projectId = ref(), project = ref(null), loadingProject = ref(false), loadError = ref('')
const taskBoard = ref(null), createOpen = ref(false)
const canViewTasks = computed(() => project.value?.visible_sections.includes('tasks'))
const canAssign = computed(() => canViewTasks.value && project.value.can_manage && project.value.status === 'active')
let version = 0

watch(() => data.items, items => {
  if (!items.some(item => item.id === projectId.value)) projectId.value = items[0]?.id
})

async function loadProject(background = false) {
  const id = projectId.value
  const current = ++version
  if (!id) { loadingProject.value = false; return }
  loadingProject.value = !background && !project.value
  try {
    const result = await api.get(`/operations/${id}`, null, { silent: true })
    if (current !== version || id !== projectId.value) return
    project.value = result
    loadError.value = ''
  } catch (err) {
    if (current === version && (!project.value || [40301, 40401].includes(err.code))) {
      project.value = null
      loadError.value = err.message
    }
  } finally { if (current === version) loadingProject.value = false }
}

watch(projectId, () => {
  project.value = null
  loadError.value = ''
  loadProject()
})
useBusinessRefresh(() => loadProject(true), { interval: 12000, enabled: () => canReadOperations() && Boolean(projectId.value) })
onBeforeUnmount(() => { version++ })

function openProject(id) { router.push({ name: 'operation-detail', params: { id } }) }
</script>

<style scoped>
.project-select { width: min(190px, 100%); }
</style>
