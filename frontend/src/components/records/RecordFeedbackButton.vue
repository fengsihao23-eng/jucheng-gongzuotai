<template>
  <el-button plain type="primary" aria-haspopup="dialog" @click="open = true">互动反馈</el-button>
  <RecordDetailDialog v-model="open" dialog-title="互动反馈" :target="target"
    :heading="heading" :progress-hint="progressHint" />
</template>

<script setup>
import { ref } from 'vue'
import RecordDetailDialog from './RecordDetailDialog.vue'

defineProps({ target: { type: Object, required: true }, heading: String, progressHint: String })
const open = ref(false)
</script>
