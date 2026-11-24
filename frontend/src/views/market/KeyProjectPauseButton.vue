<template>
  <el-button v-if="project.can_pause" :link="compact" :size="compact ? 'small' : undefined"
    :loading="working" @click.stop="toggle" @keydown.enter.stop @keydown.space.stop>
    {{ project.paused ? '恢复跟进' : '暂停跟进' }}
  </el-button>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'

const props = defineProps({ project: { type: Object, required: true }, compact: Boolean })
const emit = defineEmits(['changed'])
const working = ref(false)

async function toggle() {
  if (working.value) return
  const { project_code, name, paused } = props.project
  working.value = true
  try {
    if (!paused) {
      await ElMessageBox.confirm(`确定暂停跟进「${name}」吗？暂停后将停止该项目的任务提醒。`, '暂停跟进', {
        confirmButtonText: '确认暂停',
        cancelButtonText: '取消',
        type: 'warning',
      })
    }
    await api.put(`/market/projects/${project_code}/pause`, { paused: !paused })
    ElMessage.success(paused ? '已恢复跟进' : '已暂停跟进')
    emit('changed')
    announceBusinessChange()
  } catch {
    // Cancellation leaves the project unchanged. The API client shows request errors.
  } finally {
    working.value = false
  }
}
</script>
