<template>
  <Panel title="删除密码" sub="各模块删除记录时共用此密码。" :loading="loading">
    <p v-if="settings?.updated_at" class="jc-small jc-muted">最近更新 {{ datetime(settings.updated_at) }}</p>
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="当前密码">
        <el-input :model-value="shown ? current : '••••••••'" readonly autocomplete="off" aria-label="当前删除密码" />
        <el-button :loading="revealing" @click="toggle">{{ shown ? '隐藏密码' : '查看密码' }}</el-button>
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="nextPassword" type="password" show-password autocomplete="new-password"
          maxlength="128" aria-label="新删除密码" />
      </el-form-item>
      <el-button type="primary" native-type="submit" :loading="saving" :disabled="!settings || !nextPassword.trim()">
        保存新密码
      </el-button>
    </el-form>
  </Panel>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const props = defineProps({ active: Boolean })
const settings = ref(null), loading = ref(false), revealing = ref(false), saving = ref(false)
const shown = ref(false), current = ref(''), nextPassword = ref('')
const endpoint = '/admin/deletion-password'
let version = 0

function hide() {
  shown.value = false
  current.value = ''
}

async function load() {
  const request = ++version
  loading.value = true
  try {
    const data = await api.get(endpoint)
    if (request === version) settings.value = data
  } catch {} finally {
    if (request === version) loading.value = false
  }
}

async function toggle() {
  if (shown.value) return hide()
  const request = version
  revealing.value = true
  try {
    const { password, ...info } = await api.get(endpoint, { reveal: true })
    if (request !== version) return
    settings.value = info
    current.value = password
    shown.value = true
  } catch {} finally { revealing.value = false }
}

async function save() {
  if (!settings.value || saving.value || !nextPassword.value.trim()) return
  const request = version
  saving.value = true
  try {
    const data = await api.put(endpoint, { password: nextPassword.value, expected_revision: settings.value.revision })
    if (request !== version) return
    settings.value = data
    nextPassword.value = ''
    hide()
    ElMessage.success('删除密码已更新')
  } catch (err) {
    if (err.code === 40901 && request === version) load()
  } finally { saving.value = false }
}

watch(() => props.active, active => {
  if (active) load()
  else {
    version++
    hide()
    nextPassword.value = ''
  }
}, { immediate: true })
</script>

<style scoped>
.el-form-item { max-width: 430px; }
.el-form-item :deep(.el-form-item__content) { display: flex; flex-wrap: wrap; gap: 8px; }
.el-form-item :deep(.el-input) { flex: 1 1 220px; }
</style>
