<template>
  <div>
    <div class="team-intro">
      <p class="jc-muted">按项目组成跨部门团队。执行成员可提交自行安排、反馈本人任务；部门审核人可审核分配给自己的计划；项目管理员维护任务和资料。公司审核由具备公司审核权限的人员处理。</p>
      <el-button v-if="project.can_manage" link type="primary" @click="emit('settings')">项目设置</el-button>
    </div>
    <div v-if="project.can_manage" class="add-row">
      <el-select v-model="userId" filterable placeholder="选择团队成员" style="width: 240px"><el-option v-for="u in users" :key="u.id" :value="u.id" :label="`${u.name} · ${u.dept || '跨部门'}`" /></el-select>
      <el-select v-model="memberRole" style="width: 160px"><el-option v-for="(v, k) in ROLES" :key="k" :value="k" :label="v" /></el-select>
      <el-button type="primary" :disabled="!userId" :loading="saving" @click="save">保存成员</el-button>
      <el-button @click="batchOpen = true">批量加入团队</el-button>
    </div>
    <el-dialog v-model="batchOpen" title="批量加入此项目" width="min(580px, 94vw)" append-to-body>
      <el-form label-position="top">
        <el-form-item label="按部门选择">
          <el-select v-model="batchDept" clearable placeholder="全部部门" style="width: 205px">
            <el-option v-for="d in departments" :key="d.code" :label="d.name" :value="d.code" />
          </el-select>
          <el-button :disabled="!batchDept || !batchCandidates.length" @click="pickDept">选择该部门可加入人员</el-button>
        </el-form-item>
        <el-form-item label="确认人员">
          <el-select v-model="batchIds" filterable multiple collapse-tags collapse-tags-tooltip style="width: 100%" placeholder="选择未加入的员工">
            <el-option v-for="u in available" :key="u.id" :value="u.id" :label="u.name + ' · ' + (u.department || u.dept || '跨部门')" />
          </el-select>
        </el-form-item>
        <el-form-item label="授予项目角色"><el-radio-group v-model="batchRole"><el-radio value="member">执行成员</el-radio><el-radio value="viewer">只读成员</el-radio></el-radio-group></el-form-item>
        <p class="jc-muted jc-small">权限只作用于本项目。现有团队成员的权限不会被覆盖。</p>
      </el-form>
      <template #footer><el-button @click="batchOpen = false">取消</el-button><el-button type="primary" :disabled="!batchIds.length" :loading="saving" @click="saveBatch">加入 {{ batchIds.length }} 人</el-button></template>
    </el-dialog>
    <el-table :data="project.members">
      <el-table-column prop="name" label="成员" min-width="160"><template #default="{ row }">{{ row.name }} <span v-if="row.is_owner" class="jc-muted jc-small">项目负责人</span><span v-else-if="!row.active" class="jc-down jc-small">账号已停用</span></template></el-table-column>
      <el-table-column prop="dept" label="部门" width="110" />
      <el-table-column label="项目权限" min-width="150"><template #default="{ row }">{{ ROLES[row.role] }}</template></el-table-column>
      <el-table-column v-if="project.can_manage" width="140"><template #default="{ row }"><template v-if="!row.is_owner"><el-button link type="primary" @click="userId = row.user_id; memberRole = row.role">调整</el-button><el-button link type="danger" :disabled="saving" @click="remove(row)">移除</el-button></template></template></el-table-column>
    </el-table>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { ROLES, announceChange } from './shared'
const props = defineProps({ project: Object }), emit = defineEmits(['saved', 'settings'])
const users = ref([]), userId = ref(null), memberRole = ref('member'), saving = ref(false)
const batchOpen = ref(false), batchIds = ref([]), batchDept = ref(''), batchRole = ref('member')
const available = computed(() => users.value.filter(u => !props.project.members.some(m => m.user_id === u.id)))
const departments = computed(() => Array.from(new Map(available.value.filter(u => u.dept).map(u => [u.dept, { code: u.dept, name: u.department || u.dept }])).values()))
const batchCandidates = computed(() => available.value.filter(u => !batchDept.value || u.dept === batchDept.value))
function pickDept() { batchIds.value = [...new Set([...batchIds.value, ...batchCandidates.value.map(u => u.id)])] }
async function saveBatch() {
  if (batchIds.value.length > 200) return ElMessage.warning('每次最多加入 200 人')
  saving.value = true
  try {
    const result = await api.post('/operations/' + props.project.id + '/members/batch', { user_ids: batchIds.value, role: batchRole.value })
    batchIds.value = []; batchOpen.value = false; emit('saved'); announceChange()
    ElMessage.success('已加入 ' + result.added + ' 人')
  } catch {} finally { saving.value = false }
}
onMounted(async () => { if (props.project.can_manage) { try { users.value = (await api.get('/operations/options', { project_id: props.project.id })).users.filter(u => u.id !== props.project.owner_id) } catch {} } })
async function save() {
  saving.value = true
  try { await api.post(`/operations/${props.project.id}/members`, { user_id: userId.value, role: memberRole.value }); userId.value = null; ElMessage.success('团队权限已保存'); emit('saved'); announceChange() } catch {} finally { saving.value = false }
}
async function remove(row) {
  try { await ElMessageBox.confirm(`将${row.name}移出「${props.project.name}」？移出后将失去本项目访问权限。`, '移除成员', { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' }) } catch { return }
  saving.value = true
  try {
    await api.del(`/operations/${props.project.id}/members/${row.user_id}`)
    done(row, false)
  } catch (err) {
    if (err.code !== 40901) return
    try {
      await ElMessageBox.confirm(`${err.message}。转交后该成员不再负责这些任务，确认继续？`, '成员还有未完成任务',
        { type: 'warning', confirmButtonText: '转交并移除', cancelButtonText: '取消' })
    } catch { return }
    await api.del(`/operations/${props.project.id}/members/${row.user_id}`, { transfer: true })
    done(row, true)
  } finally { saving.value = false }
}
function done(row, transferred) {
  emit('saved'); announceChange()
  ElMessage.success(transferred ? `${row.name}已移出团队，任务已转交项目负责人` : `${row.name}已移出团队`)
}
</script>
<style scoped>
.team-intro { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 6px 14px; margin-bottom: 12px; }
.team-intro p { flex: 1 1 480px; margin: 0; }.add-row { display: flex; gap: 10px; flex-wrap: wrap; margin: 24px 0; }
</style>
