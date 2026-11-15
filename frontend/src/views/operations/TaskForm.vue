<template>
  <el-dialog v-model="visible" :title="initial?.id ? '调整任务安排' : form.source === 'self' ? '提交自行安排任务' : '布置重点任务'" width="min(680px, 94vw)" align-center append-to-body destroy-on-close @closed="emit('closed')">
    <el-form ref="formRef" :model="form" label-position="top">
      <div class="two">
        <el-form-item label="任务来源"><span v-if="form.source === 'project_plan'">负责人计划</span><el-radio-group v-else v-model="form.source" :disabled="!!initial?.id" @change="sourceChanged"><el-radio-button value="company" :disabled="!project.can_manage">公司安排</el-radio-button><el-radio-button value="self">自行安排</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="计划月份" prop="plan_month" :rules="[{required:true,message:'选择计划月份'}]"><el-date-picker v-model="form.plan_month" type="month" value-format="YYYY-MM" :clearable="false" style="width:100%" @change="monthChanged=true" /></el-form-item>
      </div>
      <el-form-item label="要完成什么" prop="title" :rules="[{ required: true, whitespace: true, message: '填写具体任务' }]">
        <el-input v-model="form.title" maxlength="160" placeholder="例如：完成目标学校的需求确认，提交下一步推进计划" />
      </el-form-item>
      <el-form-item label="完成要求"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="6000" placeholder="写清楚预期结果，负责人就能据此反馈" /></el-form-item>
      <div class="two">
        <el-form-item label="负责人" prop="owner_id" :rules="[{ required: true, message: '选择负责人' }]"><el-select v-model="form.owner_id" :disabled="form.source === 'self'" filterable placeholder="从项目团队选择"><el-option v-for="m in assignable" :key="m.user_id" :value="m.user_id" :label="m.name" /></el-select></el-form-item>
        <el-form-item label="截止日期" prop="due_date" :rules="[{ required: true, message: '选择截止日期' }]"><el-date-picker v-model="form.due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" @change="v => {if(v && !monthChanged)form.plan_month=v.slice(0,7)}" /></el-form-item>
      </div>
      <template v-if="form.source === 'self'">
        <el-alert title="自行安排需依次经过部门和公司审核；修改已提交安排后重新审核。" type="info" :closable="false" class="mb-16" />
        <div class="two">
          <el-form-item label="部门审核人" prop="department_reviewer_id" :rules="[{required:true,message:'选择部门审核人'}]"><el-select v-model="form.department_reviewer_id" filterable><el-option v-for="u in departmentReviewers" :key="u.id" :label="u.name" :value="u.id" /></el-select></el-form-item>
          <el-form-item label="公司审核人" prop="company_reviewer_id" :rules="[{required:true,message:'选择公司审核人'}]"><el-select v-model="form.company_reviewer_id" filterable><el-option v-for="u in companyReviewers" :key="u.id" :label="u.name" :value="u.id" /></el-select></el-form-item>
        </div>
        <p v-if="!departmentReviewers.length || !companyReviewers.length" class="jc-small jc-muted">部门审核人由项目团队指定；公司审核人需具有公司审核权限。请项目管理员先完善审核人员。</p>
      </template>
      <el-form-item v-if="project.schools.length" label="事项类型"><el-select v-model="form.kind">
        <el-option label="一般事项" value="general" /><el-option label="市场开拓" value="outreach" />
      </el-select></el-form-item>
      <el-form-item v-if="project.schools.length" :label="form.kind === 'outreach' ? '目标学校' : '关联学校（可选）'"><el-select v-model="form.school_id" clearable filterable><el-option v-for="s in project.schools" :key="s.id" :label="s.name" :value="s.id" /></el-select></el-form-item>
      <template v-if="project.can_manage && form.source === 'company'">
        <el-form-item label="任务可见范围">
          <el-radio-group v-model="form.visibility"><el-radio-button value="team">全项目组</el-radio-button><el-radio-button value="selected">指定成员</el-radio-button></el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.visibility === 'selected'" label="可查看任务的成员">
          <el-select v-model="form.viewer_ids" filterable multiple collapse-tags collapse-tags-tooltip style="width: 100%" placeholder="选择项目成员">
            <el-option v-for="m in project.members.filter(x => x.active)" :key="m.user_id" :value="m.user_id" :label="m.name" />
          </el-select>
          <p class="jc-muted jc-small">负责人、发布人、当前审核人及有权管理该项目的人员可查看。</p>
        </el-form-item>
      </template>
      <div class="two">
        <el-form-item label="重点程度"><el-radio-group v-model="form.priority"><el-radio-button value="key">重点任务</el-radio-button><el-radio-button value="normal">一般任务</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="反馈频率"><el-select v-model="form.cadence" aria-label="反馈频率"><el-option v-for="(label, value) in CADENCES" :key="value" :value="value" :label="label" /></el-select></el-form-item>
      </div>
      <p class="jc-small jc-muted">按自然日计算，进展反馈后重新起算。每天 16:00 汇总到期、逾期及待回复事项；已读通知不结束任务。</p>
      <template v-if="!initial?.id">
        <div class="jc-row between"><b>阶段节点 <span class="jc-muted jc-small">可选，适合分阶段完成的任务</span></b><el-button link type="primary" :disabled="form.nodes.length >= 30" @click="form.nodes.push({ name: '', due_date: form.due_date })">添加节点</el-button></div>
        <div v-for="(n, i) in form.nodes" :key="i" class="node">
          <el-input v-model="n.name" :aria-label="`第${i + 1}个节点`" :placeholder="`第 ${i + 1} 步：预期结果`" maxlength="128" />
          <el-date-picker v-model="n.due_date" type="date" value-format="YYYY-MM-DD" placeholder="节点日期" style="width: 150px; flex-shrink: 0" />
          <el-button link type="danger" @click="form.nodes.splice(i, 1)">移除</el-button>
        </div>
      </template>
    </el-form>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ form.source === 'self' ? '提交审核' : initial?.id ? '保存安排' : '发布任务' }}</el-button></template>
  </el-dialog>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { announceChange, CADENCES } from './shared'
const props = defineProps({ modelValue: Boolean, project: Object, initial: Object, schoolPreset: Number, mode: {type:String,default:'company'} })
const emit = defineEmits(['update:modelValue', 'saved', 'closed'])
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const assignable = computed(() => props.project.members.filter(m => m.role !== 'viewer' && m.active))
const form = reactive({ nodes: [] }), formRef = ref(), saving = ref(false), auth = useAuth(), monthChanged=ref(false), options=ref({})
const departmentReviewers = computed(() => (options.value.department_reviewers || []).filter(u => u.id !== form.owner_id))
const companyReviewers = computed(() => (options.value.company_reviewers || []).filter(u => u.id !== form.owner_id && u.id !== form.department_reviewer_id))
function sourceChanged() { form.owner_id = form.source === 'self' ? auth.profile?.id : null }
watch(visible, async v => {
  monthChanged.value=!!props.initial?.id
  if (v) Object.assign(form, { title: props.initial?.title || '', description: props.initial?.description || '',
    owner_id: props.initial?.owner_id || (props.mode === 'self' ? auth.profile?.id : null), due_date: props.initial?.due_date || null, school_id: props.initial?.school_id || props.schoolPreset || null,
    kind: props.initial?.kind || (props.schoolPreset ? 'outreach' : 'general'), visibility: props.initial?.visibility || 'team',
    viewer_ids: [...(props.initial?.viewer_ids || [])],
    priority: props.initial?.priority || 'key', cadence: props.initial?.cadence || 'daily', nodes: [], source:props.initial?.source || props.mode,
    plan_month:props.initial?.plan_month || props.project.today.slice(0,7), department_reviewer_id:props.initial?.department_reviewer_id || null,
    company_reviewer_id:props.initial?.company_reviewer_id || null })
  if(v) { try {options.value=await api.get('/operations/options',{project_id:props.project.id})} catch {} }
}, { immediate: true })
async function save() {
  if (!await formRef.value.validate().catch(() => false)) return
  if (form.kind === 'outreach' && !form.school_id) return ElMessage.warning('请选择市场开拓的学校')
  if (form.nodes.some(n => !n.name.trim() || !n.due_date || n.due_date > form.due_date)) return ElMessage.warning('请填写节点名称和日期，节点日期应不晚于任务截止日期')
  saving.value = true
  try {
    const body = { ...form, school_id: form.school_id || null }
    if (form.source !== 'self') {delete body.department_reviewer_id;delete body.company_reviewer_id}
    if (!props.project.can_manage || form.source !== 'company') { delete body.visibility; delete body.viewer_ids }
    else if (form.visibility !== 'selected') body.viewer_ids = []
    if (props.initial?.id) { delete body.nodes; if (!body.school_id) delete body.school_id; delete body.source; body.expected_revision = props.initial.revision }
    const result = props.initial?.id ? await api.put(`/operations/tasks/${props.initial.id}`, body) : await api.post(`/operations/${props.project.id}/tasks`, body)
    visible.value = false; ElMessage.success('任务安排已保存'); emit('saved', result); announceChange()
  } catch {} finally { saving.value = false }
}
</script>
<style scoped>
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }.node { display: flex; gap: 8px; margin-top: 12px; align-items: center; }
@media(max-width: 560px) { .two { grid-template-columns: 1fr; gap: 0; }.node { flex-wrap: wrap; } }
</style>
