<template>
  <el-dialog v-model="visible" :title="initial?.id ? '编辑运营项目' : '新建运营项目'" width="min(630px, 94vw)" class="ops-project-form" destroy-on-close>
    <el-form ref="formRef" :model="form" label-position="top" @submit.prevent="save">
      <el-form-item label="项目名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写项目名称' }]">
        <el-input v-model="form.name" maxlength="128" aria-label="项目名称" placeholder="例如：智慧校园运营" />
      </el-form-item>
      <div class="two">
        <el-form-item label="项目分类" prop="category" :rules="[{ required: true, message: '选择或填写分类' }]">
          <el-select v-model="form.category" filterable allow-create default-first-option @change="suggestForNewCategory">
            <el-option v-for="v in categories" :key="v" :label="v" :value="v" />
          </el-select>
        </el-form-item>
        <el-form-item label="项目负责人" prop="owner_id" :rules="[{ required: true, message: '选择负责人' }]">
          <el-select v-model="form.owner_id" filterable><el-option v-for="u in users" :key="u.id" :value="u.id" :label="`${u.name} · ${u.dept || '跨部门'}`" /></el-select>
        </el-form-item>
      </div>
      <p v-if="initial?.id" class="jc-small jc-muted">更换项目负责人后，原负责人保留执行成员权限，可在项目团队中调整。</p>
      <p v-else class="jc-small jc-muted">创建后会通知所选项目负责人。已绑定公司通讯录的负责人还会收到企业微信通知。</p>
      <p v-if="!initial?.id" class="jc-small jc-muted">普通管理者为他人创建项目时，会以项目管理员身份加入团队。</p>
      <el-form-item label="标准功能">
        <div class="feature-box" v-loading="loadingOptions">
          <div v-if="features.length" class="feature-heading">
            <el-checkbox :model-value="allFeaturesSelected" :indeterminate="someFeaturesSelected" @change="selectAllFeatures">全选</el-checkbox>
            <el-button link type="primary" @click="applyPreset">使用推荐组合</el-button>
          </div>
          <el-checkbox-group v-if="features.length" v-model="form.enabled_sections" aria-label="标准功能" class="feature-grid" @change="onFeaturesChanged">
            <el-checkbox v-for="option in features" :key="option.value" :value="option.value">
              {{ option.value === 'arrears' && form.name?.trim() === '一键报警' ? '欠款维护' : option.label }}
            </el-checkbox>
          </el-checkbox-group>
          <span v-if="optionError" class="jc-small jc-down">无法读取标准功能。<el-button link type="primary" @click="loadOptions">重试</el-button></span>
          <p v-else-if="features.length" class="feature-hint jc-small jc-muted">{{ initial?.id ? '取消勾选会隐藏对应功能，已有资料会保留。' : '勾选后，相关功能会出现在项目详情。' }}</p>
        </div>
      </el-form-item>
      <el-form-item label="本阶段目标"><el-input v-model="form.objective" type="textarea" :rows="3" maxlength="6000" placeholder="本月要推进哪些学校、完成什么结果" /></el-form-item>
      <div class="two">
        <el-form-item label="目标日期"><el-date-picker v-model="form.target_date" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" style="width: 100%" /></el-form-item>
        <el-form-item label="项目状态"><el-select v-model="form.status"><el-option v-for="(v, k) in PROJECT_STATUS" :key="k" :label="v" :value="k" /></el-select></el-form-item>
      </div>
      <el-checkbox v-model="form.is_key">列为重点项目</el-checkbox>
    </el-form>
    <template #footer><el-button @click="visible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="loadingOptions || !features.length" @click="save">保存项目</el-button></template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { PROJECT_STATUS, announceChange } from './shared'

const props = defineProps({ modelValue: Boolean, initial: Object })
const emit = defineEmits(['update:modelValue', 'saved'])
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const users = ref([]), categories = ref([]), formRef = ref(), saving = ref(false), auth = useAuth()
const features = ref([]), presets = ref({}), loadingOptions = ref(false), optionError = ref(false)
const form = reactive({ enabled_sections: [] })
let optionVersion = 0, changedFeatures = false, featuresRevision = 0

const allFeaturesSelected = computed(() => features.value.length === form.enabled_sections.length)
const someFeaturesSelected = computed(() => form.enabled_sections.length > 0 && !allFeaturesSelected.value)
function fillPreset() {
  form.enabled_sections = [...(presets.value[form.name?.includes('一键报警') ? 'alarm' : form.category?.includes('校园') ? 'campus' : 'general'] || [])]
}
function applyPreset() { changedFeatures = true; fillPreset() }
function onFeaturesChanged() { changedFeatures = true }
function selectAllFeatures(value) {
  form.enabled_sections = value ? features.value.map(option => option.value) : []
  onFeaturesChanged()
}
function suggestForNewCategory() { if (!props.initial?.id && !changedFeatures && features.value.length) fillPreset() }
async function loadOptions() {
  const id = ++optionVersion
  loadingOptions.value = true; optionError.value = false
  try {
    const data = await api.get('/operations/options', props.initial?.id ? { project_id: props.initial.id } : {})
    if (id !== optionVersion || !visible.value) return
    users.value = data.users
    categories.value = [...new Set(['智慧校园', '心理服务', '其他运营', ...data.categories])]
    features.value = data.features || []
    presets.value = data.feature_presets || {}
    if (!features.value.length) { optionError.value = true; return }
    if (!props.initial?.id && !changedFeatures) fillPreset()
    else form.enabled_sections = features.value.map(option => option.value).filter(key => form.enabled_sections.includes(key))
  } catch { if (id === optionVersion) optionError.value = true }
  finally { if (id === optionVersion) loadingOptions.value = false }
}
watch(visible, v => {
  optionVersion++
  if (!v) return
  const source = props.initial
  changedFeatures = false
  featuresRevision = source?.feature_settings?.revision || 0
  features.value = []
  Object.assign(form, { name: source?.name || '智慧校园', category: source?.category || '智慧校园',
    owner_id: source?.owner_id || auth.profile?.id, objective: source?.objective || '', target_date: source?.target_date || null,
    status: source?.status || 'active', is_key: source?.is_key ?? true,
    enabled_sections: [...(source?.feature_settings?.sections || [])] })
  loadOptions()
})
async function save() {
  if (loadingOptions.value || !features.value.length || saving.value) return
  if (!await formRef.value.validate().catch(() => false)) return
  saving.value = true
  try {
    const { enabled_sections, ...payload } = form
    if (!props.initial?.id || changedFeatures) payload.enabled_sections = enabled_sections
    if (props.initial?.id && changedFeatures) payload.expected_features_revision = featuresRevision
    const result = props.initial?.id ? await api.put(`/operations/${props.initial.id}`, payload) : await api.post('/operations', payload)
    visible.value = false
    ElMessage.success('运营项目已保存')
    emit('saved', result)
    announceChange()
  } catch { /* Keep the form for correction. */ } finally { saving.value = false }
}
</script>
<style scoped>
.two { display:grid;grid-template-columns:1fr 1fr;gap:16px; }
.feature-box { width:100%;min-height:42px;border:1px solid var(--jc-line);border-radius:8px;padding:10px 12px; }
.feature-heading { display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0 8px; }
.feature-grid { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:2px 6px; }
.feature-grid :deep(.el-checkbox) { margin-right:0;white-space:normal;height:auto;min-height:30px; }
.feature-grid :deep(.el-checkbox__label) { padding-left:6px;line-height:1.4; }
.feature-hint { margin:6px 0 0;line-height:1.6; }
:global(.ops-project-form > .el-dialog__body) { max-height:min(70vh, calc(100dvh - 155px));overflow:auto; }
@media(max-width:500px) { .two { grid-template-columns:1fr;gap:0; }.feature-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
</style>
