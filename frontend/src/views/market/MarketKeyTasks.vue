<template>
  <Panel title="任务情况" :loading="(loading && !ready) || loadingProject">
    <template v-if="available.length" #extra>
      <el-select v-model="code" class="project-select" aria-label="选择重点项目" placeholder="选择重点项目" filterable>
        <el-option v-for="item in available" :key="item.project_code" :label="item.name" :value="item.project_code" />
      </el-select>
      <el-button v-if="project?.can_manage && !project.paused && project.status !== 5" type="primary" @click="taskBoard?.editTask()">安排任务</el-button>
      <router-link v-if="project" class="el-button project-detail-link" :to="{ name: 'key-project-detail', params: { code } }">项目详情</router-link>
    </template>
    <KeyTaskBoard v-if="project" :key="project.project_code" ref="taskBoard" :project="project" @refresh="loadProject()" />
    <el-empty v-else-if="error" :description="error" :image-size="66"><el-button @click="loadProject()">重试</el-button></el-empty>
    <el-empty v-else-if="ready && !available.length" description="暂无重点项目" :image-size="66" />
  </Panel>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import api from '@/api/client'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import KeyTaskBoard from './KeyTaskBoard.vue'

const props = defineProps({ projects: { type: Array, required: true }, loading: Boolean, ready: Boolean })
const available = computed(() => props.projects.filter(project => project.is_key))
const code = ref(''), project = ref(null), loadingProject = ref(false), error = ref(''), taskBoard = ref(null)
let version = 0

watch(available, items => {
  if (items.some(item => item.project_code === code.value)) return
  code.value = (items.find(item => item.total_nodes && !item.paused) || items[0])?.project_code || ''
}, { immediate: true })

async function loadProject() {
  const current = ++version, selected = code.value
  if (!selected) { loadingProject.value = false; return }
  loadingProject.value = !project.value && !error.value
  try {
    const result = await api.get(`/market/projects/${encodeURIComponent(selected)}`, null, { silent: true })
    if (current !== version || selected !== code.value) return
    project.value = result
    error.value = ''
  } catch (err) {
    if (current === version && (!project.value || [40301, 40401].includes(err.code))) {
      project.value = null
      error.value = err.message
    }
  } finally { if (current === version) loadingProject.value = false }
}

watch(code, () => {
  project.value = null
  error.value = ''
  loadProject()
}, { immediate: true })
useBusinessRefresh(loadProject, { interval: 12000, enabled: () => Boolean(code.value) })
onBeforeUnmount(() => { version++ })
</script>

<style scoped>
.project-select { width: min(190px, 100%); }
.project-detail-link { text-decoration: none; }
</style>
