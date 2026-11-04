<!-- 企业微信审批发起：按模板控件动态渲染，提交后由企微审批流接管 -->
<template>
  <el-dialog :model-value="modelValue" :title="`发起企业微信审批 · ${title}`" width="560px"
             @update:model-value="$emit('update:modelValue', $event)" @open="load">
    <div v-loading="loading">
      <el-alert v-if="error" type="warning" :closable="false" show-icon :title="error">
        <template v-if="needConfig">
          请管理员在「系统管理 → 企业微信接口」配置该类审批的模板 ID。
        </template>
      </el-alert>

      <template v-else-if="detail">
        <p class="jc-small jc-muted mb-12">
          模板：{{ detail.template_name }} · {{ detail.controls.length }} 个控件
          <span v-if="unsupported.length">；附件/明细表请在企微客户端补充</span>
        </p>
        <el-form label-position="top">
          <el-form-item v-for="c in fields" :key="c.id" :required="c.required">
            <template #label>
              {{ c.title }}
              <span v-if="c.required" class="req">*</span>
            </template>

            <el-select v-if="c.control === 'Selector'" v-model="form[c.title]" :multiple="c.multi"
                       :placeholder="c.placeholder || '请选择'" style="width: 100%">
              <el-option v-for="o in c.options" :key="o.key" :label="o.text" :value="o.text" />
            </el-select>

            <el-date-picker v-else-if="c.control === 'Date'" v-model="form[c.title]" type="date"
                            value-format="YYYY-MM-DD" :placeholder="c.placeholder || '选择日期'" style="width: 100%" />

            <el-input-number v-else-if="c.control === 'Money'" v-model="form[c.title]" :precision="2" :step="100"
                             :min="0" controls-position="right" style="width: 100%" />

            <el-input-number v-else-if="c.control === 'Number'" v-model="form[c.title]" controls-position="right"
                             style="width: 100%" />

            <el-input v-else-if="c.control === 'Textarea'" v-model="form[c.title]" type="textarea" :rows="3"
                      :placeholder="c.placeholder" maxlength="2000" show-word-limit />

            <el-input v-else-if="c.control === 'Contact'" v-model="form[c.title]"
                      placeholder="企业微信 UserID，多个用逗号分隔" />

            <el-input v-else v-model="form[c.title]" :placeholder="c.placeholder" maxlength="200" />
          </el-form-item>
        </el-form>
      </template>
    </div>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :disabled="!detail || !!error" :loading="submitting" @click="submit">
        提交审批
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'

const props = defineProps({
  modelValue: Boolean,
  kind: { type: String, default: 'expense' },
  title: { type: String, default: '' },
  defaults: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue', 'submitted'])

const loading = ref(false)
const submitting = ref(false)
const detail = ref(null)
const error = ref('')
const needConfig = ref(false)
const form = reactive({})

const unsupported = computed(() => (detail.value?.controls || [])
  .filter((c) => !['Text', 'Textarea', 'Number', 'Money', 'Date', 'Selector', 'Contact'].includes(c.control)))
const fields = computed(() => (detail.value?.controls || []).filter((c) => !unsupported.value.includes(c)))

// 业务字段简写 → 模板标题，方便调用方传 customer/amount 这类稳定键
const ALIAS = {
  报销金额: 'amount', 合同金额: 'amount', 客户名称: 'customer', 合同名称: 'name',
  报销事由: 'reason', 费用类型: 'category', 费用日期: 'date', 签订日期: 'date',
  项目名称: 'project', 申请人: 'applicant', 负责人: 'owner',
}

async function load() {
  loading.value = true
  error.value = ''
  needConfig.value = false
  detail.value = null
  Object.keys(form).forEach((key) => delete form[key])
  try {
    const data = await api.get(`/wecom-approval/template/${props.kind}`, null, { silent: true })
    detail.value = data
    for (const control of data.controls) {
      const preset = props.defaults[control.title] ?? props.defaults[ALIAS[control.title]]
      if (preset !== undefined && preset !== null && preset !== '') form[control.title] = preset
    }
  } catch (e) {
    error.value = e.message
    needConfig.value = e.code === 40010
  } finally {
    loading.value = false
  }
}

async function submit() {
  submitting.value = true
  try {
    const values = {}
    for (const [key, value] of Object.entries(form)) {
      if (value !== '' && value !== null && value !== undefined) values[key] = value
    }
    const result = await api.post('/wecom-approval/submit', { kind: props.kind, values })
    ElMessage.success(`已发起审批 ${result.sp_no}`)
    emit('submitted', result)
    emit('update:modelValue', false)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.req { color: var(--jc-down); margin-left: 2px; }
</style>
