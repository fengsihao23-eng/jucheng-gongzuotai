<template>
  <el-form-item label="反馈截止时间">
    <div class="deadline-field">
      <el-date-picker v-model="localTime" type="datetime" format="YYYY-MM-DD HH:mm" value-format="YYYY-MM-DD HH:mm"
        aria-label="反馈截止时间" placeholder="选择日期和时间" clearable />
      <span class="jc-small jc-muted">北京时间，留空按反馈频率处理。</span>
    </div>
  </el-form-item>
</template>

<script setup>
import { computed } from 'vue'
import { feedbackTime } from '@/utils/taskFeedback'

const value = defineModel({ type: String, default: null })
const localTime = computed({
  get: () => feedbackTime(value.value),
  set: time => { value.value = time ? `${time.replace(' ', 'T')}:00+08:00` : null },
})
</script>

<style scoped>
.deadline-field { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; width: 100%; }
.deadline-field .el-date-editor { width: min(230px, 100%); }
</style>
