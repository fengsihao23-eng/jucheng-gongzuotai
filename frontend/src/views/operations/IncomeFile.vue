<template>
  <el-button @click="open = true">按校导入每日收入</el-button>
  <el-dialog v-model="open" title="核对每日收入后导入" width="min(720px, 94vw)" append-to-body @closed="reset">
    <p class="jc-muted jc-small">先把源表按学校和实际收款日期合计。表头：学校、实际收款日期、收入金额、备注。学校需先建档；确认为项目公共收入的行可留空学校。</p>
    <UploadBox accept=".csv,.xlsx" title="选择 UTF-8 CSV 或单页 XLSX（3 MB 内）" @change="choose" />
    <el-button class="mt-8" :disabled="!file" :loading="loading" @click="preview">读取并核对</el-button>
    <div v-if="review" class="mt-16">
      <p>有效 {{ review.rows }} 行，{{ money(review.amount) }}；拟新增 <b>{{ review.new_rows }} 行，{{ money(review.new_amount) }}</b>。已有相同记录 {{ review.unchanged }} 行。</p>
      <el-alert v-if="review.error_count" type="warning" :closable="false" :title="'需更正 ' + review.error_count + ' 行'">
        <ul><li v-for="e in review.errors" :key="e.line">第 {{ e.line }} 行：{{ e.message }}</li></ul>
      </el-alert>
      <el-table :data="review.sample" size="small" class="mt-8">
        <el-table-column label="学校" min-width="110"><template #default="{ row }">{{ row.school || '项目公共收入' }}</template></el-table-column>
        <el-table-column label="实际收款日期" prop="income_date" width="124" />
        <el-table-column label="收入金额" prop="amount" width="115" />
        <el-table-column label="备注" prop="note" min-width="100" show-overflow-tooltip />
      </el-table>
    </div>
    <template #footer><el-button @click="open = false">取消</el-button>
      <el-button type="primary" :disabled="!review?.new_rows || !!review?.error_count || review.already_imported" :loading="saving" @click="confirm">确认新增</el-button>
    </template>
  </el-dialog>
</template>
<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { money } from '@/utils/format'
const props = defineProps({ projectId: Number }), emit = defineEmits(['saved'])
const open = ref(false), file = ref(null), review = ref(null), loading = ref(false), saving = ref(false)
function choose(f) { file.value = f; review.value = null }
function reset() { choose(null) }
const endpoint = () => '/operations/' + props.projectId + '/income-file/'
async function preview() {
  const selected = file.value; loading.value = true; review.value = null
  try { const r = await api.upload(endpoint() + 'preview', selected); if (file.value === selected) review.value = r } catch {} finally { loading.value = false }
}
async function confirm() {
  const r = review.value
  saving.value = true
  try {
    const out = await api.upload(endpoint() + 'confirm', file.value, { expected_sha: r.sha256, expected_rows: r.new_rows, expected_amount: r.new_amount })
    ElMessage.success(out.already_imported ? '记录已存在，无需重复导入' : '每日收入已登记')
    open.value = false; emit('saved')
  } catch {} finally { saving.value = false }
}
</script>
