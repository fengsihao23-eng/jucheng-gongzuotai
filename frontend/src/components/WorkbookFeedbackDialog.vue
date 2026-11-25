<template>
  <RecordDetailDialog v-if="selection" v-model="open" dialog-title="互动反馈"
    :target="selection.target" :heading="selection.heading" :progress-hint="selection.hint"
    :archive-on-open="selection.archive" />
</template>

<script setup>
import { ref, shallowRef } from 'vue'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'

const open = ref(false), selection = shallowRef(null)

function show(target, heading, hint, archive = false) {
  selection.value = { target, heading, hint, archive }
  open.value = true
}

defineExpose({ show })
</script>
