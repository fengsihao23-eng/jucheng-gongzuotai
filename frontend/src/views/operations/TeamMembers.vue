<template>
  <div>
    <p class="jc-muted">按项目组成跨部门团队。执行成员反馈自己的任务；只读成员查看进展；项目管理员维护任务和资料。</p>
    <div v-if="project.can_manage" class="add-row">
      <el-select v-model="userId" filterable placeholder="选择团队成员" style="width: 240px"><el-option v-for="u in users" :key="u.id" :value="u.id" :label="`${u.name} · ${u.dept || '跨部门'}`" /></el-select>
      <el-select v-model="memberRole" style="width: 160px"><el-option v-for="(v, k) in ROLES" :key="k" :value="k" :label="v" /></el-select>
      <el-button type="primary" :disabled="!userId" :loading="saving" @click="save">保存成员</el-button>
    </div>
    <el-table :data="project.members">
      <el-table-column prop="name" label="成员" min-width="160"><template #default="{ row }">{{ row.name }} <span v-if="row.is_owner" class="jc-muted jc-small">项目负责人</span><span v-else-if="!row.active" class="jc-down jc-small">账号已停用</span></template></el-table-column>
      <el-table-column prop="dept" label="部门" width="110" />
      <el-table-column label="项目权限" min-width="150"><template #default="{ row }">{{ ROLES[row.role] }}</template></el-table-column>
      <el-table-column v-if="project.can_manage" width="140"><template #default="{ row }"><template v-if="!row.is_owner"><el-button link type="primary" @click="userId = row.user_id; memberRole = row.role">调整</el-button><el-button link type="danger" :disabled="saving" @click="remove(row)">移除</el-button></template></template></el-table-column>
    </el-table>
  </div>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { ROLES, announceChange } from './shared'
const props = defineProps({ project: Object }), emit = defineEmits(['saved'])
const users = ref([]), userId = ref(null), memberRole = ref('member'), saving = ref(false)
onMounted(async () => { if (props.project.can_manage) { try { users.value = (await api.get('/operations/options', { project_id: props.project.id })).users.filter(u => u.id !== props.project.owner_id) } catch {} } })
async function save() {
  saving.value = true
  try { await api.post(`/operations/${props.project.id}/members`, { user_id: userId.value, role: memberRole.value }); userId.value = null; ElMessage.success('团队权限已保存'); emit('saved'); announceChange() } catch {} finally { saving.value = false }
}
async function remove(row) {
  try { await ElMessageBox.confirm(`将${row.name}移出「${props.project.name}」？移出后将失去本项目访问权限。`, '移除成员', { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' }) } catch { return }
  saving.value = true
  try { await api.del(`/operations/${props.project.id}/members/${row.user_id}`); emit('saved'); announceChange(); ElMessage.success('成员已移除') } catch {} finally { saving.value = false }
}
</script>
<style scoped>.add-row { display: flex; gap: 10px; flex-wrap: wrap; margin: 24px 0; }</style>
