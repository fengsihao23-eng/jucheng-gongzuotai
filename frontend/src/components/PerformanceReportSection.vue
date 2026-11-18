<template>
  <Panel title="报表生成" sub="以文字方式提问，直接根据企微原表数据输出统计报表或合同清单" class="report-builder-panel mb-16">
    <form class="query-bar" @submit.prevent="submit()">
      <el-input
        v-model="question"
        aria-label="报表提问"
        clearable
        maxlength="500"
        placeholder="输入提问，例如：统计各区域合同金额、查询未收款合同清单、按负责人统计签约业绩…"
        class="query-input"
      />
      <el-button type="primary" native-type="submit" :loading="loading" :disabled="!question.trim()">生成报表</el-button>
      <el-button v-if="result" @click="clearResult">收起结果</el-button>
    </form>

    <div class="preset-row">
      <span class="jc-small jc-muted">常用提问：</span>
      <button
        v-for="item in presets"
        :key="item"
        type="button"
        class="preset-chip"
        :disabled="loading"
        @click="pickPreset(item)"
      >{{ item }}</button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mt-12" />

    <div v-if="result" v-loading="loading" class="report-output mt-12">
      <div class="output-head">
        <div class="output-meta">
          <b>{{ result.title }}</b>
          <el-tag v-for="tag in result.filters" :key="tag" size="small" type="info">{{ tag }}</el-tag>
        </div>
        <div class="output-actions">
          <el-button size="small" @click="copyAnswer">复制文字结论</el-button>
          <el-button size="small" :disabled="!result.rows?.length" @click="exportCsv">导出表格 CSV</el-button>
        </div>
      </div>

      <div class="answer-box">
        <p class="answer-text">{{ result.answer }}</p>
      </div>

      <el-table
        v-if="result.rows?.length"
        :data="result.rows"
        size="small"
        border
        max-height="420"
        class="report-table"
      >
        <el-table-column
          v-for="col in result.columns"
          :key="col.prop"
          :prop="col.prop"
          :label="col.label"
          :width="col.width"
          :min-width="col.min_width"
          :align="col.align || 'left'"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span v-if="col.kind === 'money'" class="jc-num">{{ formatMoney(row[col.prop], row[col.prop + '_text']) }}</span>
            <el-button
              v-else-if="col.kind === 'link' && row.detail_path"
              link
              type="primary"
              class="row-link"
              @click="emit('open-row', row)"
            >{{ row[col.prop] }}</el-button>
            <span v-else>{{ row[col.prop] ?? '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="result.truncated" class="jc-small jc-muted mt-8">已展示前 {{ result.rows.length }} 条明细（共 {{ result.total_rows }} 条），可补充更具体的筛选词缩小范围。</p>
    </div>
  </Panel>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'

const props = defineProps({
  year: { type: String, default: '2026' },
  start: { type: String, default: '' },
  end: { type: String, default: '' },
})
const emit = defineEmits(['open-row'])

const presets = [
  '统计各区域合同金额',
  '查询未收款合同清单',
  '按负责人统计签约业绩',
  '统计各月合同签约金额',
  '按客户统计合同金额前10名',
]

const question = ref('')
const loading = ref(false)
const error = ref('')
const result = ref(null)
let reqSeq = 0

function formatMoney(val, fallbackText) {
  if (val == null || val === '') return fallbackText || '—'
  return Number(val).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
}

function pickPreset(text) {
  question.value = text
  submit()
}

function clearResult() {
  reqSeq++
  loading.value = false
  error.value = ''
  result.value = null
}

async function submit() {
  const q = question.value.trim()
  if (!q || loading.value) return
  const seq = ++reqSeq
  loading.value = true
  error.value = ''
  try {
    const res = await api.post('/business-workbook/contracts/report-query', {
      question: q,
      year: props.year || '',
      start: props.start || '',
      end: props.end || '',
    }, { silent: true })
    if (seq === reqSeq) result.value = res
  } catch (e) {
    if (seq === reqSeq) error.value = e.message || '报表生成失败，请稍后重试'
  } finally {
    if (seq === reqSeq) loading.value = false
  }
}

async function copyAnswer() {
  if (!result.value?.answer) return
  try {
    await navigator.clipboard.writeText(result.value.answer)
    ElMessage.success('文字结论已复制')
  } catch {
    ElMessage.warning('当前环境无法直接访问剪贴板，请手动选中复制')
  }
}

function exportCsv() {
  if (!result.value?.rows?.length) return
  const cols = result.value.columns || []
  const escapeCell = v => {
    const s = v == null ? '' : String(v)
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const header = cols.map(c => escapeCell(c.label)).join(',')
  const body = result.value.rows.map(r => cols.map(c => escapeCell(r[c.prop] ?? r[c.prop + '_text'] ?? '')).join(',')).join('\r\n')
  const csv = '\uFEFF' + `# ${result.value.title}\r\n# ${result.value.answer}\r\n` + header + '\r\n' + body
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${result.value.title.replace(/[\\/:*?"<>|\s·]+/g, '_')}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.mb-16 { margin-bottom: 16px; }
.mt-12 { margin-top: 12px; }
.mt-8 { margin-top: 8px; }
.query-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.query-input { flex: 1 1 280px; min-width: 0; }
.query-bar > .el-button { margin-left: 0; flex-shrink: 0; }
.preset-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px; margin-top: 10px; }
.preset-chip {
  padding: 4px 10px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--jc-ink-2);
  background: var(--jc-bg-2, #f7f6f2);
  border: 1px solid var(--jc-line);
  border-radius: 999px;
  cursor: pointer;
  transition: border-color .15s, color .15s;
}
.preset-chip:hover:not(:disabled) { border-color: var(--jc-brand); color: var(--jc-brand); }
.preset-chip:disabled { opacity: 0.6; cursor: not-allowed; }
.report-output { border-top: 1px solid var(--jc-line); padding-top: 14px; }
.output-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.output-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.output-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.output-actions .el-button + .el-button { margin-left: 0; }
.answer-box {
  padding: 12px 14px;
  margin-bottom: 12px;
  border: 1px solid var(--jc-line);
  border-radius: 8px;
  background: var(--jc-bg-2, #f9f8f5);
}
.answer-text { margin: 0; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13.5px; color: var(--jc-ink); }
.row-link { max-width: 100%; overflow: hidden; text-overflow: ellipsis; text-align: left; }
@media (max-width: 560px) {
  .query-input { flex-basis: 100%; }
}
</style>
