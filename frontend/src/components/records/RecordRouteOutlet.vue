<template>
  <RouterView v-if="background" :route="background" v-slot="{ Component }">
    <RecordRouteContext :location="background">
      <component :is="Component" :key="background.path" />
    </RecordRouteContext>
  </RouterView>
  <template v-if="route.meta.feedbackOverlay">
    <RecordDetailDialog v-if="route.name === 'linked-record'" :model-value="true" :initial="linked" linked
      title="业务跟进" @update:model-value="close">
      <el-alert v-if="linkedError" :title="linkedError" type="warning" :closable="false" show-icon />
      <el-skeleton v-else-if="!linked" :rows="4" animated />
    </RecordDetailDialog>
    <RouterView v-else v-slot="{ Component }">
      <component :is="Component" @update:model-value="close" />
    </RouterView>
  </template>
</template>

<script setup>
import { shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import RecordDetailDialog from './RecordDetailDialog.vue'
import RecordRouteContext from './RecordRouteContext.vue'

const router = useRouter(), route = useRoute()
const background = shallowRef(null), linked = shallowRef(null), linkedError = shallowRef('')
watch(router.currentRoute, current => {
  if (!current.meta.feedbackOverlay) background.value = current
}, { immediate: true, flush: 'sync' })
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
  const home = background.value?.fullPath || '/notifications'
  if (background.value && window.history.state?.back === home) router.back()
  else router.replace(home)
}
</script>
