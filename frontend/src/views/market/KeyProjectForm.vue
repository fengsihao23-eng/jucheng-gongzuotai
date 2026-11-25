<template>
  <el-dialog v-model="visible" :title="initial ? '编辑重点项目' : '新建重点项目'"
    :width="initial ? 'min(620px, 94vw)' : 'min(800px, 94vw)'" class="key-project-form"
    append-to-body destroy-on-close :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <el-form ref="formRef" :model="form" :disabled="saving" label-position="top" scroll-to-error>
      <el-form-item label="项目名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写项目名称' }]">
        <el-input v-model="form.name" aria-label="重点项目名称" maxlength="128" />
      </el-form-item>
      <el-form-item label="项目内容 / 需要完成什么">
        <el-input v-model="form.content" type="textarea" :rows="3" maxlength="6000" aria-label="重点项目内容" placeholder="说明项目目标、工作内容和要求" />
      </el-form-item>
      <el-form-item label="项目负责人" prop="owner_id" :rules="[{ required: true, message: '选择项目负责人' }]">
        <el-select v-model="form.owner_id" filterable aria-label="重点项目负责人">
          <el-option v-for="user in users" :key="user.id" :value="user.id" :label="user.name" />
        </el-select>
      </el-form-item>
      <div class="two">
        <el-form-item label="准备日期" prop="preparation_on" :rules="[{ required: true, message: '选择准备日期' }]">
          <el-date-picker v-model="form.preparation_on" type="date" value-format="YYYY-MM-DD" aria-label="项目准备日期" />
        </el-form-item>
        <el-form-item label="计划完成日期" prop="due_on" :rules="[{ required: true, message: '选择完成日期' }]">
          <el-date-picker v-model="form.due_on" type="date" value-format="YYYY-MM-DD" aria-label="项目完成日期" />
        </el-form-item>
      </div>
      <details class="additional">
        <summary>客户与业务信息</summary>
        <div class="two mt-16">
          <el-form-item label="客户 / 合作单位（可选）"><el-input v-model="form.customer_name" maxlength="128" aria-label="重点项目客户" /></el-form-item>
          <el-form-item label="业务线">
            <el-select v-model="form.business_line" aria-label="重点项目业务线">
              <el-option v-for="(name, key) in BUSINESS_LINES" :key="key" :label="name" :value="key" />
            </el-select>
          </el-form-item>
        </div>
      </details>
      <el-form-item v-if="initial" label="项目状态">
        <el-select v-model="form.status" aria-label="重点项目状态">
          <el-option v-for="(value, key) in PROJECT_STATUS" :key="key" :value="Number(key)" :label="value" />
        </el-select>
      </el-form-item>
      <section v-else class="project-tasks" aria-label="项目任务">
        <el-form-item label="项目团队（可选）">
          <el-select v-model="teamMemberIds" filterable multiple collapse-tags collapse-tags-tooltip aria-label="新建项目团队成员" placeholder="选择项目团队成员">
            <el-option v-for="person in teamUsers.filter(user => user.id !== form.owner_id)" :key="person.id" :value="person.id"
              :label="`${person.name} · ${person.department || person.dept || '跨部门'}`" />
          </el-select>
        </el-form-item>
        <h3>项目任务</h3>
        <p class="jc-small jc-muted">可添加多项任务，默认沿用项目负责人和日期。创建后会通知项目和任务各自的负责人。</p>
        <section v-for="(task, index) in form.tasks" :key="task.key" class="task-plan" :aria-label="`任务 ${index + 1}`">
          <div class="jc-row between task-header">
            <h4>任务 {{ index + 1 }}</h4>
            <el-button link :aria-label="`移除任务 ${index + 1}`" @click="form.tasks.splice(index, 1)">移除</el-button>
          </div>
          <KeyTaskFields :plan="task.plan" :users="taskUsers" :path="`tasks.${index}.plan`" compact />
        </section>
        <el-button type="primary" plain @click="addTask">添加任务</el-button>
      </section>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">{{ initial ? '保存项目' : '创建重点项目' }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import { BUSINESS_LINES, PROJECT_STATUS } from '@/utils/format'
import KeyTaskFields from './KeyTaskFields.vue'
import { taskDateError, taskPlan } from './keyTaskPlan'

const props = defineProps({ modelValue: Boolean, initial: Object })
const emit = defineEmits(['update:modelValue', 'saved'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const auth = useAuth(), form = reactive({ tasks: [] }), users = ref([]), formRef = ref(), saving = ref(false)
const teamUsers = ref([]), teamMemberIds = ref([])
const taskUsers = computed(() => {
  const listed = new Set(users.value.map(user => user.id))
  return [...users.value, ...teamUsers.value.filter(user => teamMemberIds.value.includes(user.id) && !listed.has(user.id))]
})
let nextTaskKey = 0, optionsVersion = 0

watch(visible, async open => {
  const version = ++optionsVersion
  if (!open) return
  const project = props.initial
  teamMemberIds.value = []
  teamUsers.value = []
  Object.assign(form, { name: project?.name || '', content: project?.content || '',
    owner_id: project?.owner_id || auth.profile?.id, preparation_on: project?.start_date || null,
    due_on: project?.plan_end_date || null, customer_name: project?.customer_name || '',
    business_line: project?.business_line || 'TD', status: project?.status || 2, tasks: [] })
  try {
    const options = await api.get('/market/key-options', { include_team: project ? undefined : true })
    if (version !== optionsVersion) return
    users.value = options.users
    teamUsers.value = options.team_users || []
    if (!form.preparation_on) form.preparation_on = options.today
  } catch { /* 请求错误由接口提示统一显示。 */ }
})

watch(teamMemberIds, () => {
  const available = new Set(taskUsers.value.map(user => user.id))
  for (const { plan } of form.tasks) {
    if (!available.has(plan.owner_id)) plan.owner_id = null
  }
})

const inheritedFields = ['owner_id', 'preparation_on', 'due_on']
watch(() => inheritedFields.map(field => form[field]), (values, previous) => {
  for (const { plan } of form.tasks) {
    inheritedFields.forEach((field, index) => {
      if (plan[field] === previous[index]) plan[field] = values[index]
    })
  }
}, { flush: 'sync' })

function addTask() { form.tasks.push({ key: ++nextTaskKey, plan: taskPlan(form) }) }

async function save() {
  if (saving.value || !formRef.value) return
  // Lock the submit action before validation, including concurrent clicks.
  saving.value = true
  try {
    if (!await formRef.value.validate().catch(() => false)) return
    if (form.preparation_on > form.due_on) return ElMessage.warning('准备日期不能晚于完成日期')
    const { tasks, ...project } = form
    for (const [index, task] of tasks.entries()) {
      const error = taskDateError(task.plan, project)
      if (error) return ElMessage.warning(`任务 ${index + 1}，${error}`)
    }
    const result = props.initial
      ? await api.put(`/market/projects/${props.initial.project_code}`, { ...project, expected_revision: props.initial.revision })
      : await api.post('/market/projects', { ...project, tasks: tasks.map(task => task.plan), team_member_ids: teamMemberIds.value })
    visible.value = false
    ElMessage.success(props.initial ? '重点项目已保存' : tasks.length ? `重点项目及 ${tasks.length} 项任务已创建` : '重点项目已创建')
    emit('saved', result)
    announceBusinessChange()
  } catch { /* 提交失败时保留已填内容。 */ }
  finally { saving.value = false }
}
</script>

<style scoped>
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.el-select, .el-date-editor { width: 100%; }
.additional { margin-bottom: 18px; }
.additional summary { cursor: pointer; color: var(--jc-ink-3); font-size: 13px; }
.project-tasks { border-top: 1px solid var(--jc-line-2); padding-top: 14px; }
.project-tasks h3 { margin: 0; font-size: 16px; }
.project-tasks > p { margin: 7px 0 12px; }
.task-plan { border: 1px solid var(--jc-line); border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; }
.task-header { margin-bottom: 8px; }
.task-header h4 { margin: 0; font-size: 14px; }
:global(.key-project-form > .el-dialog__body) { max-height: min(70dvh, calc(100dvh - 150px)); overflow: auto; }
@media (max-width: 560px) { .two { grid-template-columns: 1fr; gap: 0; } }
</style>
