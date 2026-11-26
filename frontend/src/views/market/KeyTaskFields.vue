<template>
  <div>
    <div :class="{ two: compact }">
      <el-form-item label="任务名称" :prop="field('title')" :rules="[{ required: true, whitespace: true, message: '填写任务名称' }]">
        <el-input v-model="plan.title" :aria-label="general ? '任务名称' : '重点任务名称'" maxlength="64" />
      </el-form-item>
      <el-form-item label="任务负责人" :prop="field('owner_id')" :rules="[{ required: true, message: '选择负责人' }]">
        <el-select v-model="plan.owner_id" filterable :aria-label="general ? '任务负责人' : '重点任务负责人'">
          <el-option v-for="user in users" :key="user.id" :value="user.id" :label="user.name" />
        </el-select>
      </el-form-item>
    </div>
    <div class="two">
      <el-form-item label="准备日期" :prop="field('preparation_on')" :rules="[{ required: true, message: '选择准备日期' }]">
        <el-date-picker v-model="plan.preparation_on" type="date" value-format="YYYY-MM-DD" aria-label="任务准备日期" />
      </el-form-item>
      <el-form-item label="完成节点" :prop="field('due_on')" :rules="[{ required: true, message: '选择完成日期' }]">
        <el-date-picker v-model="plan.due_on" type="date" value-format="YYYY-MM-DD" aria-label="任务完成日期" />
      </el-form-item>
    </div>
    <details :open="!compact" class="task-options">
      <summary v-show="compact">工作要求、反馈与阶段节点</summary>
      <el-form-item label="工作内容 / 要求">
        <el-input v-model="plan.requirements" type="textarea" :rows="compact ? 2 : 3" maxlength="6000" :aria-label="general ? '任务要求' : '重点任务要求'" />
      </el-form-item>
      <el-form-item label="进展反馈频率">
        <el-radio-group v-model="plan.cadence" :aria-label="general ? '任务反馈频率' : '重点任务反馈频率'">
          <el-radio value="daily">每日</el-radio><el-radio value="weekly">每周</el-radio><el-radio value="node">按节点</el-radio>
        </el-radio-group>
      </el-form-item>
      <FeedbackDeadlineField v-model="plan.feedback_due_at" />
      <div class="node-heading">
        <b>阶段节点</b>
        <el-button link type="primary" :disabled="plan.nodes.length >= 30" @click="plan.nodes.push({ name: '', due_on: plan.due_on })">增加阶段节点</el-button>
      </div>
      <div v-for="(node, index) in plan.nodes" :key="node.id || index" class="node-edit">
        <el-input v-model="node.name" :aria-label="`阶段名称${index + 1}`" placeholder="例如：准备资料、提交方案" maxlength="128" :disabled="completed.has(node.id)" />
        <el-date-picker v-model="node.due_on" type="date" value-format="YYYY-MM-DD" :aria-label="`阶段日期${index + 1}`" :disabled="completed.has(node.id)" />
        <el-button link :disabled="completed.has(node.id)" @click="plan.nodes.splice(index, 1)">移除</el-button>
      </div>
    </details>
  </div>
</template>

<script setup>
import FeedbackDeadlineField from '@/components/FeedbackDeadlineField.vue'

const props = defineProps({ plan: { type: Object, required: true }, users: { type: Array, required: true },
  path: String, compact: Boolean, general: Boolean, completed: { type: Set, default: () => new Set() } })

function field(name) { return props.path ? `${props.path}.${name}` : name }
</script>

<style scoped>
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.el-select, .el-date-editor { width: 100%; }
.task-options summary { cursor: pointer; color: var(--jc-brand); font-size: 13px; margin: 4px 0 12px; }
.node-heading { display: flex; justify-content: space-between; align-items: center; margin: 6px 0 10px; }
.node-edit { display: grid; grid-template-columns: minmax(0, 1fr) 165px 40px; gap: 8px; margin-bottom: 10px; }
.node-edit :deep(.el-date-editor.el-input) { width: 100%; min-width: 0; }
@media (max-width: 560px) {
  .two { grid-template-columns: 1fr; gap: 0; }
  .node-edit { grid-template-columns: minmax(0, 1fr) 40px; }
  .node-edit :deep(.el-date-editor.el-input) { grid-row: 2; }
  .node-edit .el-button { grid-column: 2; grid-row: 1; }
}
</style>
