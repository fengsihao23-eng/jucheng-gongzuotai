<template>
  <RouterView v-if="background" :route="background" v-slot="{ Component }">
    <RecordRouteContext :location="background">
      <component :is="Component" :key="background.path" />
    </RecordRouteContext>
  </RouterView>
  <template v-if="route.meta.recordDetail">
    <RecordDetailDialog v-if="route.name === 'linked-record'" :model-value="true" :initial="linked" linked
      title="业务跟进" @update:model-value="close">
      <el-alert v-if="linkedError" :title="linkedError" type="warning" :closable="false" show-icon />
      <el-skeleton v-else-if="!linked" :rows="4" animated />
    </RecordDetailDialog>
    <RouterView v-else v-slot="{ Component }">
      <component v-if="route.name === 'collection-followup'" :is="Component" @update:model-value="close" />
      <RecordDetailDialog v-else :key="route.path" :model-value="true" :dialog-title="route.meta.title" :target="target"
        :progress-hint="hints[route.meta.recordDetail]" @update:model-value="close">
        <template #workspace><component :is="Component" /></template>
      </RecordDetailDialog>
    </RouterView>
  </template>
</template>

<script setup>
import { computed, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import RecordDetailDialog from './RecordDetailDialog.vue'
import RecordRouteContext from './RecordRouteContext.vue'

const router = useRouter(), route = useRoute()
const background = shallowRef(null), linked = shallowRef(null), linkedError = shallowRef('')
const fallbacks = { 'key-project-detail': '/market', 'project-detail': '/projects', 'operation-detail': '/operations',
  'contract-detail': '/contracts', 'performance-contract-detail': '/contracts', 'collection-contract-detail': '/collections',
  'collection-project-report': '/collections', 'collection-followup': '/notifications', 'linked-record': '/notifications' }
const hints = {
  project: '说明项目目前推进到哪一步、需要协调什么。',
  'key-project': '说明关键事项、节点进展或需要协调的问题。',
  operation: '说明项目进展、实际成果和待协调事项。',
  contract: '说明签署、履约或交付目前推进到哪一步。',
  'workbook-contracts': '说明签署、履约或交付目前推进到哪一步。',
  'workbook-collections': '说明催收、付款审批或预计到账的情况。',
}
watch(router.currentRoute, current => {
  if (!current.meta.recordDetail) background.value = current
}, { immediate: true, flush: 'sync' })
const target = computed(() => ({ kind: route.meta.recordDetail, id: route.params.row ?? route.params.id ?? route.params.code,
  sheet: route.params.sheet, revision: typeof route.query.revision === 'string' ? route.query.revision : undefined }))
let request = 0
watch(() => [route.name, route.params.id], async ([name, id]) => {
  const current = ++request
  linked.value = null
  linkedError.value = ''
  if (name !== 'linked-record') return
  try {
    const result = await api.get(`/record-followups/entries/${encodeURIComponent(id)}`, null, { silent: true })
    if (current === request) linked.value = result
  } catch (e) { if (current === request) linkedError.value = e.message }
}, { immediate: true })
function close(value) {
  if (value) return
  const fallback = fallbacks[route.name] || '/'
  let home = background.value?.fullPath || fallback
  if (!background.value && typeof route.query.back === 'string') {
    try {
      const link = new URL(route.query.back, location.origin)
      if (link.origin === location.origin && link.pathname === fallback) home = link.pathname + link.search
    } catch { /* Use the module's own entry. */ }
  }
  if (background.value && window.history.state?.back === home) router.back()
  else router.replace(home)
}
</script>
