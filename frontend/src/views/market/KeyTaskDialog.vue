<template>
  <RecordDetailDialog v-model="visible" dialog-title="任务情况" :heading="item?.title" :subtitle="project.name"
    :status="item?.is_delayed ? '逾期未完成' : item?.status_text" :status-tone="item?.is_delayed ? 'down' : item?.status === 3 ? 'up' : 'brand'"
    :target="{ kind: 'key-task', id: item?.id }"
    :facts="item ? [{ label: '负责人', value: item.owner_name || '—' }, { label: '截止日期', value: item.plan_date || '—' },
      { label: '反馈频率', value: cadenceLabel[item.cadence] || '按节点反馈', hint: item.feedback_deadline_pending ? `反馈截止 ${feedbackTime(item.feedback_due_at)}` : item.feedback_due_on ? `下次反馈 ${item.feedback_due_on}` : '' }] : []">
    <template v-if="item?.can_manage && item.status !== 3 && !project.paused && project.status !== 5" #actions>
      <el-button plain @click="emit('edit', item); visible = false">调整安排</el-button>
    </template>
    <div v-if="item">
      <h3>完成要求</h3>
      <p v-if="item.requirements" class="plain">{{ item.requirements }}</p>
      <h4 v-if="item.nodes.length">阶段节点</h4><ul class="node-list"><li v-for="n in item.nodes" :key="n.id"><span>{{ n.name }}</span><small :class="n.overdue?'jc-down':'jc-muted'">{{ n.due_on }} · {{ n.completed_on ? `已完成 ${n.completed_on}` : n.overdue?'已逾期':'待完成' }}</small></li></ul>
      <p v-if="item.problems" class="plain jc-down">存在问题：{{ item.problems }}</p><p v-if="item.next_step" class="plain">下一步：{{ item.next_step }}</p>
      <p v-if="item.feedback_status === 'overdue'" class="jc-small jc-down">逾期未反馈</p>
    </div>
    <el-empty v-else-if="!loading" description="无法读取任务，请检查权限或重新打开" />
    <template #progress>
      <div v-if="canFeedback" class="task-feedback">
      <h3>任务反馈</h3><el-form ref="formRef" :model="form" label-position="top">
          <el-form-item label="当前进展" prop="content" :rules="[{required:true,whitespace:true,message:'填写实际进展'}]"><el-input v-model="form.content" :aria-label="`${taskLabel}进展`" type="textarea" :rows="3" maxlength="6000" placeholder="已完成什么、当前进度如何" /></el-form-item>
          <el-form-item label="存在问题"><el-input v-model="form.problems" :aria-label="`${taskLabel}问题`" type="textarea" :rows="2" maxlength="6000" placeholder="没有问题可留空" /></el-form-item><el-form-item label="下一步"><el-input v-model="form.next_step" :aria-label="`${taskLabel}下一步`" type="textarea" :rows="2" maxlength="6000" /></el-form-item>
          <el-form-item v-if="item.nodes.some(n=>!n.completed_on)" label="本次完成的节点"><el-checkbox-group v-model="form.completed_node_ids"><el-checkbox v-for="n in item.nodes.filter(n=>!n.completed_on)" :key="n.id" :value="n.id">{{ n.name }}</el-checkbox></el-checkbox-group></el-form-item>
          <el-form-item label="任务状态"><el-radio-group v-model="form.status" :aria-label="`${taskLabel}状态`"><el-radio-button :value="3">已完成</el-radio-button><el-radio-button :value="2">未完成</el-radio-button></el-radio-group></el-form-item>
          <el-form-item v-if="form.status===3" label="实际完成日期"><el-date-picker v-model="form.completed_on" type="date" value-format="YYYY-MM-DD" aria-label="任务实际完成日期" /></el-form-item>
        </el-form>
      </div>
      <p v-else class="jc-small jc-muted">{{ item?.status === 3 ? '任务已完成，可在过程记录查看历次反馈。' : '可在过程记录查看任务反馈。' }}</p>
    </template>
    <template v-if="canFeedback" #footer>
      <el-button type="primary" :loading="saving" @click="submit">提交进展</el-button>
    </template>
    <template v-if="item?.notification_deliveries?.length" #history>
      <WecomDeliveryStatus :rows="item.notification_deliveries" />
    </template>
  </RecordDetailDialog>
</template>
<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import WecomDeliveryStatus from '@/components/WecomDeliveryStatus.vue'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import { feedbackTime } from '@/utils/taskFeedback'
const props = defineProps({ modelValue: Boolean, taskId: Number, project: Object, tasksPath: String })
const emit = defineEmits(['update:modelValue', 'saved', 'edit'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const item = ref(null), loading = ref(false), saving = ref(false), form = reactive({}), formRef = ref()
const taskLabel = computed(() => props.project.is_key ? '重点任务' : '任务')
const taskPath = computed(() => `${props.tasksPath}/${props.taskId}`)
const canFeedback = computed(() => item.value?.can_feedback && item.value.status !== 3 && !props.project.paused && props.project.status !== 5)
const cadenceLabel = { daily: '每天反馈', weekly: '每周反馈', node: '按节点反馈' }
let version = 0

async function load() {
  if (!visible.value || !props.taskId) return
  const request = ++version
  loading.value = true
  try {
    const result = await api.get(taskPath.value)
    if (request !== version || !visible.value) return
    item.value = result
    Object.assign(form, { content: '', problems: result.problems || '', next_step: result.next_step || '',
      status: 2, completed_on: props.project.today, completed_node_ids: [] })
    await nextTick()
    formRef.value?.clearValidate()
  } catch {
    if (request === version) item.value = null
  } finally {
    if (request === version) loading.value = false
  }
}

async function submit() {
  if (saving.value || !item.value || !await formRef.value?.validate().catch(() => false)) return
  if (form.status === 3 && item.value.nodes.some(node => !node.completed_on && !form.completed_node_ids.includes(node.id))) {
    return ElMessage.warning('请先确认所有阶段节点完成')
  }
  saving.value = true
  try {
    await api.post(`${taskPath.value}/feedback`, { ...form, completed_on: form.status === 3 ? form.completed_on : null,
      expected_revision: item.value.revision })
    ElMessage.success('进展已保存')
    await load()
    announceBusinessChange()
    emit('saved')
  } catch { /* 提交失败时保留已填写的反馈。 */ }
  finally { saving.value = false }
}

watch([visible, taskPath], ([open]) => {
  version++
  item.value = null
  if (open) load()
}, { immediate: true })
</script>
<style scoped>
.plain { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.node-list { list-style: none; padding: 0; margin: 0; }
.node-list li { display: grid; gap: 4px; padding: 9px 0; border-bottom: 1px solid var(--jc-line-2); }
.task-feedback :deep(.el-checkbox-group) { display: grid; }
</style>
