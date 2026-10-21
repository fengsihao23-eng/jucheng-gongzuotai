<!-- 合同新增 / 编辑对话框（人工录入 source_type=2；编辑留痕） -->
<template>
  <el-dialog :model-value="modelValue" :title="contract ? `编辑合同 ${contract.contract_no}` : '新增合同'" width="620px"
             @update:model-value="(v) => emit('update:modelValue', v)" @open="init">
    <el-alert v-if="contract?.source_type === 1" type="info" :closable="false" show-icon class="mb-16"
              title="该合同由企微审批自动登记，修改将写入操作审计（前后值）" />
    <el-form ref="formRef" :model="form" :rules="rules" label-width="96px">
      <el-form-item label="合同编号" prop="contract_no">
        <el-input v-model="form.contract_no" placeholder="留空自动生成 HT-年份-流水号" />
      </el-form-item>
      <el-form-item label="合同名称" prop="name"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="所属项目" prop="project_code">
        <el-select v-model="form.project_code" filterable clearable placeholder="可选" style="width: 100%" @change="onProject">
          <el-option v-for="p in opts.projects" :key="p.code" :label="`${p.code} ${p.name}`" :value="p.code" />
        </el-select>
      </el-form-item>
      <el-form-item label="客户名称" prop="customer_name"><el-input v-model="form.customer_name" /></el-form-item>
      <div class="jc-grid c2" style="gap: 0 16px">
        <el-form-item label="合同金额" prop="amount">
          <el-input-number v-model="form.amount" :min="0.01" :precision="2" :step="10000" controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-form-item label="签订日期" prop="sign_date">
          <el-date-picker v-model="form.sign_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="业务线" prop="business_line">
          <el-select v-model="form.business_line" style="width: 100%">
            <el-option v-for="(n, k) in BUSINESS_LINES" :key="k" :label="n" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="销售负责人" prop="owner_id">
          <el-select v-model="form.owner_id" filterable clearable style="width: 100%">
            <el-option v-for="u in opts.owners" :key="u.id" :label="u.name" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="contract" label="状态" prop="status">
          <el-select v-model="form.status" style="width: 100%">
            <el-option v-for="(n, k) in CONTRACT_STATUS" :key="k" :label="n" :value="Number(k)" />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item label="备注" prop="remark"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { BUSINESS_LINES, CONTRACT_STATUS } from '@/utils/format'

const props = defineProps({ modelValue: Boolean, contract: Object })
const emit = defineEmits(['update:modelValue', 'saved'])
const formRef = ref()
const saving = ref(false)
const opts = reactive({ projects: [], owners: [] })
const blank = () => ({ contract_no: '', name: '', project_code: '', customer_name: '', amount: null, sign_date: '',
                       business_line: '', owner_id: null, status: 1, remark: '' })
const form = reactive(blank())
const rules = {
  customer_name: [{ required: true, message: '请填写客户名称' }],
  amount: [{ required: true, message: '请填写合同金额' }],
  sign_date: [{ required: true, message: '请选择签订日期' }],
  business_line: [{ required: true, message: '请选择业务线' }],
}

async function init() {
  Object.assign(form, blank())
  if (props.contract) {
    const c = props.contract
    Object.assign(form, { contract_no: c.contract_no, name: c.name, project_code: c.project_code || '', customer_name: c.customer_name,
                          amount: Number(c.amount), sign_date: c.sign_date, business_line: c.business_line, owner_id: c.owner_id,
                          status: c.status, remark: c.remark || '' })
  }
  if (!opts.projects.length) Object.assign(opts, await api.get('/contracts/options'))
}
function onProject(code) {
  const p = opts.projects.find((x) => x.code === code)
  if (!p) return
  if (!form.customer_name) form.customer_name = p.customer_name
  if (!form.business_line) form.business_line = p.business_line
  if (!form.name) form.name = p.name + '合同'
}
async function save() {
  await formRef.value.validate()
  saving.value = true
  try {
    const body = { ...form, amount: String(form.amount) }
    if (!props.contract) delete body.status
    const c = props.contract ? await api.put(`/contracts/${props.contract.id}`, body) : await api.post('/contracts', body)
    ElMessage.success('已保存')
    emit('update:modelValue', false)
    emit('saved', c)
  } finally { saving.value = false }
}
</script>
