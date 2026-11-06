<template>
  <el-dialog v-model="visible" :title="initial?.id ? '调整任务安排' : '布置重点任务'" width="min(680px, 94vw)" destroy-on-close>
    <el-form ref="formRef" :model="form" label-position="top">
      <el-form-item label="要完成什么" prop="title" :rules="[{ required: true, whitespace: true, message: '填写具体任务' }]">
        <el-input v-model="form.title" maxlength="160" placeholder="例如：完成目标学校的需求确认，提交下一步推进计划" />
      </el-form-item>
      <el-form-item label="完成要求"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="6000" placeholder="写清楚预期结果，负责人就能据此反馈" /></el-form-item>
      <div class="two">
        <el-form-item label="负责人" prop="owner_id" :rules="[{ required: true, message: '选择负责人' }]"><el-select v-model="form.owner_id" filterable placeholder="从项目团队选择"><el-option v-for="m in assignable" :key="m.user_id" :value="m.user_id" :label="m.name" /></el-select></el-form-item>
        <el-form-item label="截止日期" prop="due_date" :rules="[{ required: true, message: '选择截止日期' }]"><el-date-picker v-model="form.due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
      </div>
      <el-form-item v-if="!initial?.id" label="关联学校（可选）"><el-select v-model="form.school_id" clearable filterable><el-option v-for="s in project.schools" :key="s.id" :label="s.name" :value="s.id" /></el-select></el-form-item>
      <div class="two">
        <el-form-item label="重点程度"><el-radio-group v-model="form.priority"><el-radio-button value="key">重点任务</el-radio-button><el-radio-button value="normal">一般任务</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="反馈频率"><el-radio-group v-model="form.cadence"><el-radio-button value="daily">每天反馈</el-radio-button><el-radio-button value="node">按节点反馈</el-radio-button></el-radio-group></el-form-item>
      </div>
      <template v-if="!initial?.id">
        <div class="jc-row between"><b>阶段节点 <span class="jc-muted jc-small">可选，适合分阶段完成的任务</span></b><el-button link type="primary" :disabled="form.nodes.length >= 30" @click="form.nodes.push({ name: '', due_date: form.due_date })">添加节点</el-button></div>
        <div v-for="(n, i) in form.nodes" :key="i" class="node">
          <el-input v-model="n.name" :aria-label="`第${i + 1}个节点`" :placeholder="`第 ${i + 1} 步：预期结果`" maxlength="128" />
          <el-date-picker v-model="n.due_date" type="date" value-format="YYYY-MM-DD" placeholder="节点日期" style="width: 150px; flex-shrink: 0" />
          <el-button link type="danger" @click="form.nodes.splice(i, 1)">移除</el-button>
        </div>
      </template>
    </el-form>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ initial?.id ? '保存安排' : '发布任务' }}</el-button></template>
  </el-dialog>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceChange } from './shared'
const props = defineProps({ modelValue: Boolean, project: Object, initial: Object })
const emit = defineEmits(['update:modelValue', 'saved'])
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const assignable = computed(() => props.project.members.filter(m => m.role !== 'viewer' && m.active))
const form = reactive({ nodes: [] }), formRef = ref(), saving = ref(false)
watch(visible, v => {
  if (v) Object.assign(form, { title: props.initial?.title || '', description: props.initial?.description || '',
    owner_id: props.initial?.owner_id || null, due_date: props.initial?.due_date || null, school_id: null,
    priority: props.initial?.priority || 'key', cadence: props.initial?.cadence || 'daily', nodes: [] })
})
async function save() {
  if (!await formRef.value.validate().catch(() => false)) return
  if (form.nodes.some(n => !n.name.trim() || !n.due_date || n.due_date > form.due_date)) return ElMessage.warning('请填写节点名称和日期，节点日期应不晚于任务截止日期')
  saving.value = true
  try {
    const body = { ...form, school_id: form.school_id || null }
    if (props.initial?.id) { delete body.nodes; delete body.school_id }
    const result = props.initial?.id ? await api.put(`/operations/tasks/${props.initial.id}`, body) : await api.post(`/operations/${props.project.id}/tasks`, body)
    visible.value = false; ElMessage.success('任务安排已保存'); emit('saved', result); announceChange()
  } catch {} finally { saving.value = false }
}
</script>
<style scoped>
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }.node { display: flex; gap: 8px; margin-top: 12px; align-items: center; }
@media(max-width: 560px) { .two { grid-template-columns: 1fr; gap: 0; }.node { flex-wrap: wrap; } }
</style>
