<template>
  <el-dialog v-model="visible" :title="initial ? '调整任务安排' : '安排任务'" width="min(700px, 94vw)" class="key-task-form" append-to-body destroy-on-close>
    <el-form ref="formRef" :model="form" label-position="top" scroll-to-error>
      <KeyTaskFields :plan="form" :users="users" :completed="completed" />
      <p class="jc-small jc-muted">任务安排后通知负责人。超过反馈截止时间仍未提交进展时，企业微信提醒负责人填写。</p>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存安排</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import KeyTaskFields from './KeyTaskFields.vue'
import { taskDateError, taskPlan } from './keyTaskPlan'

const props = defineProps({ modelValue: Boolean, project: Object, initial: Object })
const emit = defineEmits(['update:modelValue', 'saved'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const form = reactive({ nodes: [] }), formRef = ref(), saving = ref(false), users = ref([])
const completed = computed(() => new Set((props.initial?.nodes || []).filter(node => node.completed_on).map(node => node.id)))

watch(visible, async open => {
  if (!open) return
  Object.assign(form, taskPlan(props.project, props.initial))
  try { users.value = (await api.get('/market/key-options')).users }
  catch { /* 请求错误由接口提示统一显示。 */ }
})

async function save() {
  if (saving.value || !formRef.value) return
  saving.value = true
  try {
    if (!await formRef.value.validate().catch(() => false)) return
    const error = taskDateError(form, props.project)
    if (error) return ElMessage.warning(error)
    const path = `/market/projects/${props.project.project_code}/tasks`
    const result = props.initial
      ? await api.put(`${path}/${props.initial.id}`, { ...form, expected_revision: props.initial.revision })
      : await api.post(path, form)
    visible.value = false
    ElMessage.success('任务安排已保存')
    announceBusinessChange()
    emit('saved', result)
  } catch { /* 提交失败时保留已填内容。 */ }
  finally { saving.value = false }
}
</script>

<style scoped>
:global(.key-task-form > .el-dialog__body) { max-height: 70dvh; overflow: auto; }
</style>
