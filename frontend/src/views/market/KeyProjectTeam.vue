<template>
  <section :aria-label="project.is_key ? '重点项目团队' : '一般项目团队'">
    <p v-if="project.can_manage" class="jc-small jc-muted team-hint">团队成员可查看项目任务和跟进记录，并收到企业微信项目通知。</p>
    <div v-if="project.can_manage" class="team-tools">
      <el-select v-model="selected" :loading="loading" :disabled="saving" :aria-label="project.is_key ? '选择重点项目成员' : '选择一般项目成员'"
        placeholder="选择团队成员" filterable multiple collapse-tags collapse-tags-tooltip class="team-select"
        @visible-change="open => { if (open && !loaded) loadOptions() }">
        <el-option v-for="person in available" :key="person.id" :value="person.id"
          :label="`${person.name} · ${person.department || person.dept || '跨部门'}`" />
      </el-select>
      <el-button type="primary" :disabled="!selected.length" :loading="saving" @click="add">加入团队</el-button>
    </div>
    <el-table :data="project.team" row-key="id" empty-text="暂无项目团队成员">
      <el-table-column label="成员" min-width="110">
        <template #default="{ row }">
          {{ row.name }}
          <small v-if="!row.active" class="jc-muted">账号不可用</small>
        </template>
      </el-table-column>
      <el-table-column label="部门" min-width="105"><template #default="{ row }">{{ row.department || row.dept || '—' }}</template></el-table-column>
      <el-table-column label="项目角色" min-width="106">
        <template #default="{ row }"><StatusPill :text="row.is_owner ? '项目负责人' : '团队成员'" :tone="row.is_owner ? 'brand' : 'info'" /></template>
      </el-table-column>
      <el-table-column v-if="project.can_manage" label="操作" width="85">
        <template #default="{ row }"><el-button v-if="!row.is_owner" link type="danger" :aria-label="`移除成员 ${row.name}`" @click="remove(row)">移除</el-button></template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import { deleteRecord } from '@/composables/useRecordDeletion'

const props = defineProps({ project: { type: Object, required: true } })
const emit = defineEmits(['saved'])
const people = ref([]), selected = ref([]), saving = ref(false), loading = ref(false), loaded = ref(false)
const endpoint = computed(() => `/market/projects/${encodeURIComponent(props.project.project_code)}/team`)
const scope = computed(() => props.project.is_key ? undefined : 'general')
const available = computed(() => people.value.filter(person => !props.project.team.some(member => member.id === person.id)))

async function loadOptions() {
  if (loading.value) return
  loading.value = true
  try {
    people.value = (await api.get('/market/key-options', { project_code: props.project.project_code,
      scope: scope.value, include_team: true })).team_users || []
    loaded.value = true
  } catch { /* 统一显示 API 错误，重新打开可重试。 */ }
  finally { loading.value = false }
}

function refresh() {
  emit('saved')
  announceBusinessChange()
}

async function add() {
  if (!selected.value.length || saving.value) return
  saving.value = true
  try {
    const result = await api.post(endpoint.value, { user_ids: selected.value }, { params: { scope: scope.value } })
    selected.value = []
    refresh()
    ElMessage.success(result.added ? `已加入 ${result.added} 位团队成员` : '所选员工已在团队中')
  } catch { /* 保留当前选择以便重试。 */ }
  finally { saving.value = false }
}

function remove(member) {
  deleteRecord({ endpoint: `${endpoint.value}/${member.id}`, query: { scope: scope.value }, title: '移除团队成员', name: member.name,
    description: `将「${member.name}」移出项目团队后，该成员的团队访问权限和消息订阅会取消。`,
    confirmLabel: '确认移除', onDeleted: refresh })
}
</script>

<style scoped>
.team-tools { display: flex; align-items: flex-start; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }
.team-select { width: min(340px, 100%); }
.team-hint { margin: 0 0 12px; }
</style>
