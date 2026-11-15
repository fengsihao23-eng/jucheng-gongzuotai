<template>
  <details class="visibility" @toggle="onToggle">
    <summary>按人设置可见内容</summary><p class="jc-muted jc-small">任务始终可办理；可限制为仅本人相关任务，并分别控制营收、采购、经费等资料。管理者保留完整项目管理范围。</p>
    <el-table :data="data?.items || []"><el-table-column prop="name" label="成员" min-width="130" /><el-table-column label="任务范围" width="135"><template #default="{ row }">{{ row.task_scope === 'self' ? '本人相关' : '项目授权任务' }}</template></el-table-column><el-table-column label="资料分区" min-width="250"><template #default="{ row }">{{ row.locked ? '项目管理范围' : row.sections.filter(s => s !== 'tasks').map(s => data.sections[s]).join('、') || '仅任务' }}</template></el-table-column><el-table-column width="100"><template #default="{ row }"><el-button v-if="!row.locked" link type="primary" @click="edit(row)">设置可见</el-button></template></el-table-column></el-table>
    <el-dialog v-model="open" :title="`${person?.name || ''} · 本项目可见内容`" width="min(560px,94vw)" append-to-body>
      <el-form label-position="top"><el-form-item label="任务范围"><el-radio-group v-model="form.task_scope"><el-radio value="team">项目授权任务</el-radio><el-radio value="self">仅本人负责、被指定查看或审核的任务</el-radio></el-radio-group></el-form-item><el-form-item label="可查看的资料"><el-checkbox-group v-model="form.sections"><el-checkbox v-for="(name,key) in data?.sections || {}" :key="key" :value="key" :disabled="key === 'tasks'">{{ name }}</el-checkbox></el-checkbox-group></el-form-item></el-form>
      <template #footer><el-button @click="open=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存此人可见范围</el-button></template>
    </el-dialog>
  </details>
</template>
<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceChange } from './shared'
const props = defineProps({ projectId: Number }), emit = defineEmits(['saved'])
const data = ref(null), person = ref(null), open = ref(false), saving = ref(false), form = reactive({ sections: [], task_scope: 'team', expected_revision: 0 })
async function load() { try { data.value = await api.get(`/operations/${props.projectId}/member-visibility`) } catch {} }
function onToggle(e) { if(e.target.open) load() }
function edit(row) { person.value = row; Object.assign(form, { sections: [...row.sections], task_scope: row.task_scope, expected_revision: row.revision }); open.value = true }
async function save() { saving.value = true; try { data.value = await api.put(`/operations/${props.projectId}/member-visibility/${person.value.user_id}`, form); open.value = false; emit('saved'); announceChange(); ElMessage.success('此人的项目可见范围已保存') } catch {} finally { saving.value = false } }
</script>
<style scoped>.visibility { margin: 20px 0; padding: 16px; border: 1px solid var(--jc-line); border-radius: 7px; }summary { cursor: pointer; font-weight: 600; }.el-checkbox-group { display: flex; flex-wrap: wrap; gap: 6px 12px; }.el-radio-group { display: flex; flex-direction: column; align-items: flex-start; }:deep(.el-radio__label) { white-space: normal; }</style>
